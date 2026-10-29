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

VERSION = "0.6.1"
STATUSES = {"TODO", "RUNNING", "REVIEW", "ACCEPTING", "DONE", "BLOCKED"}
PHASES = {"planning", "implementation", "verification", "complete"}
TASK_KINDS = {"code", "run"}
EVENT_TYPES = {"stop_requested", "stopped", "resumed", "fixed", "boundary"}
ROLES = {"researcher", "architect", "tech-lead", "risk-tester", "coder", "reviewer", "checker", "verifier",
         "run-operator", "usage-auditor", "artifact-verifier"}
ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$")
DIGEST = re.compile(r"^[0-9a-f]{64}$")
USAGE_KEYS = ("input", "output", "cache_read", "cache_write")
USAGE_SOURCES = {"host", "unobserved"}
AGENT_ACCESS = {"read", "write"}
# Fan-out scales with complexity: a one-file fix does not need a fleet, and a
# migration should not be squeezed into a SIMPLE budget. The run declares its
# plan first; these are the ceilings that plan starts from.
COMPLEXITY_DEFAULTS = {
    "SIMPLE": {"max_agents": 4, "max_review_rounds": 2, "max_concurrent_agents": 3},
    "MEDIUM": {"max_agents": 8, "max_review_rounds": 3, "max_concurrent_agents": 4},
    "COMPLEX": {"max_agents": 14, "max_review_rounds": 4, "max_concurrent_agents": 6},
}


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


def check_agent_accounting(agent):
    """Optional, host-reported child accounting. Absence is normal and never invalid."""
    for field in ("started_at", "ended_at"):
        if agent.get(field) is not None:
            timestamp(agent[field])
    usage = agent.get("usage")
    if usage is not None:
        require(isinstance(usage, dict) and all(type(usage.get(key)) is int and usage[key] >= 0 for key in USAGE_KEYS),
                "agent usage must count input/output/cache_read/cache_write")
    source = agent.get("usage_source")
    require(source is None or isinstance(source, str) and source in USAGE_SOURCES,
            "usage_source must be host or unobserved")
    require(usage is None or source is not None,
            "recorded agent usage requires usage_source (host or unobserved)")


def check_run_task(task):
    manifest = task.get("run_manifest")
    require(isinstance(manifest, dict), "run task needs run_manifest")
    require(isinstance(manifest.get("tool"), str) and bool(manifest["tool"].strip()), "manifest tool required")
    strings(manifest.get("command"), "manifest command", True)
    require(isinstance(manifest.get("run_id"), str) and bool(manifest["run_id"].strip()), "manifest run_id required")
    require(isinstance(manifest.get("run_dir"), str) and bool(manifest["run_dir"].strip()), "manifest run_dir required")
    relative_path(manifest["run_dir"])
    require(enum(manifest.get("launch"), {"dsh-job", "external"}), "manifest launch must be dsh-job or external")
    for field in ("config", "model_pin", "provider_pin", "input_freeze"):
        value = manifest.get(field)
        require(value is None or isinstance(value, str) and bool(value.strip()), f"manifest {field} must be a nonempty string or absent")
    authorization = task.get("authorization")
    require(isinstance(authorization, dict), "run task needs authorization")
    require(type(authorization.get("paid")) is bool, "authorization.paid must be boolean")
    if authorization["paid"]:
        for field in ("granted_by", "ref", "scope"):
            require(isinstance(authorization.get(field), str) and bool(authorization[field].strip()), f"paid run requires authorization.{field}")
    budget = authorization.get("budget")
    if budget is not None:
        require(isinstance(budget, dict), "authorization.budget must be an object")
        for field in ("max_calls", "max_cost"):
            value = budget.get(field)
            require(value is None or type(value) in (int, float) and value >= 0, f"budget {field} must be a nonnegative number")
        require(budget.get("max_calls") is not None or budget.get("max_cost") is not None, "budget needs max_calls or max_cost")
        if budget.get("max_cost") is not None:
            require(isinstance(budget.get("currency"), str) and bool(budget["currency"].strip()), "a cost budget requires currency")
    events = task.get("events", [])
    require(isinstance(events, list), "events must be a list")
    boundaries = fixes = 0
    stopped = False
    for event in events:
        require(isinstance(event, dict), "event must be an object")
        require(enum(event.get("type"), EVENT_TYPES), "invalid event type")
        timestamp(event.get("at"))
        require(isinstance(event.get("reason"), str) and bool(event["reason"].strip()), "event needs reason")
        require(isinstance(event.get("ref"), str) and bool(event["ref"].strip()), "event needs ref")
        if event["type"] == "boundary":
            boundaries += 1
            require(isinstance(event.get("prior_run_id"), str) and bool(event["prior_run_id"].strip()), "boundary needs prior_run_id")
            require(event["prior_run_id"].casefold() != manifest["run_id"].casefold(), "boundary must change run_id")
        elif event["type"] == "fixed":
            fixes += 1
        elif event["type"] == "stopped":
            stopped = True
        elif event["type"] == "resumed":
            require(stopped, "resumed requires a preceding stopped")
            stopped = False
    require(not stopped or task["status"] in {"RUNNING", "BLOCKED"}, "stopped run must be BLOCKED or resumed before REVIEW/ACCEPTING/DONE")
    require(task["attempt"] == boundaries + 1, "attempt must equal boundary count + 1")
    require(fixes <= task["attempt"], "at most one fixed event per attempt")
    stop_reason = task.get("stop_reason")
    require(stop_reason is None or isinstance(stop_reason, str) and bool(stop_reason.strip()),
            "stop_reason must be a nonempty string when present")


