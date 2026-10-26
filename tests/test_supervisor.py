"""Unit tests for the supertick DSH supervisor validator/watchdog (read-only coverage).

Loads the module by absolute path from the repo layout, exercises validate/tick/wait/initialize
against synthetic run directories in TemporaryDirectory. No real project state is touched.
"""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1] / "skills" / "supertick" / "scripts" / "supervisor.py"
SPEC = importlib.util.spec_from_file_location("supervisor", ROOT)
supervisor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(supervisor)
Invalid = supervisor.Invalid
NOW = "2026-10-26T00:00:00+00:00"


def make_task(tid, status="TODO", attempt=1, criteria=("works",), files=("src/a.py",),
              depends_on=(), change_id=None, coder_id=None, reviewer_id=None, checker_id=None):
    return {"id": tid, "title": f"task {tid}", "status": status, "attempt": attempt,
            "criteria": list(criteria), "files": list(files), "depends_on": list(depends_on),
            "change_id": change_id, "coder_id": coder_id, "reviewer_id": reviewer_id,
            "checker_id": checker_id}


def make_agent(aid, role="coder", task=None, status="running", model="glm-5.3"):
    return {"id": aid, "provider": "zai-coding-cn", "model": model, "role": role,
            "status": status, "last_event_at": NOW, "task": task}


def make_state(project_root, tasks=(), agents=(), phase="implementation", max_coders=2):
    return {"schema": 1, "name": "feat", "project_root": str(project_root), "phase": phase,
            "complexity": "SIMPLE", "max_coders": max_coders, "created_at": NOW,
            "updated_at": NOW, "tasks": list(tasks), "agents": list(agents), "jobs": []}


DIGEST = "a" * 64


class RunDirTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = Path(self._tmp.name)
        self.run = self.project / "SUPERTICK" / "feature-feat"
        self.run.mkdir(parents=True)

    def write_state(self, state):
        (self.run / "RUN.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        return state

    def write_report(self, task, kind, agent_id, verdict="PASS", criteria=("works",),
                     results=("PASS",), change_id=DIGEST, attempt=None):
        attempt = attempt or task["attempt"]
        path = self.run / supervisor.report_path(task, kind)
        path.parent.mkdir(parents=True, exist_ok=True)
        report = {"kind": kind, "task": task["id"], "attempt": attempt, "change_id": change_id,
                  "agent_id": agent_id, "verdict": verdict, "summary": "s",
                  "checks": [{"criterion": c, "result": r, "evidence": "e"}
                             for c, r in zip(criteria, results)]}
        path.write_text(json.dumps(report), encoding="utf-8")
        return report


class TestInitialize(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = Path(self._tmp.name)

    def test_init_creates_run(self):
        run = supervisor.initialize(str(self.project), "csv-export")
        state = json.loads((run / "RUN.json").read_text(encoding="utf-8"))
        self.assertEqual(state["phase"], "planning")
        self.assertTrue((run / "PLAN.MD").is_file())
        self.assertTrue((run / "reports").is_dir())

    def test_reinit_rejected(self):
        supervisor.initialize(str(self.project), "feat")
        with self.assertRaises(Invalid):
            supervisor.initialize(str(self.project), "feat")

    def test_bad_name_rejected(self):
        with self.assertRaises(Invalid):
            supervisor.initialize(str(self.project), "bad name!")


class TestRelativePath(unittest.TestCase):
    def test_ok(self):
        self.assertEqual(str(supervisor.relative_path("reports/t1/attempt-1/review.json")),
                         "reports/t1/attempt-1/review.json")

    def test_single_backslash_rejected(self):
        with self.assertRaises(Invalid):
            supervisor.relative_path("reports\\..\\..\\evil")

    def test_dotdot_slash_rejected(self):
        with self.assertRaises(Invalid):
            supervisor.relative_path("reports/../evil")

    def test_absolute_and_drive_rejected(self):
        for bad in ("/etc/passwd", "C:/x", "C:\\x"):
            with self.assertRaises(Invalid):
                supervisor.relative_path(bad)

    def test_reserved_names_rejected(self):
        for bad in ("CON.json", "com1/log.txt", "NUL"):
            with self.assertRaises(Invalid):
                supervisor.relative_path(bad)

    def test_glob_and_trailing_rejected(self):
        for bad in ("src/*.py", "src/a.", "src/a "):
            with self.assertRaises(Invalid):
                supervisor.relative_path(bad)


class TestValidate(RunDirTestCase):
    def test_minimal_state_ok(self):
        self.write_state(make_state(self.run))
        supervisor.validate(json.loads((self.run / "RUN.json").read_text()))

    def test_dependency_cycle_rejected(self):
        t1 = make_task("t1", depends_on=["t2"])
        t2 = make_task("t2", depends_on=["t1"])
        with self.assertRaises(Invalid):
            supervisor.validate(make_state(self.run, tasks=[t1, t2]))

    def test_missing_dependency_rejected(self):
        with self.assertRaises(Invalid):
            supervisor.validate(make_state(self.run, tasks=[make_task("t1", depends_on=["nope"])]))

    def test_casefold_collision_rejected(self):
        t1 = make_task("t1", status="RUNNING", files=("SRC/A.PY",), coder_id="c1")
        t2 = make_task("t2", status="RUNNING", files=("src/a.py",), coder_id="c2")
        a1 = make_agent("c1", task="t1")
        a2 = make_agent("c2", task="t2")
        with self.assertRaises(Invalid):
            supervisor.validate(make_state(self.run, tasks=[t1, t2], agents=[a1, a2]))

    def test_forbidden_model_rejected(self):
        for provider, model in (("zai-coding-cn", "gpt-6.1"), ("openai", "glm-5.3")):
            agent = make_agent("c1", model=model)
            agent["provider"] = provider
            with self.assertRaises(Invalid):
                supervisor.validate(make_state(self.run, agents=[agent]))

    def test_review_requires_change_id(self):
        t = make_task("t1", status="REVIEW", change_id=None, coder_id="c1", reviewer_id="r1")
        agents = [make_agent("c1", task="t1"), make_agent("r1", role="reviewer", task="t1")]
        with self.assertRaises(Invalid):
            supervisor.validate(make_state(self.run, tasks=[t], agents=agents))

    def test_agent_binding_role_mismatch_rejected(self):
        t = make_task("t1", status="REVIEW", change_id=DIGEST, coder_id="c1", reviewer_id="x1")
        agents = [make_agent("c1", task="t1"), make_agent("x1", role="coder", task="t1")]
        with self.assertRaises(Invalid):
            supervisor.validate(make_state(self.run, tasks=[t], agents=agents))

    def test_checker_must_differ_from_coder(self):
        t = make_task("t1", status="ACCEPTING", change_id=DIGEST, coder_id="c1",
                      reviewer_id="r1", checker_id="c1")
        agents = [make_agent("c1", task="t1"), make_agent("r1", role="reviewer", task="t1")]
        with self.assertRaises(Invalid):
            supervisor.validate(make_state(self.run, tasks=[t], agents=agents))

    def test_max_coders_enforced(self):
        tasks = [make_task(f"t{i}", status="RUNNING", coder_id=f"c{i}", files=(f"src/f{i}.py",)) for i in range(3)]
        agents = [make_agent(f"c{i}", task=f"t{i}") for i in range(3)]
        with self.assertRaises(Invalid):
            supervisor.validate(make_state(self.run, tasks=tasks, agents=agents, max_coders=2))

    def test_done_requires_all_bindings(self):
        t = make_task("t1", status="DONE", change_id=DIGEST, coder_id="c1",
                      reviewer_id="r1")  # no checker
        agents = [make_agent("c1", task="t1"), make_agent("r1", role="reviewer", task="t1")]
        with self.assertRaises(Invalid):
            supervisor.validate(make_state(self.run, tasks=[t], agents=agents))


class TestTickReports(RunDirTestCase):
    def review_task(self):
        t = make_task("t1", status="REVIEW", change_id=DIGEST, coder_id="c1", reviewer_id="r1")
        agents = [make_agent("c1", task="t1"), make_agent("r1", role="reviewer", task="t1")]
        return t, agents

    def test_review_pass_ready(self):
        t, agents = self.review_task()
        self.write_state(make_state(self.run, tasks=[t], agents=agents))
        self.write_report(t, "review", "r1")
        result = supervisor.tick(str(self.run))
        kinds = [a["kind"] for a in result["actions"]]
        self.assertIn("report_ready", kinds)

    def test_stale_report_flagged(self):
        t, agents = self.review_task()
        self.write_state(make_state(self.run, tasks=[t], agents=agents))
        self.write_report(t, "review", "r1", change_id="b" * 64)
        result = supervisor.tick(str(self.run))
        flagged = [a for a in result["actions"] if a["kind"] == "invalid_report"]
        self.assertTrue(flagged)
        self.assertEqual(flagged[0]["priority"], "P0")

    def test_mismatched_actor_flagged(self):
        t, agents = self.review_task()
        self.write_state(make_state(self.run, tasks=[t], agents=agents))
        self.write_report(t, "review", "someone-else")
        result = supervisor.tick(str(self.run))
        self.assertTrue([a for a in result["actions"] if a["kind"] == "invalid_report"])

    def test_acceptance_missing_criterion_flagged(self):
        t = make_task("t1", status="ACCEPTING", change_id=DIGEST, coder_id="c1",
                      reviewer_id="r1", checker_id="k1")
        agents = [make_agent("c1", task="t1"), make_agent("r1", role="reviewer", task="t1"),
                  make_agent("k1", role="checker", task="t1")]
        self.write_state(make_state(self.run, tasks=[t], agents=agents))
        self.write_report(t, "review", "r1")
        self.write_report(t, "acceptance", "k1", criteria=("other",))
        result = supervisor.tick(str(self.run))
        self.assertTrue([a for a in result["actions"] if a["kind"] == "invalid_report"])

    def test_acceptance_not_run_cannot_pass(self):
        t = make_task("t1", status="ACCEPTING", change_id=DIGEST, coder_id="c1",
                      reviewer_id="r1", checker_id="k1")
        agents = [make_agent("c1", task="t1"), make_agent("r1", role="reviewer", task="t1"),
                  make_agent("k1", role="checker", task="t1")]
        self.write_state(make_state(self.run, tasks=[t], agents=agents))
        self.write_report(t, "review", "r1")
        self.write_report(t, "acceptance", "k1", verdict="PASS", results=("NOT_RUN",))
        result = supervisor.tick(str(self.run))
        self.assertTrue([a for a in result["actions"] if a["kind"] == "invalid_report"])

    def test_done_gated_on_reports(self):
        t = make_task("t1", status="DONE", change_id=DIGEST, coder_id="c1",
                      reviewer_id="r1", checker_id="k1")
        agents = [make_agent("c1", task="t1", status="inactive"),
                  make_agent("r1", role="reviewer", task="t1", status="inactive"),
                  make_agent("k1", role="checker", task="t1", status="inactive")]
        self.write_state(make_state(self.run, tasks=[t], agents=agents))
        # no reports on disk -> both missing must surface as P0
        result = supervisor.tick(str(self.run))
        invalid = [a for a in result["actions"] if a["kind"] == "invalid_report"]
        self.assertEqual(len(invalid), 2)
        self.assertTrue(all(a["priority"] == "P0" for a in invalid))


class TestReadyQueue(RunDirTestCase):
    def test_slot_limit(self):
        tasks = [make_task(f"t{i}", files=(f"src/f{i}.py",)) for i in range(3)]
        agents = [make_agent("c0", task=None)]
        state = make_state(self.run, tasks=tasks, agents=agents, max_coders=2)
        self.write_state(state)
        result = supervisor.tick(str(self.run))
        ready = [a for a in result["actions"] if a["kind"] == "task_ready"]
        self.assertEqual(len(ready), 1)  # 1 running coder, max 2 -> 1 free slot

    def test_file_conflict_blocks_ready(self):
        tasks = [make_task("t1", files=("src/a.py",)), make_task("t2", files=("SRC/A.PY",))]
        state = make_state(self.run, tasks=tasks, max_coders=5)
        self.write_state(state)
        result = supervisor.tick(str(self.run))
        ready = [a for a in result["actions"] if a["kind"] == "task_ready"]
        self.assertEqual(len(ready), 1)

    def test_dependency_not_done_blocks_ready(self):
        tasks = [make_task("t1"), make_task("t2", depends_on=["t1"])]
        self.write_state(make_state(self.run, tasks=tasks, max_coders=5))
        result = supervisor.tick(str(self.run))
        ready = [a for a in result["actions"] if a["kind"] == "task_ready"]
        self.assertEqual([a["detail"] for a in ready], ["t1"])

    def test_all_done_signals_verification(self):
        t = make_task("t1", status="DONE", change_id=DIGEST, coder_id="c1",
                      reviewer_id="r1", checker_id="k1")
        agents = [make_agent("c1", task="t1", status="inactive"),
                  make_agent("r1", role="reviewer", task="t1", status="inactive"),
                  make_agent("k1", role="checker", task="t1", status="inactive")]
        self.write_state(make_state(self.run, tasks=[t], agents=agents))
        self.write_report(t, "review", "r1")
        self.write_report(t, "acceptance", "k1")
        result = supervisor.tick(str(self.run))
        self.assertTrue([a for a in result["actions"] if a["kind"] == "verification_needed"])


class TestCompletePhase(RunDirTestCase):
    def done_setup(self):
        t = make_task("t1", status="DONE", change_id=DIGEST, coder_id="c1",
                      reviewer_id="r1", checker_id="k1")
        agents = [make_agent("c1", task="t1", status="inactive"),
                  make_agent("r1", role="reviewer", task="t1", status="inactive"),
                  make_agent("k1", role="checker", task="t1", status="inactive")]
        self.write_state(make_state(self.run, tasks=[t], agents=agents, phase="complete"))
        self.write_report(t, "review", "r1")
        self.write_report(t, "acceptance", "k1")
        return t

    def test_passing_verification_ok(self):
        self.done_setup()
        path = self.run / "reports" / "verification.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"verdict": "PASS", "checks": [
            {"result": "PASS", "evidence": "build green"}]}), encoding="utf-8")
        result = supervisor.tick(str(self.run))
        self.assertFalse([a for a in result["actions"] if a["kind"] == "invalid_verification"])

    def test_missing_or_failing_verification_flagged(self):
        self.done_setup()
        result = supervisor.tick(str(self.run))
        self.assertTrue([a for a in result["actions"]
                         if a["kind"] == "invalid_verification"])
        path = self.run / "reports" / "verification.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"verdict": "FAIL", "checks": []}), encoding="utf-8")
        result = supervisor.tick(str(self.run))
        self.assertTrue([a for a in result["actions"]
                         if a["kind"] == "invalid_verification"])


