"""Tests for package-query provenance carried onto requirement verdicts."""

from __future__ import annotations

import sys
import unittest
from unittest.mock import patch

from packaging.requirements import Requirement

from sphere.introspect import PACKAGES_SCRIPT, WarningLog, _package_record
from sphere.requirements import DeclaredRequirement, diff_requirements


class PackageQueryEvidenceTest(unittest.TestCase):
    @patch("sphere.introspect._run")
    def test_success_records_exact_isolated_command_and_verbatim_stdout(self, run):
        raw_stdout = '[{"name": "PyYAML", "version": "6.0.2"}]\n'
        run.return_value = raw_stdout

        record = _package_record("environment:/demo", sys.executable, WarningLog())

        evidence = record["query_evidence"]
        self.assertTrue(evidence["available"])
        self.assertEqual(evidence["python_path"], sys.executable)
        self.assertEqual(evidence["command"], [sys.executable, "-I", "-c", PACKAGES_SCRIPT])
        self.assertEqual(evidence["raw_stdout"], raw_stdout)
        self.assertIsNone(evidence["reason"])

    @patch("sphere.introspect._run")
    def test_fallback_records_the_command_that_actually_succeeded(self, run):
        run.side_effect = [None, "[]\n"]

        record = _package_record("interpreter:/old", sys.executable, WarningLog())

        self.assertEqual(record["query_evidence"]["command"], [sys.executable, "-c", PACKAGES_SCRIPT])
        self.assertEqual(record["query_evidence"]["raw_stdout"], "[]\n")

    def test_diff_retains_raw_distribution_name_after_canonical_matching(self):
        raw_stdout = '[{"name": "PyYAML", "version": "6.0.2"}]\n'
        record = {
            "context_id": "environment:/demo",
            "python_path": "/demo/bin/python",
            "status": "ok",
            "packages": [{"name": "PyYAML", "version": "6.0.2"}],
            "query_evidence": {
                "available": True,
                "python_path": "/demo/bin/python",
                "command": ["/demo/bin/python", "-I", "-c", PACKAGES_SCRIPT],
                "raw_stdout": raw_stdout,
                "reason": None,
            },
        }
        requirement = DeclaredRequirement(
            requirement=Requirement("pyyaml>=6"), source="requirements.txt", raw="pyyaml>=6"
        )

        diff = diff_requirements([requirement], record)

        self.assertIs(diff["query_evidence"], record["query_evidence"])
        result = diff["requirements"][0]
        self.assertEqual(result["status"], "satisfied")
        self.assertEqual(result["installed_version"], "6.0.2")
        self.assertEqual(
            result["evidence"]["reported_distribution"],
            {"name": "PyYAML", "version": "6.0.2"},
        )

    def test_missing_requirement_records_an_honest_absence(self):
        record = {
            "context_id": "environment:/demo",
            "python_path": "/demo/bin/python",
            "status": "ok",
            "packages": [],
            "query_evidence": {
                "available": True,
                "python_path": "/demo/bin/python",
                "command": ["/demo/bin/python", "-I", "-c", PACKAGES_SCRIPT],
                "raw_stdout": "[]\n",
                "reason": None,
            },
        }
        requirement = DeclaredRequirement(
            requirement=Requirement("idna>=3"), source="requirements.txt", raw="idna>=3"
        )

        result = diff_requirements([requirement], record)["requirements"][0]

        self.assertEqual(result["status"], "missing")
        self.assertIsNone(result["installed_version"])
        self.assertIsNone(result["evidence"]["reported_distribution"])


if __name__ == "__main__":
    unittest.main()