def validate(state):
    require(isinstance(state, dict) and type(state.get("schema")) is int and state["schema"] in (1, 2), "expected schema 1 or 2")
    schema = state["schema"]
    require(enum(state.get("phase"), PHASES), "invalid phase")
    require(isinstance(state.get("name"), str) and bool(ID.fullmatch(state["name"])), "invalid run name")
    require(isinstance(state.get("project_root"), str) and Path(state["project_root"]).is_absolute(), "project_root must be absolute")
    timestamp(state.get("created_at"))
    timestamp(state.get("updated_at"))
    limit = state.get("max_coders")
    require(type(limit) is int and 1 <= limit <= 8, "max_coders must be 1..8")
    agent_budget = state.get("max_agents")
    require(agent_budget is None or type(agent_budget) is int and 1 <= agent_budget <= 128,
            "max_agents must be 1..128 when present")
    review_budget = state.get("max_review_rounds")
    require(review_budget is None or type(review_budget) is int and 1 <= review_budget <= 8,
            "max_review_rounds must be 1..8 when present")
    concurrency_budget = state.get("max_concurrent_agents")
    require(concurrency_budget is None or type(concurrency_budget) is int and 1 <= concurrency_budget <= 32,
            "max_concurrent_agents must be 1..32 when present")
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
        rounds = task.get("max_review_rounds")
        require(rounds is None or type(rounds) is int and 1 <= rounds <= 8,
                "max_review_rounds must be 1..8 when present")
        planned = task.get("planned_agents")
        require(planned is None or type(planned) is int and 1 <= planned <= 128,
                "planned_agents must be 1..128 when present")
        kind = task.get("kind", "code")
        require(enum(kind, TASK_KINDS), f"invalid task kind: {key}")
        require(schema == 2 or kind == "code", "run tasks require schema 2")
        strings(task.get("criteria"), "criteria", True)
        strings(task.get("depends_on"), "dependencies")
        if kind == "code":
            for path in strings(task.get("files"), "files", True):
                relative_path(path)
            change = task.get("change_id")
            require(change is None or isinstance(change, str) and bool(DIGEST.fullmatch(change)), "change_id must be SHA-256 or null")
            if task["status"] in {"REVIEW", "ACCEPTING", "DONE"}:
                require(change is not None, "review requires change_id")
        else:
            require(task.get("files") is None, "run tasks use run_manifest, not files")
            require(task.get("change_id") is None, "run tasks use run_manifest, not change_id")
            check_run_task(task)
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
    for task in tasks:
        if task["status"] in {"RUNNING", "REVIEW", "ACCEPTING", "DONE"}:
            require(all(by_id[x]["status"] == "DONE" for x in task["depends_on"]), "unfinished dependency")
    claims = {}
    for task in tasks:
        if task["status"] in {"RUNNING", "REVIEW", "ACCEPTING"}:
            for path in (task.get("files") or []):
                # Windows is case-insensitive; use the same conservative rule everywhere.
                key = path.casefold()
                require(key not in claims, f"file ownership collision: {path}")
                claims[key] = task["id"]
        if task.get("kind", "code") == "run" and task["status"] != "TODO":
            key = task["run_manifest"]["run_dir"].casefold()
            require(key not in claims, f"run_dir ownership collision: {task['run_manifest']['run_dir']}")
            claims[key] = task["id"]
    claimed = list(claims)
    for index, first in enumerate(claimed):
        for second in claimed[index + 1:]:
            require(not first.startswith(second + "/") and not second.startswith(first + "/"),
                    f"ownership overlap: {claims[first]} vs {claims[second]}")
    agent_ids = set()
    for agent in agents:
        require(isinstance(agent, dict), "agent must be an object")
        key = agent.get("id")
        require(isinstance(key, str) and bool(key.strip()) and key not in agent_ids, "invalid or duplicate agent id")
        agent_ids.add(key)
        provider, model = agent.get("provider"), agent.get("model")
        require(isinstance(provider, str) and isinstance(model, str) and (provider, model) in ALLOWED_MODELS, "forbidden agent model")
        require(enum(agent.get("role"), ROLES), "invalid agent role")
        require(enum(agent.get("access", "write"), AGENT_ACCESS), "agent access must be read or write")
        require(enum(agent.get("status"), {"running", "inactive", "stopped"}), "invalid agent status")
        timestamp(agent.get("last_event_at"))
        check_agent_accounting(agent)
        require(agent.get("task") is None or isinstance(agent["task"], str) and agent["task"] in by_id, "unknown agent task")
    for task in tasks:
        run_task = task.get("kind", "code") == "run"
        pairs = (("operator_id", "run-operator"), ("reviewer_id", "reviewer"),
                 ("checker_id", "artifact-verifier"), ("auditor_id", "usage-auditor")) if run_task else \
                (("coder_id", "coder"), ("reviewer_id", "reviewer"), ("checker_id", "checker"))
        for field, role in pairs:
            key = task.get(field)
            if key is not None:
                agent = next((a for a in agents if a["id"] == key), None)
                require(agent is not None and agent["role"] == role and agent.get("task") == task["id"], f"invalid {field} binding")
        bound = [task.get(f) for f, _ in pairs if task.get(f) is not None]
        require(len(bound) == len(set(bound)), "bound agents must be independent")
        if task["status"] in {"RUNNING", "REVIEW", "ACCEPTING", "DONE"}:
            require(task.get("operator_id" if run_task else "coder_id") is not None,
                    "started run task needs operator_id" if run_task else "started task needs coder_id")
        if task["status"] in {"REVIEW", "ACCEPTING", "DONE"}:
            require(task.get("reviewer_id") is not None, "review requires reviewer_id")
        if task["status"] in {"ACCEPTING", "DONE"}:
            require(task.get("checker_id") is not None, "acceptance requires checker_id")
        if run_task and task["authorization"]["paid"] and task["status"] == "DONE":
            require(task.get("auditor_id") is not None, "paid DONE requires auditor_id")
    active_coders = sum(a["status"] == "running" and a["role"] == "coder" for a in agents)
    require(active_coders <= limit, "max_coders exceeded")
    job_ids = set()
    for job in jobs:
        require(isinstance(job, dict) and isinstance(job.get("id"), str) and bool(job["id"].strip()) and job["id"] not in job_ids, "invalid or duplicate job id")
        job_ids.add(job["id"])
        require(enum(job.get("status"), {"running", "collected", "killed"}), "invalid job status")
        require(isinstance(job.get("purpose"), str) and bool(job["purpose"].strip()), "empty job purpose")
        require(job.get("task") is None or job["task"] in by_id, "unknown job task binding")
    for task in tasks:
        if task.get("kind", "code") != "run" or task["status"] == "TODO":
            continue
        bound_jobs = [job for job in jobs if job.get("task") == task["id"]]
        if task["run_manifest"]["launch"] == "dsh-job":
            if task["status"] == "RUNNING":
                require(any(job["status"] in {"running", "collected"} for job in bound_jobs),
                        "dsh-job run needs a bound running/collected job while RUNNING")
            else:
                require(any(job["status"] in {"collected", "killed"} for job in bound_jobs),
                        "dsh-job run past RUNNING needs a bound collected/killed job")
        else:
            require(not bound_jobs, "external run must not bind jobs")
    if state["phase"] == "complete":
        require(bool(tasks) and all(t["status"] == "DONE" for t in tasks), "complete run has unfinished tasks")
        require(not any(a["status"] == "running" for a in agents), "complete run has running agents")
        require(not any(j["status"] == "running" for j in jobs), "complete run has uncollected jobs")
    return state


