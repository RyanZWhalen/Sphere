"""Unit tests for the command-plan compiler. Pure: no network, no interpreters."""

from __future__ import annotations

import sys
import unittest

from sphere.fixplan import build_create_venv_plan, build_plan, build_remove_venv_plan


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
              target_path=None, group="environments", kind="venv", discovered_by=None):
    node = {"id": target_id, "path": target_path or interpreter_path}
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


def _create_venv_topology():
    def requirement(name, specifier):
        return {
            "name": name, "requirement": f"{name}{specifier}", "specifier": specifier,
            "extras": [], "marker": None, "url": None, "source": "requirements.txt", "raw": f"{name}{specifier}",
        }

    return {
        "nodes": {
            "repositories": [{
                "id": REPOSITORY_ID, "type": "repository", "path": "/proj",
                "requirements": [requirement("six", "==1.16.0"), requirement("idna", ">=3.0")],
            }],
            "environments": [],
            "interpreters": [{
                "id": "interpreter:base", "type": "interpreter", "path": sys.executable,
                "discovered_by": [{"source": "system-location"}],
            }],
            "contexts": [{"id": "context:/proj", "type": "directory-context", "path": "/proj"}],
        },
        "edges": [{"type": "resolves-to", "from": "context:/proj", "to": "interpreter:base", "command": "python"}],
    }


class BuildCreateVenvPlanTest(unittest.TestCase):
    def test_plan_creates_a_venv_then_installs_into_it(self):
        plan = build_create_venv_plan(_create_venv_topology(), REPOSITORY_ID, protected_prefixes=["/nowhere"])
        self.assertTrue(plan.target.writable)
        self.assertEqual(plan.target.type, "environment")
        self.assertEqual(plan.target.kind, "venv")
        self.assertTrue(plan.target.id.endswith("/.venv"))
        self.assertEqual([s.action for s in plan.steps], ["create-venv", "install", "install"])
        # Step 0 builds the venv from the folder's resolved base interpreter.
        self.assertEqual(plan.steps[0].command[:3], [sys.executable, "-m", "venv"])
        self.assertTrue(plan.steps[0].command[3].endswith("/.venv"))
        # Install steps target the venv's OWN (not-yet-existent) interpreter.
        self.assertEqual(plan.steps[1].command[0], plan.target.interpreter_path)
        self.assertIn("six==1.16.0", plan.steps[1].command)

    def test_plan_blocked_when_new_venv_would_be_inside_sphere(self):
        plan = build_create_venv_plan(_create_venv_topology(), REPOSITORY_ID, protected_prefixes=["/proj/.venv"])
        self.assertFalse(plan.target.writable)
        self.assertIn("Sphere's own environment", plan.target.block_reason)


class BuildRemoveVenvPlanTest(unittest.TestCase):
    def _project_venv_topology(self, path="/proj/.venv"):
        return _topology(
            [],
            verdict="satisfied",
            target_id=f"environment:{path}",
            interpreter_path=f"{path}/bin/python",
            target_path=path,
        )

    def test_project_local_venv_gets_a_removal_plan(self):
        target_id = "environment:/proj/.venv"
        plan = build_remove_venv_plan(
            self._project_venv_topology(), REPOSITORY_ID, target_id, protected_prefixes=["/nowhere"]
        )

        self.assertTrue(plan.target.writable)
        self.assertEqual(plan.target.path, "/proj/.venv")
        self.assertEqual([step.action for step in plan.steps], ["remove-venv"])
        self.assertEqual(plan.steps[0].command, ["sphere", "remove-venv", "/proj/.venv"])
        self.assertEqual(plan.steps[0].expected_after_status, "removed")

    def test_removal_rejects_a_venv_outside_the_repository(self):
        target_id = "environment:/demo/.venv-good"
        plan = build_remove_venv_plan(
            self._project_venv_topology("/demo/.venv-good"), REPOSITORY_ID, target_id,
            protected_prefixes=["/nowhere"],
        )

        self.assertFalse(plan.target.writable)
        self.assertIn("directly inside this repository", plan.target.block_reason)

    def test_removal_rejects_spheres_own_environment(self):
        target_id = "environment:/proj/.venv"
        plan = build_remove_venv_plan(
            self._project_venv_topology(), REPOSITORY_ID, target_id, protected_prefixes=["/proj/.venv"]
        )

        self.assertFalse(plan.target.writable)
        self.assertIn("Sphere's own environment", plan.target.block_reason)


if __name__ == "__main__":
    unittest.main()
