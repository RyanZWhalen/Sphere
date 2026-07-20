"""Explain one authoritative repository→runtime verdict in plain language.

This is Sphere's local diagnosis layer.  It is intentionally deterministic: the
diagnoser consumes the exact ``requires`` edge produced by introspection and never
recomputes package status.  Its job is to turn those established facts into a small,
provider-neutral explanation and recommendation.  The existing command-plan compiler
remains the only component allowed to produce executable argv.

Diagnosis schema (diagnosis_schema_version = "1.0")
====================================================

{
  "diagnosis_schema_version": "1.0",
  "producer": "local-rules",
  "repository": {"id": "repository:<path>", "path": "/absolute/path"},
  "target": {
    "id": "environment/interpreter id",
    "label": "human-readable basename",
    "type": "environment | interpreter",
    "kind": "venv | conda | uv-project | null",
    "writable": true,
    "block_reason": null
  },
  "verdict": "satisfied | version-mismatch | missing",
  "headline": "short local diagnosis",
  "summary": "plain-language explanation and safe next step",
  "counts": {"total": 3, "satisfied": 1, "missing": 1, "version_mismatch": 1},
  "observations": [{
    "name": "six",
    "requirement": "six==1.16.0",
    "specifier": "==1.16.0",
    "source": "requirements.txt",
    "status": "version-mismatch",
    "installed_version": "1.15.0",
    "explanation": "six reported 1.15.0, which is outside ==1.16.0.",
    "proposed_action": "upgrade"
  }],
  "recommendation": {
    "kind": "none | repair-environment | create-project-environment",
    "summary": "what the guarded local agent can do",
    "step_count": 1,
    "requires_approval": true
  }
}
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from typing import Any

from packaging.utils import canonicalize_name

from sphere.fixplan import Plan, build_plan


DIAGNOSIS_SCHEMA_VERSION = "1.0"


def _find_node(topology: Mapping[str, Any], node_id: str) -> Mapping[str, Any] | None:
    for group in ("environments", "interpreters", "repositories", "contexts"):
        for node in topology.get("nodes", {}).get(group, []) or []:
            if node.get("id") == node_id:
                return node
    return None


def _requires_edge(
    topology: Mapping[str, Any], repository_id: str, target_id: str
) -> Mapping[str, Any] | None:
    """Return only the exact repository-origin edge for this exact target."""

    for edge in topology.get("edges", []) or []:
        if (
            edge.get("type") == "requires"
            and edge.get("from") == repository_id
            and edge.get("to") == target_id
        ):
            return edge
    return None


def _target_label(node: Mapping[str, Any], target_id: str) -> str:
    path = node.get("path") or node.get("interpreter_path") or ""
    return os.path.basename(str(path).rstrip(os.sep)) or target_id


def _required_text(item: Mapping[str, Any]) -> str:
    return str(item.get("specifier") or "any installed version")


def _step_actions(plan: Plan) -> dict[str, str]:
    return {canonicalize_name(step.package): step.action for step in plan.steps}


def _observation(item: Mapping[str, Any], actions: Mapping[str, str]) -> dict[str, Any]:
    name = str(item.get("name") or "unknown package")
    status = str(item.get("status") or "unknown")
    installed = item.get("installed_version")
    required = _required_text(item)

    if status == "missing":
        explanation = f"{name} is declared with {required}, but this target did not report it as installed."
    elif status == "version-mismatch":
        explanation = f"{name} reported {installed or 'an unknown version'}, which is outside {required}."
    elif status == "satisfied":
        explanation = f"{name} reported {installed or 'an installed version'}, satisfying {required}."
    else:
        explanation = f"Sphere has no package verdict for {name}."

    return {
        "name": name,
        "requirement": item.get("requirement") or item.get("raw") or name,
        "specifier": item.get("specifier") or "",
        "source": item.get("source") or "",
        "status": status,
        "installed_version": installed,
        "explanation": explanation,
        "proposed_action": actions.get(canonicalize_name(name), "none"),
    }


def _headline(label: str, counts: Mapping[str, int]) -> str:
    missing = counts["missing"]
    mismatch = counts["version_mismatch"]
    if counts["total"] == 0:
        return "No declared dependencies to evaluate."
    if missing == 0 and mismatch == 0:
        return f"{label} is ready for this repository."
    if missing and mismatch:
        return f"{label} has {missing + mismatch} dependency problems."
    if missing:
        noun = "package" if missing == 1 else "packages"
        return f"{label} is missing {missing} declared {noun}."
    noun = "package" if mismatch == 1 else "packages"
    return f"{label} has {mismatch} {noun} at the wrong version."


def _recommendation(plan: Plan, counts: Mapping[str, int]) -> tuple[str, str]:
    problems = counts["missing"] + counts["version_mismatch"]
    if problems == 0:
        return "none", "No repair is needed."
    if plan.target.writable:
        noun = "package action" if len(plan.steps) == 1 else "package actions"
        return (
            "repair-environment",
            f"The local repair agent can resolve this with {len(plan.steps)} defined {noun}, then re-scan the target to verify it.",
        )
    if plan.target.type == "interpreter":
        return (
            "create-project-environment",
            "Sphere will not modify this shared or bare interpreter. The safe fix is to create a project-local .venv and install the declarations there.",
        )
    return "none", plan.target.block_reason or "This target cannot be repaired safely."


def build_diagnosis(
    topology: Mapping[str, Any],
    repository_id: str,
    target_id: str,
    *,
    protected_prefixes: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Return a local explanation of one existing edge without changing its verdict."""

    repository = _find_node(topology, repository_id)
    if repository is None:
        raise ValueError(f"unknown repository: {repository_id!r}")
    target = _find_node(topology, target_id)
    if target is None:
        raise ValueError(f"unknown target: {target_id!r}")
    edge = _requires_edge(topology, repository_id, target_id)
    if edge is None:
        raise ValueError(f"no requires edge from {repository_id!r} to {target_id!r}")

    plan = build_plan(
        topology,
        repository_id,
        target_id,
        producer="local-rules",
        protected_prefixes=protected_prefixes,
    )
    diff = list(edge.get("diff", []) or [])
    counts = {
        "total": len(diff),
        "satisfied": sum(item.get("status") == "satisfied" for item in diff),
        "missing": sum(item.get("status") == "missing" for item in diff),
        "version_mismatch": sum(item.get("status") == "version-mismatch" for item in diff),
    }
    label = _target_label(target, target_id)
    recommendation_kind, recommendation_summary = _recommendation(plan, counts)
    recommended_step_count = len(plan.steps) if plan.target.writable else 0

    if counts["missing"] or counts["version_mismatch"]:
        summary = recommendation_summary
    elif counts["total"]:
        summary = f"All {counts['total']} declared requirements match the versions this target reported."
    else:
        summary = "Add requirements.txt or supported pyproject.toml declarations to compare this target."

    return {
        "diagnosis_schema_version": DIAGNOSIS_SCHEMA_VERSION,
        "producer": "local-rules",
        "repository": {"id": repository_id, "path": repository.get("path") or ""},
        "target": {
            "id": target_id,
            "label": label,
            "type": plan.target.type,
            "kind": plan.target.kind,
            "writable": plan.target.writable,
            "block_reason": plan.target.block_reason,
        },
        # This is copied verbatim from the authoritative edge. No verdict logic lives here.
        "verdict": edge.get("verdict"),
        "headline": _headline(label, counts),
        "summary": summary,
        "counts": counts,
        "observations": [_observation(item, _step_actions(plan)) for item in diff],
        "recommendation": {
            "kind": recommendation_kind,
            "summary": recommendation_summary,
            "step_count": recommended_step_count,
            "requires_approval": recommendation_kind != "none",
        },
    }