def load(run):
    raw = inside(run, "RUN.json").read_bytes()
    return validate(json.loads(raw)), hashlib.sha256(raw).hexdigest()


RUN_REPORT_NAMES = {"run_integrity": "run-integrity.json", "acceptance": "artifact-verification.json", "usage_audit": "usage-audit.json"}


def report_path(task, kind):
    name = RUN_REPORT_NAMES.get(kind, f"{kind}.json") if task.get("kind", "code") == "run" else f"{kind}.json"
    return f"reports/{task['id']}/attempt-{task['attempt']}/{name}"


def check_report(run, task, kind):
    path = inside(run, report_path(task, kind))
    require(path.is_file(), f"missing {kind} report")
    report = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(report, dict), "report must be an object")
    require(report.get("kind") == kind and report.get("task") == task["id"] and type(report.get("attempt")) is int and report["attempt"] == task["attempt"], "report identity mismatch")
    if task.get("kind", "code") == "run":
        require(report.get("run_id") == task["run_manifest"]["run_id"], "stale report run_id")
        actor = {"run_integrity": task.get("reviewer_id"), "acceptance": task.get("checker_id")}[kind]
    else:
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


def check_usage_audit(run, task):
    path = inside(run, report_path(task, "usage_audit"))
    require(path.is_file(), "missing usage_audit report")
    report = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(report, dict), "report must be an object")
    require(report.get("kind") == "usage_audit" and report.get("task") == task["id"] and type(report.get("attempt")) is int and report["attempt"] == task["attempt"], "report identity mismatch")
    require(report.get("run_id") == task["run_manifest"]["run_id"], "stale report run_id")
    require(task.get("auditor_id") is not None and report.get("agent_id") == task["auditor_id"], "report agent mismatch")
    require(type(report.get("calls_total")) is int and report["calls_total"] >= 0, "calls_total must be a nonnegative int")
    stages = report.get("by_stage")
    require(isinstance(stages, list) and bool(stages) and all(isinstance(s, dict) and isinstance(s.get("stage"), str) and bool(s["stage"].strip()) and type(s.get("calls")) is int and s["calls"] >= 0 for s in stages), "by_stage must list {stage, calls}")
    tokens = report.get("tokens")
    require(isinstance(tokens, dict) and all(type(tokens.get(k)) is int and tokens[k] >= 0 for k in ("input", "output", "cache_read", "cache_write")), "tokens must count input/output/cache_read/cache_write")
    require(isinstance(report.get("unknown_usage_calls"), list) and all(isinstance(x, str) and bool(x.strip()) for x in report["unknown_usage_calls"]), "unknown_usage_calls must be a list of strings")
    require(isinstance(report.get("reconcile"), list) and bool(report["reconcile"]) and all(isinstance(x, dict) and isinstance(x.get("source"), str) and bool(x["source"].strip()) and isinstance(x.get("note"), str) and bool(x["note"].strip()) for x in report["reconcile"]), "reconcile must list {source, note}")
    estimate = report.get("cost_estimate")
    require(isinstance(estimate, dict) and isinstance(estimate.get("tariffs_ref"), str) and bool(estimate["tariffs_ref"].strip()) and isinstance(estimate.get("total"), (int, float)) and estimate["total"] >= 0 and isinstance(estimate.get("currency"), str) and bool(estimate["currency"].strip()), "cost_estimate must be {tariffs_ref, total, currency}")
    require(estimate["note"] == "estimate, not invoice", "cost_estimate note must say estimate, not invoice")
    return report


