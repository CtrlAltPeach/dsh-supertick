"""Unit tests for the supertick DSH supervisor validator/watchdog (read-only coverage).

Loads the module by absolute path from the repo layout, exercises validate/tick/wait/initialize
against synthetic run directories in TemporaryDirectory. No real project state is touched.
"""
import hashlib
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
        self.run = self.project / "RUNS" / "feature-feat"
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
        self.assertEqual(run, self.project / "RUNS" / "feature-csv-export")
        state = json.loads((run / "RUN.json").read_text(encoding="utf-8"))
        self.assertEqual(state["phase"], "planning")
        self.assertTrue((run / "PLAN.MD").is_file())
        self.assertTrue((run / "reports").is_dir())

    def test_reinit_rejected(self):
        supervisor.initialize(str(self.project), "feat")
        with self.assertRaises(Invalid):
            supervisor.initialize(str(self.project), "feat")

    def test_legacy_run_blocks_reinit(self):
        legacy = self.project / "SUPERTICK" / "feature-feat"
        legacy.mkdir(parents=True)
        (legacy / "RUN.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(Invalid):
            supervisor.initialize(str(self.project), "feat")

    def test_legacy_run_tick_still_works(self):
        legacy = self.project / "SUPERTICK" / "feature-feat"
        legacy.mkdir(parents=True)
        state = {"schema": 2, "name": "feat", "project_root": str(self.project), "phase": "planning",
                 "complexity": "SIMPLE", "max_coders": 2, "created_at": NOW, "updated_at": NOW,
                 "tasks": [], "agents": [], "jobs": []}
        (legacy / "RUN.json").write_text(json.dumps(state), encoding="utf-8")
        result = supervisor.tick(str(legacy))
        self.assertEqual(result["phase"], "planning")

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


def make_run_task(tid, status="TODO", attempt=1, criteria=("artifacts complete",),
                  run_id="run-a", run_dir="results/toolx/run-a", paid=False, events=None,
                  operator_id=None, reviewer_id=None, checker_id=None, auditor_id=None,
                  launch="dsh-job", depends_on=(), max_review_rounds=None, budget=None):
    authorization = {"paid": paid}
    if paid:
        authorization = {"paid": True, "granted_by": "user", "ref": "m0001", "scope": "attempt 1"}
    if budget is not None:
        authorization["budget"] = budget
    task = {"id": tid, "kind": "run", "title": f"run {tid}", "status": status, "attempt": attempt,
            "criteria": list(criteria), "depends_on": list(depends_on),
            "run_manifest": {"tool": "toolx", "command": ["toolx", "--go"], "run_id": run_id,
                              "run_dir": run_dir, "launch": launch},
            "authorization": authorization, "events": events if events is not None else [],
            "operator_id": operator_id, "reviewer_id": reviewer_id,
            "checker_id": checker_id, "auditor_id": auditor_id}
    if max_review_rounds is not None:
        task["max_review_rounds"] = max_review_rounds
    return task


def make_state2(project_root, tasks=(), agents=(), jobs=(), phase="implementation", max_coders=2,
                max_agents=None, max_review_rounds=None):
    state = {"schema": 2, "name": "feat", "project_root": str(project_root), "phase": phase,
             "complexity": "SIMPLE", "max_coders": max_coders, "created_at": NOW,
             "updated_at": NOW, "tasks": list(tasks), "agents": list(agents), "jobs": list(jobs)}
    if max_agents is not None:
        state["max_agents"] = max_agents
    if max_review_rounds is not None:
        state["max_review_rounds"] = max_review_rounds
    return state


def make_event(type_, reason="r", ref="job-1", prior_run_id=None):
    event = {"type": type_, "at": NOW, "reason": reason, "ref": ref}
    if prior_run_id is not None:
        event["prior_run_id"] = prior_run_id
    return event


class RunTaskTestCase(RunDirTestCase):
    def write_run_report(self, task, kind, agent_id, verdict="PASS", criteria=None,
                         results=("PASS",), run_id=None, attempt=None):
        attempt = attempt or task["attempt"]
        path = self.run / supervisor.report_path(task, kind)
        path.parent.mkdir(parents=True, exist_ok=True)
        report = {"kind": kind, "task": task["id"], "attempt": attempt,
                  "run_id": run_id or task["run_manifest"]["run_id"], "agent_id": agent_id,
                  "verdict": verdict, "summary": "s",
                  "checks": [{"criterion": c, "result": r, "evidence": "e"}
                             for c, r in zip(criteria if criteria is not None else task["criteria"], results)]}
        path.write_text(json.dumps(report), encoding="utf-8")
        return report

    def write_usage_audit(self, task, agent_id, calls=3):
        path = self.run / supervisor.report_path(task, "usage_audit")
        path.parent.mkdir(parents=True, exist_ok=True)
        report = {"kind": "usage_audit", "task": task["id"], "attempt": task["attempt"],
                  "run_id": task["run_manifest"]["run_id"], "agent_id": agent_id,
                  "calls_total": calls, "by_stage": [{"stage": "judge", "calls": calls}],
                  "tokens": {"input": 10, "output": 5, "cache_read": 0, "cache_write": 0},
                  "unknown_usage_calls": [],
                  "reconcile": [{"source": "usage.json", "note": "3 calls"}],
                  "cost_estimate": {"tariffs_ref": "tariffs.json", "total": 0.01,
                                    "currency": "USD", "note": "estimate, not invoice"}}
        path.write_text(json.dumps(report), encoding="utf-8")
        return report

    def done_run_setup(self, paid=True, write_audit=True):
        task = make_run_task("run-1", status="DONE", paid=paid, operator_id="op-1",
                             reviewer_id="rev-1", checker_id="chk-1", auditor_id="aud-1")
        agents = [make_agent("op-1", role="run-operator", task="run-1", status="inactive"),
                  make_agent("rev-1", role="reviewer", task="run-1", status="inactive"),
                  make_agent("chk-1", role="artifact-verifier", task="run-1", status="inactive"),
                  make_agent("aud-1", role="usage-auditor", task="run-1", status="inactive")]
        jobs = [{"id": "job-1", "purpose": "run toolx", "status": "collected", "task": "run-1"}]
        self.write_state(make_state2(self.run, tasks=[task], agents=agents, jobs=jobs))
        self.write_run_report(task, "run_integrity", "rev-1")
        self.write_run_report(task, "acceptance", "chk-1")
        if paid and write_audit:
            self.write_usage_audit(task, "aud-1")
        return task


class TestRunContract(RunTaskTestCase):
    def test_full_lifecycle_valid(self):
        self.done_run_setup()
        supervisor.validate(json.loads((self.run / "RUN.json").read_text(encoding="utf-8")))

    def test_mixed_code_and_run_tasks_valid(self):
        code = make_task("task-1", status="DONE", change_id=DIGEST, coder_id="c1",
                         reviewer_id="r1", checker_id="k1")
        run = make_run_task("run-1")
        agents = [make_agent("c1", task="task-1", status="inactive"),
                  make_agent("r1", role="reviewer", task="task-1", status="inactive"),
                  make_agent("k1", role="checker", task="task-1", status="inactive")]
        state = make_state2(self.run, tasks=[code, run], agents=agents)
        supervisor.validate(state)

    def test_run_task_rejected_in_schema_1(self):
        state = make_state(self.run, tasks=[make_run_task("run-1")])
        with self.assertRaises(Invalid):
            supervisor.validate(state)

    def test_manifest_fields_required(self):
        for mutate, message in ((lambda t: t["run_manifest"].pop("run_id"), "run_id"),
                                (lambda t: t["run_manifest"].pop("run_dir"), "run_dir"),
                                (lambda t: t["run_manifest"].pop("launch"), "launch"),
                                (lambda t: t["run_manifest"].pop("command"), "command")):
            task = make_run_task("run-1")
            mutate(task)
            with self.assertRaises(Invalid):
                supervisor.validate(make_state2(self.run, tasks=[task]))

    def test_run_task_cannot_use_files_or_change_id(self):
        for mutate in (lambda t: t.update(files=["src/a.py"]),
                       lambda t: t.update(change_id=DIGEST)):
            task = make_run_task("run-1")
            mutate(task)
            with self.assertRaises(Invalid):
                supervisor.validate(make_state2(self.run, tasks=[task]))

    def test_paid_run_requires_go_reference(self):
        task = make_run_task("run-1", paid=True)
        task["authorization"] = {"paid": True}
        with self.assertRaises(Invalid):
            supervisor.validate(make_state2(self.run, tasks=[task]))
        task["authorization"] = {"paid": True, "granted_by": "user", "ref": "m0001", "scope": "s"}
        supervisor.validate(make_state2(self.run, tasks=[task]))

    def test_paid_done_requires_auditor_binding(self):
        task = make_run_task("run-1", status="DONE", paid=True, operator_id="op-1",
                             reviewer_id="rev-1", checker_id="chk-1", auditor_id=None)
        agents = [make_agent("op-1", role="run-operator", task="run-1", status="inactive"),
                  make_agent("rev-1", role="reviewer", task="run-1", status="inactive"),
                  make_agent("chk-1", role="artifact-verifier", task="run-1", status="inactive")]
        jobs = [{"id": "job-1", "purpose": "run toolx", "status": "collected", "task": "run-1"}]
        with self.assertRaises(Invalid):
            supervisor.validate(make_state2(self.run, tasks=[task], agents=agents, jobs=jobs))

    def test_running_requires_operator_and_bound_job(self):
        task = make_run_task("run-1", status="RUNNING")
        with self.assertRaises(Invalid):
            supervisor.validate(make_state2(self.run, tasks=[task]))
        task["operator_id"] = "op-1"
        agents = [make_agent("op-1", role="run-operator", task="run-1")]
        with self.assertRaises(Invalid):
            supervisor.validate(make_state2(self.run, tasks=[task], agents=agents))
        jobs = [{"id": "job-1", "purpose": "run toolx", "status": "running", "task": "run-1"}]
        supervisor.validate(make_state2(self.run, tasks=[task], agents=agents, jobs=jobs))

    def test_past_running_requires_collected_job(self):
        task = make_run_task("run-1", status="REVIEW", operator_id="op-1", reviewer_id="rev-1")
        agents = [make_agent("op-1", role="run-operator", task="run-1", status="inactive"),
                  make_agent("rev-1", role="reviewer", task="run-1", status="inactive")]
        jobs = [{"id": "job-1", "purpose": "run toolx", "status": "running", "task": "run-1"}]
        with self.assertRaises(Invalid):
            supervisor.validate(make_state2(self.run, tasks=[task], agents=agents, jobs=jobs))
        jobs[0]["status"] = "collected"
        supervisor.validate(make_state2(self.run, tasks=[task], agents=agents, jobs=jobs))

    def test_external_run_must_not_bind_jobs(self):
        task = make_run_task("run-1", status="RUNNING", launch="external", operator_id="op-1")
        agents = [make_agent("op-1", role="run-operator", task="run-1")]
        jobs = [{"id": "job-1", "purpose": "run toolx", "status": "running", "task": "run-1"}]
        with self.assertRaises(Invalid):
            supervisor.validate(make_state2(self.run, tasks=[task], agents=agents, jobs=jobs))

    def test_run_dir_ownership_case_insensitive(self):
        first = make_run_task("run-1", status="RUNNING", operator_id="op-1")
        second = make_run_task("run-2", status="RUNNING", run_dir="RESULTS/TOOLX/RUN-A", operator_id="op-1")
        agents = [make_agent("op-1", role="run-operator", task="run-1")]
        jobs = [{"id": "job-1", "purpose": "run toolx", "status": "running", "task": "run-1"}]
        with self.assertRaises(Invalid):
            supervisor.validate(make_state2(self.run, tasks=[first, second], agents=agents, jobs=jobs))

    def test_file_inside_run_dir_rejected(self):
        run_task = make_run_task("run-1", status="RUNNING", operator_id="op-1")
        code = make_task("task-1", status="RUNNING", files=("results/toolx/run-a/out.txt",), coder_id="c1")
        agents = [make_agent("op-1", role="run-operator", task="run-1"), make_agent("c1", task="task-1")]
        jobs = [{"id": "job-1", "purpose": "run toolx", "status": "running", "task": "run-1"}]
        with self.assertRaises(Invalid):
            supervisor.validate(make_state2(self.run, tasks=[run_task, code], agents=agents, jobs=jobs))

    def test_boundary_accounting(self):
        task = make_run_task("run-1", status="RUNNING", attempt=2, run_id="run-b",
                             events=[make_event("boundary", prior_run_id="run-a")], operator_id="op-1")
        agents = [make_agent("op-1", role="run-operator", task="run-1")]
        jobs = [{"id": "job-2", "purpose": "run toolx", "status": "running", "task": "run-1"}]
        supervisor.validate(make_state2(self.run, tasks=[task], agents=agents, jobs=jobs))
        task["events"].append(make_event("boundary", prior_run_id="run-b"))
        with self.assertRaises(Invalid):
            supervisor.validate(make_state2(self.run, tasks=[task], agents=agents, jobs=jobs))

    def test_event_ordering_rules(self):
        agents = [make_agent("op-1", role="run-operator", task="run-1")]
        jobs = [{"id": "job-1", "purpose": "run toolx", "status": "running", "task": "run-1"}]
        cases = [
            (dict(events=[make_event("resumed")]), Invalid),
            (dict(events=[make_event("stopped"), make_event("resumed")]), None),
            (dict(events=[make_event("fixed")]), None),
            (dict(events=[make_event("fixed"), make_event("fixed")]), Invalid),
            (dict(run_id="run-b", attempt=2, events=[make_event("boundary", prior_run_id="run-a")]), None),
            (dict(run_id="run-b", attempt=2, events=[make_event("boundary", prior_run_id="run-b")]), Invalid),
        ]
        for kwargs, expected in cases:
            task = make_run_task("run-1", status="RUNNING", operator_id="op-1", **kwargs)
            if expected is Invalid:
                with self.assertRaises(Invalid):
                    supervisor.validate(make_state2(self.run, tasks=[task], agents=agents, jobs=jobs))
            else:
                supervisor.validate(make_state2(self.run, tasks=[task], agents=agents, jobs=jobs))
        done_agents = [make_agent("op-1", role="run-operator", task="run-1", status="inactive"),
                       make_agent("rev-1", role="reviewer", task="run-1", status="inactive"),
                       make_agent("chk-1", role="artifact-verifier", task="run-1", status="inactive")]
        done_jobs = [{"id": "job-1", "purpose": "run toolx", "status": "collected", "task": "run-1"}]
        stopped_done = make_run_task("run-1", status="DONE", events=[make_event("stopped")],
                                     operator_id="op-1", reviewer_id="rev-1", checker_id="chk-1")
        with self.assertRaises(Invalid):
            supervisor.validate(make_state2(self.run, tasks=[stopped_done], agents=done_agents, jobs=done_jobs))


class TestRunTick(RunTaskTestCase):
    def test_paid_done_requires_usage_audit_report(self):
        self.done_run_setup(paid=True, write_audit=False)
        result = supervisor.tick(str(self.run))
        self.assertTrue([a for a in result["actions"]
                         if a["kind"] == "invalid_report" and "usage-audit.json" in a["ref"]])
        task = json.loads((self.run / "RUN.json").read_text(encoding="utf-8"))["tasks"][0]
        self.write_usage_audit(task, "aud-1")
        result = supervisor.tick(str(self.run))
        self.assertFalse([a for a in result["actions"]
                          if a["priority"] == "P0" or a["kind"] == "invalid_report"])

    def test_unpaid_done_needs_no_usage_audit(self):
        self.done_run_setup(paid=False, write_audit=False)
        result = supervisor.tick(str(self.run))
        self.assertFalse([a for a in result["actions"]
                          if a["priority"] == "P0" or a["kind"] == "invalid_report"])

    def test_collect_pending_flagged(self):
        task = make_run_task("run-1", status="RUNNING", operator_id="op-1")
        agents = [make_agent("op-1", role="run-operator", task="run-1")]
        jobs = [{"id": "job-1", "purpose": "run toolx", "status": "collected", "task": "run-1"}]
        self.write_state(make_state2(self.run, tasks=[task], agents=agents, jobs=jobs))
        result = supervisor.tick(str(self.run))
        self.assertTrue([a for a in result["actions"] if a["kind"] == "collect_pending"])
        self.write_run_report(task, "run_integrity", "rev-1")
        result = supervisor.tick(str(self.run))
        self.assertFalse([a for a in result["actions"] if a["kind"] == "collect_pending"])

    def test_review_report_ready_uses_run_paths(self):
        task = make_run_task("run-1", status="REVIEW", operator_id="op-1", reviewer_id="rev-1")
        agents = [make_agent("op-1", role="run-operator", task="run-1", status="inactive"),
                  make_agent("rev-1", role="reviewer", task="run-1", status="inactive")]
        jobs = [{"id": "job-1", "purpose": "run toolx", "status": "collected", "task": "run-1"}]
        self.write_state(make_state2(self.run, tasks=[task], agents=agents, jobs=jobs))
        result = supervisor.tick(str(self.run))
        self.assertFalse([a for a in result["actions"] if a["kind"] == "report_ready"])
        self.write_run_report(task, "run_integrity", "rev-1")
        result = supervisor.tick(str(self.run))
        ready = [a for a in result["actions"] if a["kind"] == "report_ready"]
        self.assertTrue(ready)
        self.assertIn("run-integrity.json", ready[0]["ref"])

    def test_acceptance_must_cover_run_criteria(self):
        self.done_run_setup(paid=False, write_audit=False)
        task = json.loads((self.run / "RUN.json").read_text(encoding="utf-8"))["tasks"][0]
        self.write_run_report(task, "acceptance", "chk-1", criteria=("wrong criterion",))
        result = supervisor.tick(str(self.run))
        self.assertTrue([a for a in result["actions"]
                         if a["kind"] == "invalid_report" and "artifact-verification.json" in a["ref"]])


class TestUsageAudit(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def test_aggregates_jsonl_usage_and_summary(self):
        lines = [
            {"stage": "judge", "model": "m-a", "usage": {"input": 100, "output": 50}},
            {"stage": "judge", "model": "m-a", "usage": {"input": 10, "output": 5, "cache_read": 7}},
            {"phase": "meta", "model": "m-b", "usage": {"prompt_tokens": 8, "completion_tokens": 4}},
        ]
        (self.dir / "audit.jsonl").write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
        (self.dir / "usage.json").write_text(json.dumps({"usage": {"input": 200, "output": 100, "calls_total": 3}}), encoding="utf-8")
        (self.dir / "summary.json").write_text(json.dumps({"evaluation": {"usage": {"input": 318, "output": 159}}}), encoding="utf-8")
        draft = supervisor.usage_audit(str(self.dir), jsonl_names=["audit.jsonl"],
                                       usage_names=["usage.json"], summary_names=["summary.json"])
        self.assertEqual(draft["calls_total"], 3)
        self.assertEqual({s["stage"]: s["calls"] for s in draft["by_stage"]}, {"judge": 2, "meta": 1})
        self.assertEqual(draft["tokens"]["input"], 118)
        self.assertEqual(draft["tokens"]["output"], 59)
        self.assertEqual(draft["tokens"]["cache_read"], 7)
        self.assertEqual(len(draft["reconcile"]), 3)
        notes = {r["source"]: r["note"] for r in draft["reconcile"]}
        self.assertIn("3 calls", notes["usage.json"])
        self.assertIn("in=318", notes["summary.json"])

    def test_unknown_usage_and_tariffs(self):
        lines = [
            {"stage": "judge", "model": "m-a", "usage": {"input": 1_000_000, "output": 1_000_000}},
            {"stage": "judge", "model": "m-unknown", "usage": {"input": 5, "output": 5}},
        ]
        (self.dir / "audit.jsonl").write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
        tariffs = {"currency": "USD", "models": {"m-a": {"input": 1.0, "output": 2.0}}}
        (self.dir / "tariffs.json").write_text(json.dumps(tariffs), encoding="utf-8")
        draft = supervisor.usage_audit(str(self.dir), jsonl_names=["audit.jsonl"], tariffs_path=str(self.dir / "tariffs.json"))
        self.assertEqual(draft["cost_estimate"]["total"], 3.0)
        self.assertEqual(draft["cost_estimate"]["note"], "estimate, not invoice")
        self.assertTrue(any("m-unknown" in x for x in draft["unknown_usage_calls"]))

    def test_invalid_json_line_rejected(self):
        (self.dir / "audit.jsonl").write_text("{not json}\n", encoding="utf-8")
        with self.assertRaises(Invalid):
            supervisor.usage_audit(str(self.dir), jsonl_names=["audit.jsonl"])

    def test_missing_artifact_rejected(self):
        with self.assertRaises(Invalid):
            supervisor.usage_audit(str(self.dir), jsonl_names=["nope.jsonl"])


class TestAgentBudgetContract(RunDirTestCase):
    def test_max_agents_bounds(self):
        for value in (0, 129, "12", True):
            with self.assertRaises(Invalid):
                supervisor.validate(make_state2(self.run, max_agents=value))
        supervisor.validate(make_state2(self.run, max_agents=12))
        supervisor.validate(make_state2(self.run))

    def test_max_review_rounds_bounds(self):
        for value in (0, 9, "3", False):
            with self.assertRaises(Invalid):
                supervisor.validate(make_state2(self.run, max_review_rounds=value))
        supervisor.validate(make_state2(self.run, max_review_rounds=3))

    def test_agent_budget_exceeded_is_reported_once(self):
        agents = [make_agent("c1"), make_agent("c2")]
        self.write_state(make_state2(self.run, agents=agents, max_agents=1))
        result = supervisor.tick(str(self.run))
        over = [a for a in result["actions"] if a["kind"] == "agent_budget_exceeded"]
        self.assertEqual(len(over), 1)
        self.assertEqual(over[0]["priority"], "P1")

    def test_within_agent_budget_is_quiet(self):
        self.write_state(make_state2(self.run, agents=[make_agent("c1")], max_agents=12))
        result = supervisor.tick(str(self.run))
        self.assertFalse([a for a in result["actions"] if a["kind"] == "agent_budget_exceeded"])


class TestReviewRounds(RunDirTestCase):
    def reviewer_pair(self, task):
        return [make_agent("c1", task=task["id"], status="inactive"),
                make_agent("r1", role="reviewer", task=task["id"], status="inactive")]

    def test_run_level_ceiling_flags_past_attempt(self):
        task = make_task("t1", status="REVIEW", attempt=4, change_id=DIGEST,
                         coder_id="c1", reviewer_id="r1")
        self.write_state(make_state2(self.run, tasks=[task], agents=self.reviewer_pair(task),
                                     max_review_rounds=3))
        result = supervisor.tick(str(self.run))
        flagged = [a for a in result["actions"] if a["kind"] == "review_rounds_exceeded"]
        self.assertEqual(len(flagged), 1)
        self.assertEqual(flagged[0]["priority"], "P1")

    def test_task_override_wins(self):
        task = make_task("t1", status="REVIEW", attempt=3, change_id=DIGEST,
                         coder_id="c1", reviewer_id="r1")
        task["max_review_rounds"] = 5
        self.write_state(make_state2(self.run, tasks=[task], agents=self.reviewer_pair(task),
                                     max_review_rounds=2))
        result = supervisor.tick(str(self.run))
        self.assertFalse([a for a in result["actions"] if a["kind"] == "review_rounds_exceeded"])

    def test_done_is_exempt(self):
        task = make_task("t1", status="DONE", attempt=5, change_id=DIGEST, coder_id="c1",
                         reviewer_id="r1", checker_id="k1")
        agents = self.reviewer_pair(task) + [make_agent("k1", role="checker", task="t1",
                                                        status="inactive")]
        self.write_state(make_state2(self.run, tasks=[task], agents=agents, max_review_rounds=3))
        self.write_report(task, "review", "r1")
        self.write_report(task, "acceptance", "k1")
        result = supervisor.tick(str(self.run))
        self.assertFalse([a for a in result["actions"] if a["kind"] == "review_rounds_exceeded"])


class TestAgentAccounting(RunDirTestCase):
    def state_with(self, agent):
        return make_state2(self.run, agents=[agent])

    def test_absent_accounting_is_valid(self):
        supervisor.validate(self.state_with(make_agent("c1")))

    def test_usage_requires_source(self):
        agent = make_agent("c1")
        agent["usage"] = {"input": 1, "output": 2, "cache_read": 0, "cache_write": 0}
        with self.assertRaises(Invalid):
            supervisor.validate(self.state_with(agent))
        agent["usage_source"] = "host"
        supervisor.validate(self.state_with(agent))

    def test_usage_shape_checked(self):
        for bad in ({"input": -1, "output": 0, "cache_read": 0, "cache_write": 0},
                    {"input": 1}, {"input": "1", "output": 0, "cache_read": 0, "cache_write": 0}):
            agent = make_agent("c1")
            agent["usage"], agent["usage_source"] = bad, "host"
            with self.assertRaises(Invalid):
                supervisor.validate(self.state_with(agent))

    def test_usage_source_enum_and_timestamps(self):
        agent = make_agent("c1")
        agent["usage"] = {"input": 1, "output": 0, "cache_read": 0, "cache_write": 0}
        agent["usage_source"] = "guessed"
        with self.assertRaises(Invalid):
            supervisor.validate(self.state_with(agent))
        for field in ("started_at", "ended_at"):
            agent = make_agent("c1")
            agent[field] = "not-a-timestamp"
            with self.assertRaises(Invalid):
                supervisor.validate(self.state_with(agent))
            agent[field] = NOW
            supervisor.validate(self.state_with(agent))


class TestBudgetContract(RunTaskTestCase):
    def test_invalid_budget_shapes_rejected(self):
        for budget in ({}, {"max_calls": "3"}, {"max_calls": -1}, {"max_cost": 1.0},
                       {"max_cost": 1.0, "currency": 7}):
            task = make_run_task("run-1", paid=True, budget=budget)
            with self.assertRaises(Invalid):
                supervisor.validate(make_state2(self.run, tasks=[task]))

    def test_valid_budget_shapes_accepted(self):
        for budget in ({"max_calls": 3}, {"max_cost": 1.0, "currency": "USD"},
                       {"max_calls": 5, "max_cost": 2, "currency": "USD"}):
            task = make_run_task("run-1", paid=True, budget=budget)
            supervisor.validate(make_state2(self.run, tasks=[task]))

    def test_stop_reason_shape(self):
        task = make_run_task("run-1")
        task["stop_reason"] = "   "
        with self.assertRaises(Invalid):
            supervisor.validate(make_state2(self.run, tasks=[task]))
        task["stop_reason"] = "guard trip: repeated 503"
        supervisor.validate(make_state2(self.run, tasks=[task]))

    def test_budget_exceeded_is_advisory_not_a_gate(self):
        task = make_run_task("run-1", status="DONE", paid=True, budget={"max_calls": 2},
                             operator_id="op-1", reviewer_id="rev-1", checker_id="chk-1",
                             auditor_id="aud-1")
        agents = [make_agent("op-1", role="run-operator", task="run-1", status="inactive"),
                  make_agent("rev-1", role="reviewer", task="run-1", status="inactive"),
                  make_agent("chk-1", role="artifact-verifier", task="run-1", status="inactive"),
                  make_agent("aud-1", role="usage-auditor", task="run-1", status="inactive")]
        jobs = [{"id": "job-1", "purpose": "run toolx", "status": "collected", "task": "run-1"}]
        self.write_state(make_state2(self.run, tasks=[task], agents=agents, jobs=jobs))
        self.write_run_report(task, "run_integrity", "rev-1")
        self.write_run_report(task, "acceptance", "chk-1")
        self.write_usage_audit(task, "aud-1", calls=3)
        result = supervisor.tick(str(self.run))
        over = [a for a in result["actions"] if a["kind"] == "budget_exceeded"]
        self.assertEqual(len(over), 1)
        self.assertEqual(over[0]["priority"], "P1")
        self.assertFalse([a for a in result["actions"] if a["kind"] == "invalid_report"])

    def test_within_budget_is_quiet(self):
        task = make_run_task("run-1", status="DONE", paid=True, budget={"max_calls": 5},
                             operator_id="op-1", reviewer_id="rev-1", checker_id="chk-1",
                             auditor_id="aud-1")
        agents = [make_agent("op-1", role="run-operator", task="run-1", status="inactive"),
                  make_agent("rev-1", role="reviewer", task="run-1", status="inactive"),
                  make_agent("chk-1", role="artifact-verifier", task="run-1", status="inactive"),
                  make_agent("aud-1", role="usage-auditor", task="run-1", status="inactive")]
        jobs = [{"id": "job-1", "purpose": "run toolx", "status": "collected", "task": "run-1"}]
        self.write_state(make_state2(self.run, tasks=[task], agents=agents, jobs=jobs))
        self.write_run_report(task, "run_integrity", "rev-1")
        self.write_run_report(task, "acceptance", "chk-1")
        self.write_usage_audit(task, "aud-1", calls=1)
        result = supervisor.tick(str(self.run))
        self.assertFalse([a for a in result["actions"] if a["kind"] == "budget_exceeded"])


class TestPostrunCheck(RunTaskTestCase):
    @staticmethod
    def digest(data):
        return hashlib.sha256(data).hexdigest()

    def test_freeze_hashes_and_stop_reason(self):
        kept = self.project / "src" / "kept.py"
        kept.parent.mkdir(parents=True)
        kept.write_bytes(b"kept")
        drifted = self.project / "src" / "drifted.py"
        drifted.write_bytes(b"new content")
        manifest = {"schema": 1, "inputs": {"files": {str(kept): self.digest(b"kept"),
                                                          str(drifted): self.digest(b"old content")}}}
        (self.run / "FROZEN-INPUTS.json").write_text(json.dumps(manifest), encoding="utf-8")
        task = make_run_task("run-1", status="BLOCKED", launch="external")
        task["run_manifest"]["input_freeze"] = "FROZEN-INPUTS.json sha256 abc123; dry-run exit 0"
        task["stop_reason"] = "guard trip: repeated 503"
        self.write_state(make_state2(self.run, tasks=[task]))
        result = supervisor.postrun_check(str(self.run))
        self.assertTrue(result["deterministic"])
        self.assertEqual(result["gate_validation"], "not_run")
        entry = result["tasks"][0]
        self.assertTrue(entry["stop_reason_recorded"])
        self.assertFalse(entry["run_dir"]["exists"])
        freeze = entry["input_freeze"][0]
        self.assertEqual(freeze["source"], "FROZEN-INPUTS.json")
        self.assertEqual(freeze["listed"], 2)
        self.assertEqual(freeze["matched"], 1)
        self.assertEqual(freeze["mismatched"], {"count": 1, "names": [str(drifted)]})
        self.assertEqual(freeze["missing"]["count"], 0)

    def test_missing_manifest_reports_error(self):
        task = make_run_task("run-1", status="BLOCKED", launch="external")
        task["run_manifest"]["input_freeze"] = "ABSENT.json sha256 abc123"
        self.write_state(make_state2(self.run, tasks=[task]))
        freeze = supervisor.postrun_check(str(self.run))["tasks"][0]["input_freeze"][0]
        self.assertEqual(freeze["source"], "ABSENT.json")
        self.assertIn("not found", freeze["error"])

    def test_run_dir_listing_and_event_tally(self):
        directory = self.run / "results" / "toolx" / "run-a"
        directory.mkdir(parents=True)
        (directory / "out.json").write_text("{}", encoding="utf-8")
        task = make_run_task("run-1", status="RUNNING", launch="external", operator_id="op-1",
                             events=[make_event("stopped")])
        self.write_state(make_state2(self.run, tasks=[task],
                                     agents=[make_agent("op-1", role="run-operator", task="run-1")]))
        entry = supervisor.postrun_check(str(self.run))["tasks"][0]
        self.assertEqual(entry["events"]["stopped"], 1)
        self.assertEqual(entry["run_dir"]["files"], 1)
        self.assertEqual(entry["run_dir"]["top_level"], ["out.json"])
        self.assertFalse(entry["stop_reason_recorded"])
        self.assertFalse(any(entry["audit_reports"].values()))


if __name__ == "__main__":
    unittest.main()