class TestWait(RunDirTestCase):
    def test_quiet_timeout(self):
        self.write_state(make_state(self.run, phase="planning"))
        result = supervisor.wait(str(self.run), interval=0.05, maximum=0.3)
        self.assertEqual(result["wake"], "timeout")
        self.assertEqual(result["actions"][0]["kind"], "quiet")

    def test_ready_returns_immediately(self):
        self.write_state(make_state(self.run, tasks=[make_task("t1")], max_coders=2))
        result = supervisor.wait(str(self.run), interval=5, maximum=60)
        self.assertEqual(result["wake"], "action")
        self.assertTrue([a for a in result["actions"] if a["kind"] == "task_ready"])

    def test_wait_bounds_validated(self):
        with self.assertRaises(Invalid):
            supervisor.wait(str(self.run), interval=0, maximum=10)


class TestTickIsReadOnly(RunDirTestCase):
    def test_tick_does_not_write_state(self):
        self.write_state(make_state(self.run, tasks=[make_task("t1")], max_coders=2))
        before = (self.run / "RUN.json").read_bytes()
        listing_before = sorted(p.name for p in self.run.rglob("*"))
        supervisor.tick(str(self.run))
        supervisor.tick(str(self.run))
        self.assertEqual(before, (self.run / "RUN.json").read_bytes())
        self.assertEqual(listing_before, sorted(p.name for p in self.run.rglob("*")))


if __name__ == "__main__":
    unittest.main()