def check_verification(run):
    report = json.loads(inside(run, "reports/verification.json").read_text(encoding="utf-8"))
    require(isinstance(report, dict) and report.get("verdict") == "PASS", "verification has not passed")
    checks = report.get("checks")
    require(isinstance(checks, list) and bool(checks), "verification needs checks")
    for check in checks:
        require(isinstance(check, dict) and check.get("result") == "PASS" and isinstance(check.get("evidence"), str) and bool(check["evidence"].strip()), "verification check failed or has no evidence")
    return report


FREEZE_NAME = re.compile(r"([A-Za-z0-9_.\-/]+\.json)")
HASH_LIMIT = 5000


def _hashed_entries(node, found, limit=HASH_LIMIT):
    """Collect (path, digest) pairs from a project freeze manifest of any nesting."""
    if len(found) >= limit:
        return found
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str) and isinstance(value, str) and DIGEST.fullmatch(value) and ("/" in key or "\\" in key):
                found.append((key, value))
            else:
                _hashed_entries(value, found, limit)
    elif isinstance(node, list):
        for value in node:
            _hashed_entries(value, found, limit)
    return found


def verify_freeze(run, name, project, limit=HASH_LIMIT):
    path = inside(run, name)
    if not path.is_file():
        return {"source": name, "error": "manifest not found in the run directory"}
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"source": name, "error": f"unreadable manifest: {exc}"}
    entries = _hashed_entries(manifest, [], limit)
    if not entries:
        return {"source": name, "error": "no path/digest pairs found; hashing format is project-specific"}
    missing, mismatched, unreadable, matched = [], [], [], 0
    for raw, digest in entries[:limit]:
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = project / raw
        try:
            plain_path(candidate)
            data = candidate.read_bytes()
        except (Invalid, OSError):
            unreadable.append(raw)
            continue
        if hashlib.sha256(data).hexdigest() == digest:
            matched += 1
        else:
            mismatched.append(raw)
    def brief(items):
        return {"count": len(items), "names": items[:10]}
    return {"source": name, "listed": len(entries), "matched": matched, "missing": brief(missing),
            "mismatched": brief(mismatched), "unreadable": brief(unreadable)}


