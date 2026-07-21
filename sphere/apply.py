"""Execute an approved command-plan and prove the result by re-scanning.

This is the only write surface in Sphere.  It runs each package step's exact
``argv`` against the target environment's *own* interpreter
(``[interpreter_path, "-m", "pip", ...]``), which is the write-side mirror of
the read-side isolation invariant: installs can only ever land in the target the
plan named, never in Sphere's own process or ``.sphere-venv``. A separately
guarded ``remove-venv`` step can remove only the project-local venv that its
fingerprint-matched plan named.

``execute_plan`` is a generator yielding one event dict per stage, so the server
can stream per-action receipts as they happen and tests can assert the event
sequence without a network.  The subprocess runner, package re-query, and re-scan
are injectable for exactly that reason; the defaults perform the real work.

Every stage is honest: a non-zero pip exit, a timeout, or a missing pip is
captured into the receipt (``exit_status`` / ``stderr_tail`` / ``error``) and the
loop stops rather than pretending the fix landed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from typing import Any

from packaging.utils import canonicalize_name

from sphere.introspect import (
    WarningLog,
    _package_record,
    _sanitized_environment,
    scan_topology,
)
from sphere.fixplan import Plan
from sphere.requirements import DeclaredRequirement, diff_requirements


STEP_TIMEOUT_SECONDS = 180
_OUTPUT_TAIL_CHARS = 4000

# A runner takes a command argv and returns the raw subprocess outcome fields.
CommandRunner = Callable[[Sequence[str]], dict[str, Any]]
# A package query re-reads one target's installed distributions as a package record.
PackageQuery = Callable[[str, str | None], dict[str, Any]]
# A re-scan returns a fresh full topology.
Rescan = Callable[[], dict[str, Any]]
VenvRemover = Callable[[str | None], dict[str, Any]]


def _tail(text: str | None) -> str:
    if not text:
        return ""
    trimmed = text.strip()
    if len(trimmed) <= _OUTPUT_TAIL_CHARS:
        return trimmed
    return trimmed[-_OUTPUT_TAIL_CHARS:]


def _run_command(command: Sequence[str]) -> dict[str, Any]:
    """Run one pip command in a sanitized environment, capturing everything."""

    start = time.monotonic()
    try:
        completed = subprocess.run(
            list(command),
            env=_sanitized_environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=STEP_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return {
            "exit_status": None,
            "stdout_tail": "",
            "stderr_tail": "",
            "duration_ms": _elapsed_ms(start),
            "error": f"command timed out after {STEP_TIMEOUT_SECONDS}s",
        }
    except OSError as error:
        return {
            "exit_status": None,
            "stdout_tail": "",
            "stderr_tail": "",
            "duration_ms": _elapsed_ms(start),
            "error": str(error),
        }
    return {
        "exit_status": completed.returncode,
        "stdout_tail": _tail(completed.stdout),
        "stderr_tail": _tail(completed.stderr),
        "duration_ms": _elapsed_ms(start),
        "error": None if completed.returncode == 0 else f"exit status {completed.returncode}",
    }


def _remove_venv(environment_path: str | None) -> dict[str, Any]:
    """Delete one real venv directory after the planner has narrowed the target.

    This deliberately does not invoke a shell or accept glob patterns.  The plan
    compiler restricts the path to a direct child of the repository; this final
    check confirms it is still a genuine venv before deleting it.
    """

    start = time.monotonic()
    if not environment_path:
        return {
            "exit_status": None,
            "stdout_tail": "",
            "stderr_tail": "",
            "duration_ms": _elapsed_ms(start),
            "error": "environment path is missing",
        }
    if os.path.islink(environment_path):
        return {
            "exit_status": None,
            "stdout_tail": "",
            "stderr_tail": "",
            "duration_ms": _elapsed_ms(start),
            "error": "refusing to remove a symlinked environment",
        }
    config_path = os.path.join(environment_path, "pyvenv.cfg")
    if not os.path.isdir(environment_path) or not os.path.isfile(config_path):
        return {
            "exit_status": None,
            "stdout_tail": "",
            "stderr_tail": "",
            "duration_ms": _elapsed_ms(start),
            "error": "refusing to remove a path that is not a virtual environment",
        }
    try:
        shutil.rmtree(environment_path)
    except OSError as error:
        return {
            "exit_status": None,
            "stdout_tail": "",
            "stderr_tail": "",
            "duration_ms": _elapsed_ms(start),
            "error": str(error),
        }
    return {
        "exit_status": 0,
        "stdout_tail": f"Removed {environment_path}",
        "stderr_tail": "",
        "duration_ms": _elapsed_ms(start),
        "error": None,
    }


def _elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


def _query_packages(context_id: str, interpreter_path: str | None) -> dict[str, Any]:
    """Re-read the target interpreter's installed distributions in isolation.

    Reuses the exact same isolated ``-I`` subprocess query the main scan uses, so a
    receipt's "after" is produced by the identical mechanism as the graph verdict.
    """

    return _package_record(context_id, interpreter_path, WarningLog())


def _match_requirement(diff: dict[str, Any], package: str) -> dict[str, Any] | None:
    target = canonicalize_name(package)
    for item in diff.get("requirements", []):
        if canonicalize_name(item.get("name") or "") == target:
            return item
    return None


def _verdict_after(topology: dict[str, Any], repository_id: str, target_id: str) -> str | None:
    for edge in topology.get("edges", []) or []:
        if (
            edge.get("type") == "requires"
            and edge.get("from") == repository_id
            and edge.get("to") == target_id
        ):
            return edge.get("verdict")
    return None


def _target_present(topology: dict[str, Any], target_id: str) -> bool:
    """Check whether a target remained in the post-action topology."""

    for nodes in (topology.get("nodes", {}) or {}).values():
        if any(node.get("id") == target_id for node in nodes or []):
            return True
    return False


def execute_plan(
    plan: Plan,
    requirements: Iterable[DeclaredRequirement],
    *,
    runner: CommandRunner = _run_command,
    remover: VenvRemover = _remove_venv,
    package_query: PackageQuery = _query_packages,
    rescan: Rescan | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield events while executing an approved plan against its target.

    Event stream (NDJSON when served):
      {"event": "plan", "plan": ...}          — the plan about to run
      {"event": "blocked", "reason": ..., "plan": ...} — target is not writable; nothing ran
      {"event": "receipt", "index": i, "step": ...}    — one action finished (per step)
      {"event": "done", "failed": bool, "plan": ..., "topology": ...} — final, re-scanned
    """

    yield {"event": "plan", "plan": plan.as_json()}

    if not plan.target.writable:
        yield {"event": "blocked", "reason": plan.target.block_reason, "plan": plan.as_json()}
        return

    requirements = list(requirements)
    interpreter_path = plan.target.interpreter_path
    failed = False

    for step in plan.steps:
        outcome = remover(plan.target.path) if step.action == "remove-venv" else runner(step.command)
        after: dict[str, Any] = {
            "interpreter_path": interpreter_path,
            "installed_version": None,
            "status": None,
        }
        if outcome.get("exit_status") == 0:
            if step.action == "create-venv":
                # Creating an env has no package verdict; prove the interpreter now exists.
                after["status"] = "created" if (interpreter_path and os.path.exists(interpreter_path)) else "failed"
            elif step.action == "remove-venv":
                after["status"] = "removed" if not os.path.lexists(plan.target.path or "") else "failed"
            else:
                diff = diff_requirements(requirements, package_query(plan.target.id, interpreter_path))
                match = _match_requirement(diff, step.package)
                if match is not None:
                    after["installed_version"] = match.get("installed_version")
                    after["status"] = match.get("status")
        step.receipt = {
            "command": list(step.command),
            "exit_status": outcome.get("exit_status"),
            "duration_ms": outcome.get("duration_ms"),
            "stdout_tail": outcome.get("stdout_tail", ""),
            "stderr_tail": outcome.get("stderr_tail", ""),
            "after": after,
            "error": outcome.get("error"),
        }
        yield {"event": "receipt", "index": step.index, "step": step.as_json()}
        if outcome.get("error"):
            failed = True
            break

    topology = rescan() if rescan is not None else None
    if topology is not None:
        if any(step.action == "remove-venv" for step in plan.steps):
            plan.verdict_after = "present" if _target_present(topology, plan.target.id) else "removed"
        else:
            plan.verdict_after = _verdict_after(topology, plan.repository["id"], plan.target.id)
    yield {"event": "done", "failed": failed, "plan": plan.as_json(), "topology": topology}


def default_rescan(directory: str | None, search_roots: Sequence[str]) -> Rescan:
    """Build a re-scan callable bound to the same directory/roots the plan used."""

    def _rescan() -> dict[str, Any]:
        return scan_topology(directory=directory, search_roots=list(search_roots))

    return _rescan
