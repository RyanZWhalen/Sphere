"""Unit tests for the plan executor, using injected hooks (no pip, no network)."""

from __future__ import annotations

import unittest

from packaging.requirements import Requirement

from sphere.apply import execute_plan
from sphere.fixplan import build_plan
from sphere.requirements import DeclaredRequirement


REPOSITORY_ID = "repository:/proj"
ENV_ID = "environment:/proj/.venv-broken"
ENV_INTERPRETER = "/proj/.venv-broken/bin/python"


def _diff_item(name, specifier, status, installed_version):
    return {
        "name": name, "requirement": f"{name}{specifier}", "specifier": specifier,
        "extras": [], "marker": None, "url": None, "source": "requirements.txt",
        "raw": f"{name}{specifier}", "installed_version": installed_version, "status": status,
    }


def _topology(diff, verdict="missing"):
    return {
        "nodes": {
            "repositories": [{"id": REPOSITORY_ID, "type": "repository", "path": "/proj"}],
            "environments": [{
                "id": ENV_ID, "type": "environment", "kind": "venv",
                "path": ENV_INTERPRETER, "interpreter_path": ENV_INTERPRETER,
            }],
            "interpreters": [], "contexts": [],
        },
        "edges": [{"type": "requires", "from": REPOSITORY_ID, "to": ENV_ID, "verdict": verdict, "diff": diff}],
    }


def _req(text):
    return DeclaredRequirement(requirement=Requirement(text), source="requirements.txt", raw=text)


def _record(packages):
    return {"context_id": ENV_ID, "python_path": ENV_INTERPRETER, "status": "ok", "packages": packages}


def _rescan(verdict):
    return lambda: {"edges": [{"type": "requires", "from": REPOSITORY_ID, "to": ENV_ID, "verdict": verdict, "diff": []}]}


def _ok_runner(_command):
    return {"exit_status": 0, "stdout_tail": "Successfully installed", "stderr_tail": "", "duration_ms": 5, "error": None}


class ExecutePlanTest(unittest.TestCase):
    def _plan(self, diff, protected=("/nowhere",)):
        return build_plan(_topology(diff), REPOSITORY_ID, ENV_ID, protected_prefixes=list(protected))

    def test_successful_run_streams_receipts_then_done(self):
        diff = [
            _diff_item("six", "==1.16.0", "version-mismatch", "1.15.0"),
            _diff_item("idna", ">=3.0", "missing", None),
        ]
        plan = self._plan(diff)
        requirements = [_req("six==1.16.0"), _req("idna>=3.0")]
        installed_after = _record([{"name": "six", "version": "1.16.0"}, {"name": "idna", "version": "3.11"}])

        events = list(execute_plan(
            plan, requirements,
            runner=_ok_runner,
            package_query=lambda _c, _p: installed_after,
            rescan=_rescan("satisfied"),
        ))

        self.assertEqual(events[0]["event"], "plan")
        receipts = [e for e in events if e["event"] == "receipt"]
        self.assertEqual([e["index"] for e in receipts], [0, 1])
        for event in receipts:
            self.assertEqual(event["step"]["receipt"]["exit_status"], 0)
            self.assertEqual(event["step"]["receipt"]["after"]["status"], "satisfied")
            self.assertEqual(event["step"]["receipt"]["after"]["interpreter_path"], ENV_INTERPRETER)
        done = events[-1]
        self.assertEqual(done["event"], "done")
        self.assertFalse(done["failed"])
        self.assertEqual(done["plan"]["verdict_after"], "satisfied")
        self.assertIsNotNone(done["topology"])

    def test_failure_stops_the_loop_and_reports_honestly(self):
        diff = [
            _diff_item("six", "==1.16.0", "missing", None),
            _diff_item("idna", ">=3.0", "missing", None),
        ]
        plan = self._plan(diff)

        def failing_runner(_command):
            return {"exit_status": 1, "stdout_tail": "", "stderr_tail": "No matching distribution",
                    "duration_ms": 3, "error": "exit status 1"}

        events = list(execute_plan(
            plan, [_req("six==1.16.0"), _req("idna>=3.0")],
            runner=failing_runner,
            package_query=lambda _c, _p: _record([]),
            rescan=_rescan("missing"),
        ))

        receipts = [e for e in events if e["event"] == "receipt"]
        self.assertEqual(len(receipts), 1)  # loop stops after the first failure
        self.assertEqual(receipts[0]["step"]["receipt"]["error"], "exit status 1")
        self.assertEqual(receipts[0]["step"]["receipt"]["stderr_tail"], "No matching distribution")
        done = events[-1]
        self.assertEqual(done["event"], "done")
        self.assertTrue(done["failed"])

    def test_blocked_target_runs_nothing(self):
        diff = [_diff_item("six", "==1.16.0", "missing", None)]
        plan = self._plan(diff, protected=("/proj/.venv-broken",))  # target is protected
        self.assertFalse(plan.target.writable)

        ran = []
        events = list(execute_plan(
            plan, [_req("six==1.16.0")],
            runner=lambda command: ran.append(command) or _ok_runner(command),
            package_query=lambda _c, _p: _record([]),
            rescan=_rescan("missing"),
        ))

        self.assertEqual([e["event"] for e in events], ["plan", "blocked"])
        self.assertEqual(ran, [])  # nothing executed


if __name__ == "__main__":
    unittest.main()