def postrun_check(run, hashes=(), limit=HASH_LIMIT):
    """Deterministic post-run evidence for stopped runs: freeze hashes, run_dir contents, stop events.

    Replaces an LLM auditor only for mechanical checks. This is a draft of facts, not a
    gate and not an acceptance report; a human or reviewer still decides validity.
    """
    state, revision = load(run)
    project = plain_path(state["project_root"])
    require(project.is_dir(), "project_root does not exist")
    frozen = {}
    tasks = []
    for task in state["tasks"]:
        if task.get("kind", "code") != "run":
            continue
        manifest = task["run_manifest"]
        events = task.get("events", [])
        entry = {"task": task["id"], "status": task["status"], "run_id": manifest["run_id"],
                 "events": {name: sum(e["type"] == name for e in events) for name in sorted(EVENT_TYPES)},
                 "audit_reports": {kind: inside(run, report_path(task, kind)).is_file()
                                   for kind in ("run_integrity", "acceptance", "usage_audit")}}
        stop_reason = task.get("stop_reason")
        entry["stop_reason_recorded"] = isinstance(stop_reason, str) and bool(stop_reason.strip())
        directory = inside(project, manifest["run_dir"])
        if directory.is_dir():
            files = [p for p in directory.rglob("*") if p.is_file()]
            entry["run_dir"] = {"path": manifest["run_dir"], "files": len(files),
                                "bytes": sum(p.stat().st_size for p in files),
                                "top_level": sorted(p.name for p in directory.iterdir())[:20]}
        else:
            entry["run_dir"] = {"path": manifest["run_dir"], "exists": False}
        names = list(hashes) or FREEZE_NAME.findall(manifest.get("input_freeze") or "")
        entry["input_freeze"] = [frozen.setdefault(name, verify_freeze(run, name, project, limit)) for name in names]
        tasks.append(entry)
    return {"version": VERSION, "revision": revision, "draft": True, "deterministic": True,
            "gate_validation": "not_run", "tasks": tasks,
            "note": "Mechanical evidence for a stopped run. Hashes prove files are unchanged since the freeze; "
                    "they do not prove the run was valid. Project metric recomputation stays a project script."}


def budget_findings(task, report):
    """Advisory comparison of a valid usage audit against authorization.budget.

    Never a gate: a run that overspent is a finding for the user, not an invalid state.
    """
    budget = (task.get("authorization") or {}).get("budget")
    if not isinstance(budget, dict):
        return []
    findings = []
    calls = report.get("calls_total")
    if isinstance(budget.get("max_calls"), (int, float)) and isinstance(calls, int) and calls > budget["max_calls"]:
        findings.append(f"{calls} calls over budget {budget['max_calls']}")
    estimate = report.get("cost_estimate")
    cost = estimate.get("total") if isinstance(estimate, dict) else None
    if isinstance(budget.get("max_cost"), (int, float)) and isinstance(cost, (int, float)) and cost > budget["max_cost"]:
        findings.append(f"estimate {cost} {estimate.get('currency')} over budget {budget['max_cost']} {budget.get('currency')}")
    return findings


