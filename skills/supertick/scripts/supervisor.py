#!/usr/bin/env python3
"""Read-only, cross-platform supervisor for the DSH supertick skill pack."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys
import time

VERSION = "0.1.0"
STATUSES = {"TODO", "RUNNING", "REVIEW", "ACCEPTING", "DONE", "BLOCKED"}
PHASES = {"planning", "implementation", "verification", "complete"}
ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$")
DIGEST = re.compile(r"^[0-9a-f]{64}$")


class Invalid(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise Invalid(message)


def enum(value, allowed):
    return isinstance(value, str) and value in allowed


def load_allowed_models():
    path = Path(__file__).resolve().parent.parent / "references" / "ALLOWED-MODELS.json"
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))["models"]
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise Invalid(f"cannot read allowed models config: {error}") from error
    pairs = {(entry.get("provider"), entry.get("model")) for entry in entries if isinstance(entry, dict)}
    require(bool(pairs), "ALLOWED-MODELS.json defines no usable provider/model pairs")
    return pairs


ALLOWED_MODELS = load_allowed_models()


def plain_path(path):
    """Refuse symlinks and Windows junctions instead of following them."""
    path = Path(path).absolute()
    for part in (path, *path.parents):
        require(not part.is_symlink() and not part.is_junction(), f"linked path: {part}")
    return path


def relative_path(value):
    require(isinstance(value, str) and value and "\\" not in value and ":" not in value,
            "path must be a relative POSIX path")
    require(all(ord(char) >= 32 for char in value), "control character in path")
    parts = value.split("/")
    require(not value.startswith("/") and all(p not in ("", ".", "..") for p in parts),
            f"unsafe relative path: {value}")
    require(all(not any(c in p for c in '*?<>|"') and not p.endswith((" ", ".")) for p in parts),
            f"non-portable relative path: {value}")
    require(all(p.split(".")[0].upper() not in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]} for p in parts),
            f"reserved relative path: {value}")
    return PurePosixPath(value)


def inside(base, relative):
    relative_path(relative)
    base = plain_path(base)
    candidate = plain_path(base / relative)
    require(candidate.is_relative_to(base), "path escapes run directory")
    return candidate


def timestamp(value):
    require(isinstance(value, str), "timestamp must be a string")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise Invalid(f"invalid timestamp: {value}") from exc
    require(result.tzinfo is not None, "timestamp needs timezone")
    return result


def strings(value, label, nonempty=False):
    require(isinstance(value, list) and all(isinstance(x, str) and x.strip() for x in value),
            f"{label} must be a list of nonempty strings")
    require(len(value) == len(set(value)), f"duplicate {label}")
    require(not nonempty or bool(value), f"empty {label}")
    return value


def validate(state):
    require(isinstance(state, dict) and type(state.get("schema")) is int and state["schema"] == 1, "expected schema 1")
    require(enum(state.get("phase"), PHASES), "invalid phase")
    require(isinstance(state.get("name"), str) and bool(ID.fullmatch(state["name"])), "invalid run name")
    require(isinstance(state.get("project_root"), str) and Path(state["project_root"]).is_absolute(), "project_root must be absolute")
    timestamp(state.get("created_at"))
    timestamp(state.get("updated_at"))
    limit = state.get("max_coders")
    require(type(limit) is int and 1 <= limit <= 8, "max_coders must be 1..8")
    require(enum(state.get("complexity"), {"SIMPLE", "MEDIUM", "COMPLEX"}), "invalid complexity")
    tasks, agents, jobs = state.get("tasks"), state.get("agents"), state.get("jobs")
    require(isinstance(tasks, list) and isinstance(agents, list) and isinstance(jobs, list), "tasks/agents/jobs must be lists")
    by_id = {}
    portable_ids = set()
    for task in tasks:
        require(isinstance(task, dict), "task must be an object")
        key = task.get("id")
        require(isinstance(key, str) and bool(ID.fullmatch(key)) and key not in by_id, "invalid or duplicate task id")
        relative_path(key)
        require(key.casefold() not in portable_ids, "case-insensitive task id collision")
        portable_ids.add(key.casefold())
        by_id[key] = task
        require(isinstance(task.get("title"), str) and bool(task["title"].strip()), "empty task title")
        require(enum(task.get("status"), STATUSES), f"invalid status: {key}")
        require(type(task.get("attempt")) is int and task["attempt"] >= 1, "attempt must be positive")
        strings(task.get("criteria"), "criteria", True)
        for path in strings(task.get("files"), "files", True):
            relative_path(path)
        strings(task.get("depends_on"), "dependencies")
        change = task.get("change_id")
        require(change is None or isinstance(change, str) and bool(DIGEST.fullmatch(change)), "change_id must be SHA-256 or null")
        if task["status"] in {"REVIEW", "ACCEPTING", "DONE"}:
            require(change is not None, "review requires change_id")
    visiting, visited = set(), set()

    def visit(key):
        require(key not in visiting, "dependency cycle")
        if key in visited:
            return
        visiting.add(key)
        for dep in by_id[key]["depends_on"]:
            require(dep in by_id and dep != key, "unknown or self dependency")
            visit(dep)
        visiting.remove(key)
        visited.add(key)

    for key in by_id:
        visit(key)
    occupied = {}
    for task in tasks:
        if task["status"] in {"RUNNING", "REVIEW", "ACCEPTING", "DONE"}:
            require(all(by_id[x]["status"] == "DONE" for x in task["depends_on"]), "unfinished dependency")
        if task["status"] in {"RUNNING", "REVIEW", "ACCEPTING"}:
            for path in task["files"]:
                # Windows is case-insensitive; use the same conservative rule everywhere.
                key = path.casefold()
                require(key not in occupied, f"file ownership collision: {path}")
                occupied[key] = task["id"]
    agent_ids = set()
    for agent in agents:
        require(isinstance(agent, dict), "agent must be an object")
        key = agent.get("id")
        require(isinstance(key, str) and bool(key.strip()) and key not in agent_ids, "invalid or duplicate agent id")
        agent_ids.add(key)
        provider, model = agent.get("provider"), agent.get("model")
        require(isinstance(provider, str) and isinstance(model, str) and (provider, model) in ALLOWED_MODELS, "forbidden agent model")
        require(enum(agent.get("role"), {"researcher", "architect", "tech-lead", "risk-tester", "coder", "reviewer", "checker", "verifier"}), "invalid agent role")
        require(enum(agent.get("status"), {"running", "inactive", "stopped"}), "invalid agent status")
        timestamp(agent.get("last_event_at"))
        require(agent.get("task") is None or isinstance(agent["task"], str) and agent["task"] in by_id, "unknown agent task")
    for task in tasks:
        for field, role in (("coder_id", "coder"), ("reviewer_id", "reviewer"), ("checker_id", "checker")):
            key = task.get(field)
            if key is not None:
                agent = next((a for a in agents if a["id"] == key), None)
                require(agent is not None and agent["role"] == role and agent.get("task") == task["id"], f"invalid {field} binding")
        bound = [task.get(f) for f in ("coder_id", "reviewer_id", "checker_id") if task.get(f) is not None]
        require(len(bound) == len(set(bound)), "reviewer/checker must be independent")
        if task["status"] in {"RUNNING", "REVIEW", "ACCEPTING", "DONE"}:
            require(task.get("coder_id") is not None, "started task needs coder_id")
        if task["status"] in {"REVIEW", "ACCEPTING", "DONE"}:
            require(task.get("reviewer_id") is not None, "review requires reviewer_id")
        if task["status"] in {"ACCEPTING", "DONE"}:
            require(task.get("checker_id") is not None, "acceptance requires checker_id")
    active_coders = sum(a["status"] == "running" and a["role"] == "coder" for a in agents)
    require(active_coders <= limit, "max_coders exceeded")
    job_ids = set()
    for job in jobs:
        require(isinstance(job, dict) and isinstance(job.get("id"), str) and bool(job["id"].strip()) and job["id"] not in job_ids, "invalid or duplicate job id")
        job_ids.add(job["id"])
        require(enum(job.get("status"), {"running", "collected", "killed"}), "invalid job status")
        require(isinstance(job.get("purpose"), str) and bool(job["purpose"].strip()), "empty job purpose")
    if state["phase"] == "complete":
        require(bool(tasks) and all(t["status"] == "DONE" for t in tasks), "complete run has unfinished tasks")
        require(not any(a["status"] == "running" for a in agents), "complete run has running agents")
        require(not any(j["status"] == "running" for j in jobs), "complete run has uncollected jobs")
    return state


def load(run):
    raw = inside(run, "RUN.json").read_bytes()
    return validate(json.loads(raw)), hashlib.sha256(raw).hexdigest()


def report_path(task, kind):
    return f"reports/{task['id']}/attempt-{task['attempt']}/{kind}.json"


def check_report(run, task, kind):
    path = inside(run, report_path(task, kind))
    require(path.is_file(), f"missing {kind} report")
    report = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(report, dict), "report must be an object")
    require(report.get("kind") == kind and report.get("task") == task["id"] and type(report.get("attempt")) is int and report["attempt"] == task["attempt"], "report identity mismatch")
    require(report.get("change_id") == task["change_id"], "stale report change_id")
    actor = task.get("reviewer_id" if kind == "review" else "checker_id")
    require(actor is not None and report.get("agent_id") == actor, "report agent mismatch")
    require(enum(report.get("verdict"), {"PASS", "FAIL"}), "report verdict must be PASS or FAIL")
    require(isinstance(report.get("summary"), str) and bool(report["summary"].strip()), "report needs summary")
    checks = report.get("checks")
    require(isinstance(checks, list) and bool(checks), "report needs checks")
    for check in checks:
        require(isinstance(check, dict) and isinstance(check.get("criterion"), str), "invalid check")
        require(enum(check.get("result"), {"PASS", "FAIL", "NOT_RUN"}), "invalid check result")
        require(isinstance(check.get("evidence"), str) and bool(check["evidence"].strip()), "check needs evidence")
    if kind == "acceptance":
        require(sorted(c["criterion"] for c in checks) == sorted(task["criteria"]), "acceptance must cover each criterion exactly once")
    if report["verdict"] == "PASS":
        require(all(c["result"] == "PASS" for c in checks), "PASS cannot contain FAIL/NOT_RUN")
    return report


def check_verification(run):
    report = json.loads(inside(run, "reports/verification.json").read_text(encoding="utf-8"))
    require(isinstance(report, dict) and report.get("verdict") == "PASS", "verification has not passed")
    checks = report.get("checks")
    require(isinstance(checks, list) and bool(checks), "verification needs checks")
    for check in checks:
        require(isinstance(check, dict) and check.get("result") == "PASS" and isinstance(check.get("evidence"), str) and bool(check["evidence"].strip()), "verification check failed or has no evidence")
    return report


def tick(run, now=None):
    state, revision = load(run)
    now = now or datetime.now(timezone.utc)
    actions = []

    def add(priority, kind, detail, ref="RUN.json"):
        actions.append({"priority": priority, "kind": kind, "detail": detail, "ref": ref})

    tasks = {t["id"]: t for t in state["tasks"]}
    for task in tasks.values():
        status = task["status"]
        if status == "BLOCKED":
            add("P1", "blocked", task["id"])
        kinds = []
        if status in {"ACCEPTING", "DONE"}:
            kinds.append("review")
        if status == "DONE":
            kinds.append("acceptance")
        if status in {"REVIEW", "ACCEPTING"}:
            kind = "review" if status == "REVIEW" else "acceptance"
            if inside(run, report_path(task, kind)).exists():
                kinds.append(kind)
        for kind in kinds:
            try:
                report = check_report(run, task, kind)
                if report["verdict"] != "PASS":
                    add("P0" if status == "DONE" or (kind == "review" and status == "ACCEPTING") else "P1", "report_failed", task["id"], report_path(task, kind))
                elif status == "REVIEW" or (status == "ACCEPTING" and kind == "acceptance"):
                    add("P1", "report_ready", f"{task['id']} {kind}", report_path(task, kind))
            except (Invalid, OSError, ValueError) as exc:
                add("P0", "invalid_report", f"{task['id']}: {exc}", report_path(task, kind))
    if state["phase"] == "implementation":
        occupied = {p.casefold() for t in tasks.values() if t["status"] in {"RUNNING", "REVIEW", "ACCEPTING"} for p in t["files"]}
        slots = state["max_coders"] - sum(a["role"] == "coder" and a["status"] == "running" for a in state["agents"])
        for task in tasks.values():
            if slots > 0 and task["status"] == "TODO" and all(tasks[d]["status"] == "DONE" for d in task["depends_on"]) and not any(p.casefold() in occupied for p in task["files"]):
                add("P1", "task_ready", task["id"])
                occupied.update(p.casefold() for p in task["files"])
                slots -= 1
        if tasks and all(t["status"] == "DONE" for t in tasks.values()):
            add("P1", "verification_needed", "all tasks accepted")
    for agent in state["agents"]:
        age = (now - timestamp(agent["last_event_at"])).total_seconds()
        if agent["status"] == "running" and age >= 1800:
            add("P1", "check_agent", f"{agent['id']}: no recorded event for {int(age)}s; verify native status, not proof of death")
    if state["phase"] == "complete":
        try:
            check_verification(run)
        except (Invalid, OSError, ValueError) as exc:
            add("P0", "invalid_verification", str(exc), "reports/verification.json")
    actions.sort(key=lambda a: a["priority"])
    return {"version": VERSION, "revision": revision, "phase": state["phase"], "actions": actions or [{"priority": "P4", "kind": "quiet", "detail": "no action", "ref": "RUN.json"}]}


def initialize(project, name):
    require(bool(ID.fullmatch(name)), "invalid run name")
    project = plain_path(project)
    require(project.is_dir(), "project directory does not exist")
    run = inside(project, f"SUPERTICK/feature-{name}")
    require(not run.exists(), "run already exists; choose resume or another name")
    run.mkdir(parents=True, exist_ok=False)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    state = {"schema": 1, "name": name, "project_root": str(project), "phase": "planning", "complexity": "SIMPLE", "max_coders": 2, "created_at": now, "updated_at": now, "tasks": [], "agents": [], "jobs": []}
    (run / "RUN.json").write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (run / "PLAN.MD").write_text("# Plan\n\nNarrative, scope, risks and decisions. Task status lives only in RUN.json.\n", encoding="utf-8")
    (run / "reports").mkdir()
    return run


def wait(run, interval=20, maximum=900, wake_on="P1"):
    require(0 < interval <= 300 and 0 < maximum <= 3600, "invalid wait bounds")
    require(wake_on in {"P0", "P1", "P2", "P3"}, "invalid wake priority")
    started = time.monotonic()
    first_revision = None
    while True:
        result = tick(run)
        if first_revision is None:
            first_revision = result["revision"]
        reason = None
        if any(a["priority"] <= wake_on for a in result["actions"]):
            reason = "action"
        elif result["revision"] != first_revision:
            reason = "state_changed"
        elif time.monotonic() - started >= maximum:
            reason = "timeout"
        if reason:
            return {"wake": reason, **result}
        time.sleep(min(interval, max(0.001, maximum - (time.monotonic() - started))))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("project")
    init.add_argument("name")
    for command in ("tick", "wait"):
        sub = commands.add_parser(command)
        sub.add_argument("run")
        if command == "wait":
            sub.add_argument("--interval", type=float, default=20)
            sub.add_argument("--max", type=float, default=900)
            sub.add_argument("--wake-on", choices=["P0", "P1", "P2", "P3"], default="P1")
    args = parser.parse_args(argv)
    if sys.version_info < (3, 12):
        print(json.dumps({"error": "Python 3.12+ required for Windows junction checks"}), file=sys.stderr)
        return 2
    try:
        if args.command == "init":
            result = {"run": str(initialize(args.project, args.name))}
        elif args.command == "tick":
            result = tick(args.run)
        else:
            result = wait(args.run, args.interval, args.max, args.wake_on)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (Invalid, OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
