"""Compile a repository→runtime diff into an ordered, executable command plan.

This module is the intermediate representation ("command-plan IR") that sits
between a ``requires`` edge diff (produced by :mod:`sphere.introspect`) and the
pip commands executed by :mod:`sphere.apply`.  It is deliberately pure and imports
only the standard library plus ``packaging``, so it stays inside Sphere's
core-dependency constraint and is trivially testable without a network or a real
interpreter.

The same :class:`Plan` shape is intended to be produced later by the GPT diagnosis
layer and by drag-and-drop, then executed and receipted through one guarded path.
That is why the exact command ``argv`` lives inside each step, why every plan
carries a ``producer`` tag and a ``fingerprint``, and why a target that must not be
mutated is expressed as ``writable = False`` rather than simply omitted: the IR is
the contract three different producers share.

Plan schema (plan_schema_version = "1.0")
=========================================

{
  "plan_schema_version": "1.0",
  "id": "plan:<stable short hash>",
  "producer": "diff-compiler | gpt | manual-dnd",
  "generated_at": "ISO-8601 UTC timestamp",
  "repository": {"id": "repository id", "path": "canonical repository path"},
  "target": {
    "id": "environment or interpreter id",
    "type": "environment | interpreter",
    "kind": "venv | uv-project | conda | null",
    "path": "canonical environment directory when the target is an environment, or null",
    "interpreter_path": "the executable pip runs against, or null",
    "writable": "boolean; false means execution is refused",
    "block_reason": "why the target is not writable, or null"
  },
  "verdict_before": "satisfied | version-mismatch | missing",
  "verdict_after": "same, filled by the post-execution re-scan (null until then)",
  "steps": [{
    "index": 0,
    "action": "install | upgrade | downgrade | uninstall | create-venv | remove-venv",
    "package": "PEP 503 canonicalized name",
    "specifier": "declared PEP 440 specifier ('' means any version)",
    "requirement": "full serialized PEP 508 requirement",
    "source": "which declaration produced this step",
    "command": ["executable", "-m", "pip", "install", "name==x"],
    "before": {"status": "missing | version-mismatch", "installed_version": "or null"},
    "expected_after_status": "satisfied",
    "receipt": "null until sphere.apply fills it"
  }],
  "fingerprint": "hash binding the target interpreter to the ordered commands"
}
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version


PLAN_SCHEMA_VERSION = "1.0"

# Interpreter discovery sources that mark a shared runtime Sphere must never mutate.
# Mirrors the discovery sources emitted by sphere.introspect.
_SHARED_RUNTIME_SOURCES: dict[str, str] = {
    "system-location": "system",
    "macos-framework": "macOS framework",
    "homebrew": "Homebrew",
    "pyenv": "pyenv-managed",
    "uv-managed": "uv-managed",
}


def _canonical(path: str) -> str:
    return os.path.realpath(os.path.abspath(os.path.expanduser(path)))


def _is_probably_executable(path: str | None) -> bool:
    return bool(path) and os.path.isfile(path) and os.access(path, os.X_OK)


@dataclass
class Step:
    """One package operation: exactly one exact command with one before/after."""

    index: int
    action: str
    package: str
    specifier: str
    requirement: str
    source: str
    command: list[str]
    before: dict[str, Any]
    expected_after_status: str = "satisfied"
    receipt: dict[str, Any] | None = None

    def as_json(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "action": self.action,
            "package": self.package,
            "specifier": self.specifier,
            "requirement": self.requirement,
            "source": self.source,
            "command": list(self.command),
            "before": dict(self.before),
            "expected_after_status": self.expected_after_status,
            "receipt": self.receipt,
        }


@dataclass
class PlanTarget:
    """The runtime a plan acts on, and whether Sphere is permitted to mutate it."""

    id: str
    type: str
    kind: str | None
    path: str | None
    interpreter_path: str | None
    writable: bool
    block_reason: str | None

    def as_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "kind": self.kind,
            "path": self.path,
            "interpreter_path": self.interpreter_path,
            "writable": self.writable,
            "block_reason": self.block_reason,
        }


@dataclass
class Plan:
    """An ordered, previewable, executable command plan for one target."""

    id: str
    producer: str
    generated_at: str
    repository: dict[str, str]
    target: PlanTarget
    verdict_before: str
    steps: list[Step]
    fingerprint: str
    plan_schema_version: str = PLAN_SCHEMA_VERSION
    verdict_after: str | None = None

    def as_json(self) -> dict[str, Any]:
        return {
            "plan_schema_version": self.plan_schema_version,
            "id": self.id,
            "producer": self.producer,
            "generated_at": self.generated_at,
            "repository": dict(self.repository),
            "target": self.target.as_json(),
            "verdict_before": self.verdict_before,
            "verdict_after": self.verdict_after,
            "steps": [step.as_json() for step in self.steps],
            "fingerprint": self.fingerprint,
        }


def _find_node(topology: Mapping[str, Any], node_id: str) -> tuple[dict[str, Any] | None, str | None]:
    groups = topology.get("nodes", {})
    for group in ("environments", "interpreters", "repositories", "contexts"):
        for node in groups.get(group, []) or []:
            if node.get("id") == node_id:
                return node, group
    return None, None


def _requires_edge(
    topology: Mapping[str, Any], repository_id: str, target_id: str
) -> Mapping[str, Any] | None:
    for edge in topology.get("edges", []) or []:
        if (
            edge.get("type") == "requires"
            and edge.get("from") == repository_id
            and edge.get("to") == target_id
        ):
            return edge
    return None


def _pip_argument(item: Mapping[str, Any]) -> str:
    """Reconstruct the pip requirement argument from one diff item.

    Markers are intentionally dropped: a step is only created for a requirement we
    intend to install, so the command names the distribution and its version range.
    """

    name = item.get("name") or ""
    url = item.get("url")
    if url:
        return f"{name} @ {url}"
    extras = item.get("extras") or []
    specifier = item.get("specifier") or ""
    base = f"{name}[{','.join(extras)}]" if extras else name
    return f"{base}{specifier}"


def _classify_action(status: str, installed_version: str | None, specifier: str) -> str:
    """Label a step for the receipt narrative; the pip command is identical either way."""

    if status != "version-mismatch":
        return "install"
    if not installed_version:
        return "upgrade"
    try:
        installed = Version(installed_version)
    except InvalidVersion:
        return "upgrade"
    anchors: list[Version] = []
    try:
        for specifier_clause in SpecifierSet(specifier or ""):
            try:
                anchors.append(Version(specifier_clause.version.replace(".*", "")))
            except InvalidVersion:
                continue
    except InvalidSpecifier:
        return "upgrade"
    # Every version the specifier anchors on is below what is installed → step down.
    if anchors and all(anchor < installed for anchor in anchors):
        return "downgrade"
    return "upgrade"


def _classify_target(
    node: Mapping[str, Any],
    group: str,
    interpreter_path: str | None,
    protected_prefixes: Sequence[str],
) -> tuple[bool, str | None]:
    """Decide whether Sphere may run pip against this target, and explain refusals."""

    if not interpreter_path:
        return False, "No interpreter is associated with this environment."

    canonical = _canonical(interpreter_path)
    for prefix in protected_prefixes:
        if canonical == prefix or canonical.startswith(prefix + os.sep):
            return False, "This is Sphere's own environment; refusing to modify the tool's runtime."

    if group == "interpreters":
        sources = {entry.get("source") for entry in node.get("discovered_by", []) or []}
        for source, label in _SHARED_RUNTIME_SOURCES.items():
            if source in sources:
                return (
                    False,
                    f"Fixing a shared {label} interpreter would pollute a runtime other projects rely on. "
                    "Select an environment, or create one for this folder.",
                )
        return False, "This is a bare interpreter, not an environment. Select or create an environment to fix."

    return True, None


def _fingerprint(interpreter_path: str | None, steps: Iterable[Step]) -> str:
    payload = json.dumps(
        {"interpreter": interpreter_path, "commands": [list(step.command) for step in steps]},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_plan(
    topology: Mapping[str, Any],
    repository_id: str,
    target_id: str,
    *,
    producer: str = "diff-compiler",
    protected_prefixes: Sequence[str] | None = None,
    generated_at: str | None = None,
) -> Plan:
    """Compile the repository→target ``requires`` diff into an ordered command plan.

    The plan reuses the diff already computed during ``scan_topology`` rather than
    re-deriving it, so the preview reflects exactly the verdicts the graph showed.
    A target that must not be mutated (Sphere's own venv, a shared system/Homebrew/
    pyenv/uv interpreter, or a bare interpreter) is returned with ``writable=False``
    and a ``block_reason`` instead of runnable steps.
    """

    if protected_prefixes is None:
        protected_prefixes = (_canonical(sys.prefix),)
    else:
        protected_prefixes = tuple(_canonical(prefix) for prefix in protected_prefixes)

    repository_node, _ = _find_node(topology, repository_id)
    if repository_node is None:
        raise ValueError(f"unknown repository: {repository_id!r}")
    target_node, group = _find_node(topology, target_id)
    if target_node is None:
        raise ValueError(f"unknown target: {target_id!r}")

    interpreter_path = (
        target_node.get("interpreter_path") if group == "environments" else target_node.get("path")
    )
    writable, block_reason = _classify_target(target_node, group, interpreter_path, protected_prefixes)

    edge = _requires_edge(topology, repository_id, target_id)
    diff_items = list(edge.get("diff", [])) if edge else []
    verdict_before = edge.get("verdict", "satisfied") if edge else "satisfied"

    steps: list[Step] = []
    for item in diff_items:
        status = item.get("status")
        if status == "satisfied" or status is None:
            continue
        argument = _pip_argument(item)
        steps.append(
            Step(
                index=len(steps),
                action=_classify_action(status, item.get("installed_version"), item.get("specifier") or ""),
                package=canonicalize_name(item.get("name") or ""),
                specifier=item.get("specifier") or "",
                requirement=item.get("requirement") or argument,
                source=item.get("source") or "",
                command=[interpreter_path or "python", "-m", "pip", "install", argument],
                before={"status": status, "installed_version": item.get("installed_version")},
            )
        )

    fingerprint = _fingerprint(interpreter_path, steps)
    return Plan(
        id=f"plan:{fingerprint[:12]}",
        producer=producer,
        generated_at=generated_at or datetime.now(UTC).isoformat(),
        repository={"id": repository_node["id"], "path": repository_node.get("path", "")},
        target=PlanTarget(
            id=target_id,
            type="environment" if group == "environments" else "interpreter",
            kind=target_node.get("kind"),
            path=target_node.get("path"),
            interpreter_path=interpreter_path,
            writable=writable,
            block_reason=block_reason,
        ),
        verdict_before=verdict_before,
        steps=steps,
        fingerprint=fingerprint,
    )


def _resolved_interpreter_id(topology: Mapping[str, Any]) -> str | None:
    for edge in topology.get("edges", []) or []:
        if edge.get("type") == "resolves-to":
            return edge.get("to")
    return None


def build_create_venv_plan(
    topology: Mapping[str, Any],
    repository_id: str,
    *,
    base_interpreter_id: str | None = None,
    venv_dirname: str = ".venv",
    producer: str = "diff-compiler",
    protected_prefixes: Sequence[str] | None = None,
    generated_at: str | None = None,
) -> Plan:
    """Compile a plan that creates a fresh venv for a folder, then fills it.

    This is the honest resolution for the "folder resolves to a shared interpreter"
    case (the documented ``create-venv`` action the IR already permits): instead of
    polluting that interpreter, create a project-local venv *from* it and install the
    declared requirements into the new environment. Step 0 is a ``create-venv`` action
    whose command is ``<base> -m venv <folder>/.venv``; the remaining steps are
    ordinary installs targeting the interpreter that step will create.
    """

    if protected_prefixes is None:
        protected_prefixes = (_canonical(sys.prefix),)
    else:
        protected_prefixes = tuple(_canonical(prefix) for prefix in protected_prefixes)

    repository_node, _ = _find_node(topology, repository_id)
    if repository_node is None:
        raise ValueError(f"unknown repository: {repository_id!r}")
    folder = repository_node.get("path") or ""
    venv_path = _canonical(os.path.join(folder, venv_dirname))
    venv_python = os.path.join(venv_path, "bin", "python")

    base_id = base_interpreter_id or _resolved_interpreter_id(topology)
    base_node, _ = _find_node(topology, base_id) if base_id else (None, None)
    base_path = base_node.get("path") if base_node else None

    writable, block_reason = True, None
    if not _is_probably_executable(base_path):
        writable, block_reason = False, "No base interpreter is available to create a virtual environment."
    else:
        canonical_python = _canonical(venv_python)
        for prefix in protected_prefixes:
            if canonical_python == prefix or canonical_python.startswith(prefix + os.sep):
                writable, block_reason = False, "This is Sphere's own environment; refusing to modify the tool's runtime."

    steps: list[Step] = [
        Step(
            index=0,
            action="create-venv",
            package=venv_dirname,
            specifier="",
            requirement=f"venv @ {venv_path}",
            source="sphere:create-venv",
            command=[base_path or "python", "-m", "venv", venv_path],
            before={"status": "absent", "installed_version": None},
            expected_after_status="created",
        )
    ]
    for item in repository_node.get("requirements", []) or []:
        argument = _pip_argument(item)
        steps.append(
            Step(
                index=len(steps),
                action="install",
                package=canonicalize_name(item.get("name") or ""),
                specifier=item.get("specifier") or "",
                requirement=item.get("requirement") or argument,
                source=item.get("source") or "",
                command=[venv_python, "-m", "pip", "install", argument],
                before={"status": "missing", "installed_version": None},
            )
        )

    fingerprint = _fingerprint(venv_python, steps)
    return Plan(
        id=f"plan:{fingerprint[:12]}",
        producer=producer,
        generated_at=generated_at or datetime.now(UTC).isoformat(),
        repository={"id": repository_node["id"], "path": folder},
        target=PlanTarget(
            id=f"environment:{venv_path}",
            type="environment",
            kind="venv",
            path=venv_path,
            interpreter_path=venv_python,
            writable=writable,
            block_reason=block_reason,
        ),
        verdict_before="missing",
        steps=steps,
        fingerprint=fingerprint,
    )


def _is_direct_child(path: str, parent: str) -> bool:
    """Return whether ``path`` is immediately inside ``parent`` after canonicalization."""

    try:
        return os.path.dirname(_canonical(path)) == _canonical(parent)
    except (OSError, ValueError):
        return False


def _classify_removal_target(
    node: Mapping[str, Any],
    group: str | None,
    repository_path: str,
    protected_prefixes: Sequence[str],
) -> tuple[bool, str | None]:
    """Allow removal only for a project-local, non-tool-owned virtual environment."""

    environment_path = node.get("path")
    if group != "environments" or node.get("kind") != "venv":
        return False, "Only a project-local Python virtual environment can be removed."
    if not isinstance(environment_path, str) or not _is_direct_child(environment_path, repository_path):
        return False, "Only a virtual environment directly inside this repository can be removed."

    canonical_environment = _canonical(environment_path)
    for prefix in protected_prefixes:
        if prefix == canonical_environment or prefix.startswith(canonical_environment + os.sep):
            return False, "This is Sphere's own environment; refusing to remove the tool's runtime."
    return True, None


def build_remove_venv_plan(
    topology: Mapping[str, Any],
    repository_id: str,
    target_id: str,
    *,
    producer: str = "diff-compiler",
    protected_prefixes: Sequence[str] | None = None,
    generated_at: str | None = None,
) -> Plan:
    """Compile a guarded plan to remove one project-local virtual environment.

    Removal is intentionally narrower than package repair: it is offered only for
    a ``venv`` directory that is an immediate child of the repository selected by
    the scan.  The plan still requires a preview and fingerprint-matched approval
    before :func:`sphere.apply.execute_plan` performs the deletion.
    """

    if protected_prefixes is None:
        protected_prefixes = (_canonical(sys.prefix),)
    else:
        protected_prefixes = tuple(_canonical(prefix) for prefix in protected_prefixes)

    repository_node, _ = _find_node(topology, repository_id)
    if repository_node is None:
        raise ValueError(f"unknown repository: {repository_id!r}")
    target_node, group = _find_node(topology, target_id)
    if target_node is None:
        raise ValueError(f"unknown target: {target_id!r}")

    repository_path = repository_node.get("path") or ""
    environment_path = target_node.get("path") or ""
    writable, block_reason = _classify_removal_target(
        target_node, group, repository_path, protected_prefixes
    )
    canonical_environment = _canonical(environment_path) if environment_path else None
    interpreter_path = target_node.get("interpreter_path")
    edge = _requires_edge(topology, repository_id, target_id)
    verdict_before = edge.get("verdict", "unknown") if edge else "unknown"
    steps = [
        Step(
            index=0,
            action="remove-venv",
            package=os.path.basename(canonical_environment or environment_path),
            specifier="",
            requirement=f"venv @ {canonical_environment or environment_path}",
            source="sphere:remove-venv",
            command=["sphere", "remove-venv", canonical_environment or environment_path],
            before={"status": "present", "installed_version": None},
            expected_after_status="removed",
        )
    ]
    fingerprint = _fingerprint(interpreter_path, steps)
    return Plan(
        id=f"plan:{fingerprint[:12]}",
        producer=producer,
        generated_at=generated_at or datetime.now(UTC).isoformat(),
        repository={"id": repository_node["id"], "path": repository_path},
        target=PlanTarget(
            id=target_id,
            type="environment" if group == "environments" else "interpreter",
            kind=target_node.get("kind"),
            path=canonical_environment,
            interpreter_path=interpreter_path,
            writable=writable,
            block_reason=block_reason,
        ),
        verdict_before=verdict_before,
        steps=steps,
        fingerprint=fingerprint,
    )