def tick(run, now=None):
    state, revision = load(run)
    now = now or datetime.now(timezone.utc)
    actions = []

    def add(priority, kind, detail, ref="RUN.json"):
        actions.append({"priority": priority, "kind": kind, "detail": detail, "ref": ref})

    tasks = {t["id"]: t for t in state["tasks"]}
    review_budget = state.get("max_review_rounds")
    for task in tasks.values():
        status = task["status"]
        if status == "BLOCKED":
            add("P1", "blocked", task["id"])
        planned = task.get("planned_agents")
        if planned is not None:
            spent = sum(1 for a in state["agents"] if a.get("task") == task["id"])
            if spent > planned:
                add("P1", "fanout_planned_exceeded",
                    f"{task['id']}: {spent} agents over planned {planned}; the plan was not the budget")
        writer_id = task.get("operator_id") if task.get("kind", "code") == "run" else task.get("coder_id")
        writer = next((a for a in state["agents"] if a["id"] == writer_id), None)
        if writer is not None and writer.get("access") == "read":
            add("P0", "read_agent_owns_files",
                f"{task['id']}: writer agent {writer['id']} is read-only; only a write agent owns files")
        rounds = task.get("max_review_rounds", review_budget)
        if rounds is not None and task["attempt"] > rounds and status not in {"DONE", "BLOCKED"}:
            add("P1", "review_rounds_exceeded",
                f"{task['id']}: attempt {task['attempt']} over {rounds}; probe the real call graph, list defect classes, then ask the user")
        run_task = task.get("kind", "code") == "run"
        review_kind = "run_integrity" if run_task else "review"
        kinds = []
        if status in {"ACCEPTING", "DONE"}:
            kinds.append(review_kind)
        if status == "DONE":
            kinds.append("acceptance")
        if status in {"REVIEW", "ACCEPTING"}:
            kind = review_kind if status == "REVIEW" else "acceptance"
            if inside(run, report_path(task, kind)).exists():
                kinds.append(kind)
        for kind in kinds:
            try:
                report = check_report(run, task, kind)
                if report["verdict"] != "PASS":
                    add("P0" if status == "DONE" or (kind == review_kind and status == "ACCEPTING") else "P1", "report_failed", task["id"], report_path(task, kind))
                elif status == "REVIEW" or (status == "ACCEPTING" and kind == "acceptance"):
                    add("P1", "report_ready", f"{task['id']} {kind}", report_path(task, kind))
            except (Invalid, OSError, ValueError) as exc:
                add("P0", "invalid_report", f"{task['id']}: {exc}", report_path(task, kind))
        if run_task:
            if status == "DONE" and task["authorization"]["paid"]:
                try:
                    audited = check_usage_audit(run, task)
                except (Invalid, OSError, ValueError) as exc:
                    add("P0", "invalid_report", f"{task['id']}: {exc}", report_path(task, "usage_audit"))
                else:
                    for finding in budget_findings(task, audited):
                        add("P1", "budget_exceeded", f"{task['id']}: {finding}", report_path(task, "usage_audit"))
            if status == "RUNNING":
                collected = any(job.get("task") == task["id"] and job["status"] == "collected" for job in state["jobs"])
                if collected and not inside(run, report_path(task, "run_integrity")).exists():
                    add("P1", "collect_pending", f"{task['id']}: collected job without run-integrity report")
    if state["phase"] == "implementation":
        occupied = {p.casefold() for t in tasks.values() if t["status"] in {"RUNNING", "REVIEW", "ACCEPTING"} for p in (t.get("files") or [])}
        run_dirs = {t["run_manifest"]["run_dir"].casefold() for t in tasks.values() if t.get("kind", "code") == "run" and t["status"] != "TODO"}
        slots = state["max_coders"] - sum(a["role"] == "coder" and a["status"] == "running" for a in state["agents"])
        for task in tasks.values():
            if slots <= 0 or task["status"] != "TODO" or not all(tasks[d]["status"] == "DONE" for d in task["depends_on"]):
                continue
            if task.get("kind", "code") == "run":
                key = task["run_manifest"]["run_dir"].casefold()
                if key not in run_dirs and not any(key.startswith(rd + "/") or rd.startswith(key + "/") for rd in run_dirs):
                    add("P1", "task_ready", task["id"])
                    slots -= 1
            elif not any(p.casefold() in occupied or any(p.casefold().startswith(rd + "/") for rd in run_dirs) for p in task["files"]):
                add("P1", "task_ready", task["id"])
                occupied.update(p.casefold() for p in task["files"])
                slots -= 1
        if tasks and all(t["status"] == "DONE" for t in tasks.values()):
            add("P1", "verification_needed", "all tasks accepted")
    for agent in state["agents"]:
        age = (now - timestamp(agent["last_event_at"])).total_seconds()
        if agent["status"] == "running" and age >= 1800:
            add("P1", "check_agent", f"{agent['id']}: no recorded event for {int(age)}s; verify native status, not proof of death")
    agent_budget = state.get("max_agents")
    if agent_budget is not None and len(state["agents"]) > agent_budget:
        add("P1", "agent_budget_exceeded",
            f"{len(state['agents'])} agents recorded over budget {agent_budget}; more agents are not cheaper or faster")
    concurrency_budget = state.get("max_concurrent_agents")
    if concurrency_budget is not None:
        live = sum(1 for a in state["agents"] if a["status"] == "running")
        if live > concurrency_budget:
            add("P1", "concurrency_exceeded",
                f"{live} agents running over concurrency budget {concurrency_budget}; parallel reads are cheap, parallel writers are not")
    if state["phase"] == "complete":
        try:
            check_verification(run)
        except (Invalid, OSError, ValueError) as exc:
            add("P0", "invalid_verification", str(exc), "reports/verification.json")
    actions.sort(key=lambda a: a["priority"])
    return {"version": VERSION, "revision": revision, "phase": state["phase"], "actions": actions or [{"priority": "P4", "kind": "quiet", "detail": "no action", "ref": "RUN.json"}]}


