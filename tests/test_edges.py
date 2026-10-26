"""Focused schema, CLI and notification-boundary regressions."""
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from unittest import mock
import unittest

from test_supervisor import (DIGEST, Invalid, RunDirTestCase, make_agent,
                             make_state, make_task, supervisor)


class EdgeTests(RunDirTestCase):
    def test_single_backslash_is_one_character(self):
        for value in ("src" + chr(92) + "a.py", ".." + chr(92) + ".." + chr(92) + "evil"):
            with self.assertRaises(Invalid):
                supervisor.relative_path(value)

    def test_collection_values_are_invalid_not_typeerror(self):
        for field in ("phase", "complexity"):
            for bad in ([], {}, ["planning"]):
                with self.subTest(field=field, bad=bad):
                    state = make_state(self.project)
                    state[field] = bad
                    with self.assertRaises(Invalid):
                        supervisor.validate(state)
        for field in ("model", "role", "status", "task"):
            state = make_state(self.project, agents=[make_agent("a")])
            state["agents"][0][field] = []
            with self.subTest(field=field), self.assertRaises(Invalid):
                supervisor.validate(state)

    def test_portable_task_ids(self):
        for tasks in ([make_task("A"), make_task("a")], [make_task("CON")]):
            with self.assertRaises(Invalid):
                supervisor.validate(make_state(self.project, tasks=tasks))

    def test_control_character_path_refused(self):
        with self.assertRaises(Invalid):
            supervisor.relative_path("src/a" + chr(0) + ".py")

    def test_blocked_before_dispatch_needs_no_coder(self):
        state = make_state(self.project, tasks=[make_task("t", status="BLOCKED")])
        supervisor.validate(state)

    def test_boolean_schema_is_not_schema_one(self):
        state = make_state(self.project)
        state["schema"] = True
        with self.assertRaises(Invalid):
            supervisor.validate(state)

    def test_invalid_schema_cli_returns_json_error(self):
        state = make_state(self.project)
        state["phase"] = []
        self.write_state(state)
        stderr = StringIO()
        with redirect_stderr(stderr):
            code = supervisor.main(["tick", str(self.run)])
        self.assertEqual(code, 2)
        self.assertIn("invalid phase", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_old_python_cli_has_explanatory_error(self):
        with mock.patch.object(supervisor.sys, "version_info", (3, 11)), redirect_stderr(StringIO()) as stream:
            result = supervisor.main(["tick", str(self.run)])
        self.assertEqual(result, 2)
        self.assertIn("3.12+", stream.getvalue())

    def test_junction_path_refused(self):
        with mock.patch.object(Path, "is_junction", return_value=True):
            with self.assertRaises(Invalid):
                supervisor.inside(self.run, "RUN.json")

    def test_wait_wakes_on_state_revision_change(self):
        one = {"revision": "a", "phase": "planning", "actions": [{"priority": "P4"}]}
        two = {**one, "revision": "b"}
        with mock.patch.object(supervisor, "tick", side_effect=[one, two]):
            result = supervisor.wait(self.run, interval=0.001, maximum=1)
        self.assertEqual(result["wake"], "state_changed")

    def test_complete_rejects_uncollected_jobs(self):
        state = make_state(self.project, phase="complete")
        state["jobs"] = [{"id": "j1", "status": "running", "purpose": "test"}]
        # A complete state with no tasks is also invalid; use an accepted task
        # so this regression specifically exercises the job invariant.
        task = make_task("t", status="DONE", change_id=DIGEST, coder_id="c", reviewer_id="r", checker_id="k")
        state["tasks"] = [task]
        state["agents"] = [make_agent("c", task="t", status="inactive"),
                           make_agent("r", role="reviewer", task="t", status="inactive"),
                           make_agent("k", role="checker", task="t", status="inactive")]
        with self.assertRaisesRegex(Invalid, "uncollected jobs"):
            supervisor.validate(state)

    def test_malformed_report_verdict_is_p0(self):
        task = make_task("t", status="REVIEW", change_id=DIGEST, coder_id="c", reviewer_id="r")
        agents = [make_agent("c", task="t"), make_agent("r", role="reviewer", task="t")]
        self.write_state(make_state(self.project, tasks=[task], agents=agents))
        self.write_report(task, "review", "r", verdict=[])
        result = supervisor.tick(self.run)
        self.assertTrue(any(a["kind"] == "invalid_report" and a["priority"] == "P0" for a in result["actions"]))


if __name__ == "__main__":
    unittest.main()
