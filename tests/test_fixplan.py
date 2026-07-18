"""Unit tests for the command-plan compiler. Pure: no network, no interpreters."""

from __future__ import annotations

import unittest

from sphere.fixplan import build_plan


REPOSITORY_ID = "repository:/proj"
ENV_ID = "environment:/proj/.venv-broken"
ENV_INTERPRETER = "/proj/.venv-broken/bin/python"
SYSTEM_INTERPRETER_ID = "interpreter:/usr/bin/python3"


def _diff_item(name, specifier, status, installed_version, source="requirements.txt"):
    return {
        "name": name,
        "requirement": f"{name}{specifier}",
        "specifier": specifier,
        "extras": [],
        "marker": None,
        "url": None,
        "source": source,
        "raw": f"{name}{specifier}",
        "installed_version": installed_version,
        "status": status,
    }


def _topology(diff, *, verdict="missing", target_id=ENV_ID, interpreter_path=ENV_INTERPRETER,
              group="environments", kind="venv", discovered_by=None):
    node = {"id": target_id, "path": interpreter_path}
    if group == "environments":
        node.update({"type": "environment", "kind": kind, "interpreter_path": interpreter_path})
    else:
        node.update({"type": "interpreter", "discovered_by": discovered_by or []})
    return {
        "nodes": {
            "repositories": [{"id": REPOSITORY_ID, "type": "repository", "path": "/proj"}],
            "environments": [node] if group == "environments" else [],
            "interpreters": [node] if group == "interpreters" else [],
            "contexts": [],
        },
        "edges": [
            {"type": "requires", "from": REPOSITORY_ID, "to": target_id, "verdict": verdict, "diff": diff},
        ],
    }


class BuildPlanTest(unittest.TestCase):
    def test_mixed_diff_produces_ordered_steps(self):
        diff = [
            _diff_item("six", "==1.16.0", "version-mismatch", "1.15.0"),
            _diff_item("idna", ">=3.0", "missing", None),
            _diff_item("typing-extensions", ">=4.0", "missing", None),
        ]
        plan = build_plan(_topology(diff), REPOSITORY_ID, ENV_ID, protected_prefixes=["/nowhere"])

        self.assertTrue(plan.target.writable)
        self.assertIsNone(plan.target.block_reason)
        self.assertEqual(plan.verdict_before, "missing")
        self.assertEqual([s.action for s in plan.steps], ["upgrade", "install", "install"])
        self.assertEqual([s.index for s in plan.steps], [0, 1, 2])
        # Command targets the environment's OWN interpreter, verbatim.
        self.assertEqual(
            plan.steps[0].command,
            [ENV_INTERPRETER, "-m", "pip", "install", "six==1.16.0"],
        )
        self.assertEqual(plan.steps[1].command[-1], "idna>=3.0")
        self.assertEqual(plan.steps[0].before, {"status": "version-mismatch", "installed_version": "1.15.0"})
        self.assertTrue(plan.id.startswith("plan:"))

    def test_satisfied_target_has_no_steps(self):
        diff = [_diff_item("six", "==1.16.0", "satisfied", "1.16.0")]
        plan = build_plan(_topology(diff, verdict="satisfied"), REPOSITORY_ID, ENV_ID,
                          protected_prefixes=["/nowhere"])
        self.assertEqual(plan.steps, [])
        self.assertEqual(plan.verdict_before, "satisfied")

    def test_downgrade_when_installed_exceeds_specifier(self):
        diff = [_diff_item("flask", "<2.0", "version-mismatch", "3.0.0")]
        plan = build_plan(_topology(diff), REPOSITORY_ID, ENV_ID, protected_prefixes=["/nowhere"])
        self.assertEqual(plan.steps[0].action, "downgrade")

    def test_protected_prefix_blocks_sphere_own_env(self):
        diff = [_diff_item("six", "==1.16.0", "missing", None)]
        plan = build_plan(_topology(diff), REPOSITORY_ID, ENV_ID,
                          protected_prefixes=["/proj/.venv-broken"])
        self.assertFalse(plan.target.writable)
        self.assertIn("Sphere's own environment", plan.target.block_reason)

    def test_shared_system_interpreter_is_blocked(self):
        diff = [_diff_item("six", "==1.16.0", "missing", None)]
        topology = _topology(
            diff, target_id=SYSTEM_INTERPRETER_ID, interpreter_path="/usr/bin/python3",
            group="interpreters", kind=None, discovered_by=[{"source": "system-location"}],
        )
        plan = build_plan(topology, REPOSITORY_ID, SYSTEM_INTERPRETER_ID, protected_prefixes=["/nowhere"])
        self.assertFalse(plan.target.writable)
        self.assertEqual(plan.target.type, "interpreter")
        self.assertIn("system", plan.target.block_reason)

    def test_fingerprint_is_stable_and_command_sensitive(self):
        diff_a = [_diff_item("six", "==1.16.0", "version-mismatch", "1.15.0")]
        diff_b = [_diff_item("six", "==1.17.0", "version-mismatch", "1.15.0")]
        first = build_plan(_topology(diff_a), REPOSITORY_ID, ENV_ID, protected_prefixes=["/nowhere"])
        again = build_plan(_topology(diff_a), REPOSITORY_ID, ENV_ID, protected_prefixes=["/nowhere"])
        different = build_plan(_topology(diff_b), REPOSITORY_ID, ENV_ID, protected_prefixes=["/nowhere"])
        self.assertEqual(first.fingerprint, again.fingerprint)
        self.assertNotEqual(first.fingerprint, different.fingerprint)

    def test_unknown_target_raises(self):
        diff = [_diff_item("six", "==1.16.0", "missing", None)]
        with self.assertRaises(ValueError):
            build_plan(_topology(diff), REPOSITORY_ID, "environment:/nope", protected_prefixes=["/nowhere"])


if __name__ == "__main__":
    unittest.main()