def plan(run):
    """Read-only fan-out plan check: sum the per-task plans and compare to the ceiling.

    A ceiling that is only checked after the spend is an after-the-fact finding, not a
    plan. This makes the shortfall visible before the first spawn.
    """
    state, revision = load(run)
    tasks = state["tasks"]
    declared = state.get("max_agents")
    planned = sum(t.get("planned_agents") or 0 for t in tasks)
    unplanned = sorted(t["id"] for t in tasks if t.get("planned_agents") is None)
    writers = sum(1 for t in tasks if t.get("kind", "code") == "code")
    return {"version": VERSION, "revision": revision, "complexity": state.get("complexity"),
            "max_agents": declared, "max_concurrent_agents": state.get("max_concurrent_agents"),
            "max_review_rounds": state.get("max_review_rounds"),
            "planned_agents": planned, "code_tasks": writers, "tasks_without_plan": unplanned,
            "within_budget": declared is None or planned <= declared,
            "read_only_roles": ["researcher", "reviewer", "checker", "verifier", "usage-auditor", "artifact-verifier"]}


def initialize(project, name, complexity="SIMPLE"):
    require(bool(ID.fullmatch(name)), "invalid run name")
    require(enum(complexity, set(COMPLEXITY_DEFAULTS)), "invalid complexity")
    project = plain_path(project)
    require(project.is_dir(), "project directory does not exist")
    run = inside(project, f"RUNS/feature-{name}")
    legacy = inside(project, f"SUPERTICK/feature-{name}")
    require(not run.exists() and not legacy.exists(), "run already exists; choose resume or another name")
    run.mkdir(parents=True, exist_ok=False)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    budgets = COMPLEXITY_DEFAULTS[complexity]
    state = {"schema": 2, "name": name, "project_root": str(project), "phase": "planning",
             "complexity": complexity, "max_coders": 2, "max_agents": budgets["max_agents"],
             "max_review_rounds": budgets["max_review_rounds"],
             "max_concurrent_agents": budgets["max_concurrent_agents"],
             "created_at": now, "updated_at": now, "tasks": [], "agents": [], "jobs": []}
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


TOKEN_ALIASES = {
    "input": ("input", "input_tokens", "prompt_tokens"),
    "output": ("output", "output_tokens", "completion_tokens"),
    "cache_read": ("cache_read", "cache_read_input_tokens", "prompt_cache_hit_tokens"),
    "cache_write": ("cache_write", "cache_write_input_tokens", "prompt_cache_miss_tokens"),
}
STAGE_KEYS = ("stage", "phase", "kind", "name")


def _token_counts(node):
    return {key: sum(int(node[name]) for name in aliases if isinstance(node.get(name), (int, float)))
            for key, aliases in TOKEN_ALIASES.items()}


def _merge_counts(total, counts):
    for key, value in counts.items():
        total[key] = total.get(key, 0) + value


def _find_usage(node):
    if isinstance(node, dict):
        if any(isinstance(node.get(name), (int, float)) for aliases in TOKEN_ALIASES.values() for name in aliases):
            return node
        for value in node.values():
            found = _find_usage(value)
            if found is not None:
                return found
    return None


