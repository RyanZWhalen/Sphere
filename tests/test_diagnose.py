"""Tests for the deterministic local diagnosis layer."""

from __future__ import annotations

import unittest

from sphere.diagnose import build_diagnosis


REPOSITORY_ID = "repository:/project"
ENVIRONMENT_ID = "environment:/project/.venv"
INTERPRETER_ID = "interpreter:/framework/python3.14"


def _item(name, specifier, status, installed_version):
    return {
        "name": name,
        "requirement": f"{name}{specifier}",
        "specifier": specifier,
        "source": "requirements.txt",
        "raw": f"{name}{specifier}",
        "installed_version": installed_version,
        "status": status,
    }


def _topology(diff, *, verdict="missing", target_id=ENVIRONMENT_ID, interpreter=False):
    target = {
        "id": target_id,
        "type": "interpreter" if interpreter else "environment",
        "path": "/framework/python3.14" if interpreter else "/project/.venv",
    }
    if interpreter:
        target["discovered_by"] = [{"source": "macos-framework"}]
    else:
        target.update({"kind": "venv", "interpreter_path": "/project/.venv/bin/python"})
    return {
        "nodes": {
            "repositories": [{"id": REPOSITORY_ID, "type": "repository", "path": "/project"}],
            "environments": [] if interpreter else [target],
            "interpreters": [target] if interpreter else [],
            "contexts": [],
        },
        "edges": [
            {
                "type": "requires",
                "from": "repository:/decoy",
                "to": target_id,
                "verdict": "satisfied",
                "diff": [_item("six", "==1.16.0", "satisfied", "1.16.0")],
            },
            {
                "type": "requires",
                "from": REPOSITORY_ID,
                "to": target_id,
                "verdict": verdict,
                "diff": diff,
            },
        ],
    }


class BuildDiagnosisTest(unittest.TestCase):
    def test_mixed_edge_is_explained_without_recomputing_its_verdict(self):
        diagnosis = build_diagnosis(
            _topology(
                [
                    _item("six", "==1.16.0", "version-mismatch", "1.15.0"),
                    _item("idna", ">=3.0", "missing", None),
                    _item("typing-extensions", ">=4.0", "satisfied", "4.16.0"),
                ],
                verdict="missing",
            ),
            REPOSITORY_ID,
            ENVIRONMENT_ID,
            protected_prefixes=["/nowhere"],
        )

        self.assertEqual(diagnosis["producer"], "local-rules")
        self.assertEqual(diagnosis["verdict"], "missing")
        self.assertEqual(
            diagnosis["counts"],
            {"total": 3, "satisfied": 1, "missing": 1, "version_mismatch": 1},
        )
        self.assertEqual(diagnosis["recommendation"]["kind"], "repair-environment")
        self.assertEqual(diagnosis["recommendation"]["step_count"], 2)
        self.assertIn("reported 1.15.0", diagnosis["observations"][0]["explanation"])
        self.assertIn("did not report", diagnosis["observations"][1]["explanation"])
        self.assertEqual(diagnosis["observations"][2]["proposed_action"], "none")

    def test_shared_interpreter_recommends_a_project_environment(self):
        diagnosis = build_diagnosis(
            _topology(
                [_item("idna", ">=3.0", "missing", None)],
                target_id=INTERPRETER_ID,
                interpreter=True,
            ),
            REPOSITORY_ID,
            INTERPRETER_ID,
            protected_prefixes=["/nowhere"],
        )

        self.assertFalse(diagnosis["target"]["writable"])
        self.assertEqual(diagnosis["recommendation"]["kind"], "create-project-environment")
        self.assertEqual(diagnosis["recommendation"]["step_count"], 0)
        self.assertTrue(diagnosis["recommendation"]["requires_approval"])
        self.assertIn("project-local .venv", diagnosis["summary"])

    def test_satisfied_target_needs_no_repair(self):
        diagnosis = build_diagnosis(
            _topology(
                [_item("idna", ">=3.0", "satisfied", "3.11")],
                verdict="satisfied",
            ),
            REPOSITORY_ID,
            ENVIRONMENT_ID,
            protected_prefixes=["/nowhere"],
        )

        self.assertEqual(diagnosis["verdict"], "satisfied")
        self.assertEqual(diagnosis["recommendation"]["kind"], "none")
        self.assertFalse(diagnosis["recommendation"]["requires_approval"])
        self.assertIn("ready", diagnosis["headline"])

    def test_missing_exact_edge_is_rejected(self):
        topology = _topology([])
        topology["edges"] = topology["edges"][:1]
        with self.assertRaisesRegex(ValueError, "no requires edge"):
            build_diagnosis(
                topology,
                REPOSITORY_ID,
                ENVIRONMENT_ID,
                protected_prefixes=["/nowhere"],
            )


if __name__ == "__main__":
    unittest.main()