def usage_audit(run, jsonl_names=(), usage_names=(), summary_names=(), tariffs_path=None):
    """Aggregate run usage artifacts into a draft usage_audit report (read-only; prints to stdout).

    Sources measure the same calls, so totals are never summed across them: the draft
    takes tokens and calls_total from the most granular source available (jsonl, then
    usage, then summary); every named source lands in reconcile with its own totals.
    """
    require(bool(jsonl_names) or bool(usage_names) or bool(summary_names), "name at least one artifact")
    plain_path(run)
    unknown, reconcile, by_stage = [], [], {}
    entries = []
    jsonl_tokens = {key: 0 for key in TOKEN_ALIASES}
    jsonl_calls = 0

    def source_note(counts, calls):
        recorded = f"{calls} calls" if calls is not None else "tokens only"
        return f"{recorded}; in={counts.get('input', 0)} out={counts.get('output', 0)}"

    for name in jsonl_names:
        path = inside(run, name)
        require(path.is_file(), f"missing jsonl artifact: {name}")
        source_calls = 0
        counts = {key: 0 for key in TOKEN_ALIASES}
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except ValueError as exc:
                raise Invalid(f"{name}:{number}: invalid JSON: {exc}") from exc
            require(isinstance(entry, dict), f"{name}:{number}: line must be an object")
            entries.append((name, number, entry))
            usage = entry.get("usage") if isinstance(entry.get("usage"), dict) else _find_usage(entry)
            if usage is None:
                unknown.append(f"{name}:{number}: no usage fields")
                continue
            stage = next((entry[key] for key in STAGE_KEYS if isinstance(entry.get(key), str) and entry[key].strip()), "unknown")
            by_stage[stage] = by_stage.get(stage, 0) + 1
            _merge_counts(counts, _token_counts(usage))
            source_calls += 1
        _merge_counts(jsonl_tokens, counts)
        jsonl_calls += source_calls
        reconcile.append({"source": name, "note": source_note(counts, source_calls)})
    aggregate = None
    for label, names in (("usage", usage_names), ("summary", summary_names)):
        for name in names:
            path = inside(run, name)
            require(path.is_file(), f"missing {label} artifact: {name}")
            usage = _find_usage(json.loads(path.read_text(encoding="utf-8")))
            require(usage is not None, f"{name}: no usage fields")
            counts = _token_counts(usage)
            recorded = usage.get("calls_total") or usage.get("total_calls") or usage.get("calls")
            calls = int(recorded) if isinstance(recorded, (int, float)) else None
            reconcile.append({"source": name, "note": source_note(counts, calls)})
            if aggregate is None:
                aggregate = (dict(counts), calls)
    if jsonl_names:
        tokens, calls_total = jsonl_tokens, jsonl_calls
    elif aggregate is not None:
        tokens, calls_total = aggregate
    else:
        tokens, calls_total = {key: 0 for key in TOKEN_ALIASES}, 0
    if not by_stage:
        by_stage = {"unattributed": calls_total}
    draft = {"kind": "usage_audit", "draft": True, "calls_total": calls_total,
             "by_stage": [{"stage": stage, "calls": calls} for stage, calls in sorted(by_stage.items())],
             "tokens": tokens, "unknown_usage_calls": unknown, "reconcile": reconcile}
    if tariffs_path:
        require(bool(entries), "tariffs require a jsonl artifact with usage lines")
        tariffs = json.loads(Path(tariffs_path).read_text(encoding="utf-8"))
        require(isinstance(tariffs, dict) and isinstance(tariffs.get("models"), dict), "tariffs must map models to rates")
        total = 0.0
        for name, number, entry in entries:
            usage = entry.get("usage") if isinstance(entry.get("usage"), dict) else _find_usage(entry)
            if usage is None:
                continue
            model = entry.get("model")
            rates = tariffs["models"].get(model) if isinstance(model, str) else None
            if not isinstance(rates, dict):
                unknown.append(f"{name}:{number}: no tariff for model {model!r}")
                continue
            counts = _token_counts(usage)
            total += sum(counts[key] / 1_000_000 * float(rates.get(key, 0)) for key in TOKEN_ALIASES)
        draft["unknown_usage_calls"] = unknown
        draft["cost_estimate"] = {"tariffs_ref": str(tariffs_path), "total": round(total, 6),
                                  "currency": str(tariffs.get("currency", "USD")), "note": "estimate, not invoice"}
    return draft


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("project")
    init.add_argument("name")
    init.add_argument("--complexity", choices=sorted(COMPLEXITY_DEFAULTS), default="SIMPLE",
                      help="fan-out tier the run starts from (default SIMPLE)")
    audit = commands.add_parser("usage-audit", help="aggregate run usage artifacts into a draft usage_audit report")
    audit.add_argument("run")
    audit.add_argument("--jsonl", action="append", default=[], help="JSONL artifact name inside the run dir (repeatable)")
    audit.add_argument("--usage", action="append", default=[], help="usage JSON artifact name inside the run dir (repeatable)")
    audit.add_argument("--summary", action="append", default=[], help="summary JSON artifact name inside the run dir (repeatable)")
    audit.add_argument("--tariffs", help="optional tariffs JSON file path")
    postrun = commands.add_parser("postrun-check", help="deterministic post-run evidence for stopped runs")
    postrun.add_argument("run")
    postrun.add_argument("--hashes", action="append", default=[], help="freeze manifest name inside the run dir (repeatable; default: parsed from input_freeze)")
    postrun.add_argument("--limit", type=int, default=HASH_LIMIT, help=f"maximum freeze entries to hash (default {HASH_LIMIT})")
    for command in ("tick", "wait", "plan"):
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
            result = {"run": str(initialize(args.project, args.name, args.complexity))}
        elif args.command == "tick":
            result = tick(args.run)
        elif args.command == "plan":
            result = plan(args.run)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["within_budget"] else 2
        elif args.command == "usage-audit":
            result = usage_audit(args.run, args.jsonl, args.usage, args.summary, args.tariffs)
        elif args.command == "postrun-check":
            require(0 < args.limit <= HASH_LIMIT, f"limit must be 1..{HASH_LIMIT}")
            result = postrun_check(args.run, tuple(args.hashes), args.limit)
        else:
            result = wait(args.run, args.interval, args.max, args.wake_on)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (Invalid, OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
