# State contract (schema 2)

Paths below are relative to one RUNS/feature-<slug> run (legacy SUPERTICK/feature-<slug> run directories remain valid for tick/wait). The lead is the only
writer of RUN.json and PLAN.MD; agents write their own attempt-local reports.
Do not edit RUN.json via a shell while another writer is active. Use inspect/stat
and the returned SHA-256 revision in a CAS file-tool edit. Preserve earlier reports.

## RUN.json

`supervisor.py init` creates an empty planning run. Schema is 2; schema 1 states
(code tasks only) stay valid, but any state containing a run task must use schema 2.
Extend that object, not a second
status file. All keys shown below are mandatory except null bindings/change_id may
be absent in TODO. Timestamps are ISO 8601 with a timezone. Store actual native IDs.

```json
{
  "schema": 2,
  "name": "example",
  "project_root": "C:/work/example",
  "created_at": "2026-01-01T00:00:00Z",
  "updated_at": "2026-01-01T00:00:00Z",
  "phase": "implementation",
  "complexity": "SIMPLE",
  "max_coders": 2,
  "max_agents": 12,
  "max_review_rounds": 3,
  "tasks": [
    {
      "id": "task-1",
      "title": "Reject invalid input",
      "status": "TODO",
      "attempt": 1,
      "files": ["src/input.py", "tests/test_input.py"],
      "criteria": ["An empty input returns a validation error"],
      "depends_on": [],
      "coder_id": null,
      "reviewer_id": null,
      "checker_id": null,
      "change_id": null
    },
    {
      "id": "run-1",
      "kind": "run",
      "title": "Evaluate judge mesh on frozen inputs",
      "status": "TODO",
      "attempt": 1,
      "criteria": ["Every input unit has a scored output"],
      "depends_on": ["task-1"],
      "run_manifest": {
        "tool": "toolx",
        "command": ["toolx", "--config", "cfg.yaml", "--run-id", "run-a"],
        "run_id": "run-a",
        "run_dir": "results/toolx/run-a",
        "launch": "dsh-job",
        "input_freeze": "inputs at commit 0123abcd; SHA-256 manifest committed before launch"
      },
      "authorization": {"paid": false},
      "events": [],
      "max_review_rounds": 3,
      "operator_id": null,
      "reviewer_id": null,
      "checker_id": null,
      "auditor_id": null
    }
  ],
  "agents": [],
  "jobs": []
}
```

`phase`: planning → implementation → verification → complete. A failed check may
return to implementation. Task transitions: TODO → RUNNING → REVIEW → ACCEPTING
→ DONE. BLOCKED is explicit intervention; do not erase a blocker by pretending a
notification was success. Returning to RUNNING after a failed gate increments
attempt, resets change_id/reviewer_id/checker_id and produces a fresh snapshot.

Started tasks need coder_id, REVIEW needs reviewer_id/change_id, ACCEPTING needs
checker_id and a PASS review, DONE needs both passing reports. Agents are recorded
as objects with `id`, `provider`, `model`, `role`, `task`, `status`, `last_event_at`:

```json
{
  "id": "actual-native-child-id",
  "provider": "<from ALLOWED-MODELS.json>",
  "model": "<from ALLOWED-MODELS.json>",
  "role": "coder",
  "task": "task-1",
  "status": "running",
  "last_event_at": "2026-01-01T00:00:00Z"
}
```

Agent status: running/inactive/stopped. Roles: researcher, architect, tech-lead,
risk-tester, coder, reviewer, checker, verifier, run-operator, usage-auditor,
artifact-verifier. Planning/verifier task may be null.
Only provider/model pairs from references/ALLOWED-MODELS.json are allowed. No status derives from
an invented ID, file existence alone, or an interruption acknowledgement.

Agent objects may carry optional `started_at`, `ended_at`, `usage`
(`{input, output, cache_read, cache_write}`) and `usage_source` (`host` or
`unobserved`). These describe the child's own session cost as the host reports it.
They are never required: a host that does not expose usage leaves them out, and a
recorded `usage` must name its source — mark it `unobserved` when the numbers are
an estimate rather than a host report. Never infer usage from memory or from the
lead's own counters.

## Agent budget

The run records its own ceiling on fan-out. `max_agents` (1..128, optional for
backward compatibility) is the total number of agents the run may record;
`max_review_rounds` (1..8, optional) is the default ceiling on review attempts per
task. `init` writes 12 and 3. A task may override the round ceiling with its own
`max_review_rounds`. Exceeding a ceiling is reported as `agent_budget_exceeded` or
`review_rounds_exceeded` (P1), never as an invalid state: the numbers describe a
run that has spent more than planned, which is a user decision, not a parse error.

Both ceilings are advisory gates for the lead. When a task crosses its round
ceiling, the next attempt must probe the real call graph and list defect classes
rather than returning another single-instance finding, and the lead asks the user
instead of starting an unbounded review loop. `max_coders` still limits coder
concurrency; these fields limit total spend over the whole run.

Jobs: `{"id":"actual-job-id","purpose":"integration tests","status":"running"}`.
Status is running/collected/killed; collected means the owner obtained its result,
not necessarily success. A job may carry `"task": "run-1"` binding it to a run task
(the task id must exist). Child-owned jobs must be collected by that child, whose
report includes exit codes. Lead cannot assume it owns every child's job.

Exact file ownership is case-insensitive even on Linux to catch Windows collisions.
Paths use `/` and are relative to project_root (no directories/globs, `..`, drive
prefixes, reserved Windows devices, symlinks or junctions). Lead also verifies real
project files and hardlink aliases: the validator checks path syntax and state,
not filesystem aliases or real code modifications. Untouched untracked files are
not permission to overwrite them. A task may claim more files only after the lead
serializes and records the new ownership.

## Run tasks (kind = run)

A run task holds one long external process (eval suite, build, migration, batch
job — anything with its own run directory). It replaces `files`/`change_id` with a
`run_manifest`; the validator rejects files/change_id on run tasks and rejects run
tasks in schema 1 states.

`run_manifest` fields:

- `tool`: tool name, for reports and humans.
- `command`: argv list (no shell string) exactly as launched.
- `run_id`: the tool's own run identifier (its resume/continuity semantics belong to
  the tool; record them in `input_freeze` or the plan when they matter).
- `run_dir`: output directory relative to project_root, `/` separators. Exclusive,
  case-insensitive ownership: no two non-TODO run tasks may claim the same or
  overlapping (prefix) run_dir, and no code task may claim files inside it.
- `launch`: `"dsh-job"` (a managed background job the operator owns) or `"external"`
  (launched outside this session; bound jobs are then forbidden).
- optional `model_pin`/`provider_pin` (string): pin the tested tool's model, so a
  model swap cannot silently invalidate a comparison.
- optional `input_freeze` (string): how inputs were frozen (commit, hash manifest).

`authorization`: `{"paid": bool}` plus, when paid, nonempty `granted_by`, `ref` and
`scope`. `ref` is the user's explicit GO message for the paid calls — delegation is
never GO. The supervisor only checks presence; it never grants or implies consent.
Paid DONE additionally requires an `auditor_id` binding and a valid usage-audit
report. Unpaid runs need no usage audit (one may still be written voluntarily).

Optional `authorization.budget`: `{"max_calls": n, "max_cost": x, "currency": "USD"}`.
At least one of `max_calls`/`max_cost` is required when the block is present, and a
cost ceiling requires a currency. The usage auditor compares actuals against it and
the supervisor reports `budget_exceeded` (P1) — an overrun is a finding for the user,
never an invalid state. An optional `stop_reason` string records, in the task itself,
why a STOPPED or BLOCKED run did not complete; the supervisor checks its type only.

`events`: append-only list of `{"type": ..., "at": ..., "reason": ..., "ref": ...}`
with type one of stop_requested, stopped, resumed, fixed, boundary. `resumed`
requires a preceding `stopped`. `boundary` records an attempt-changing break
(validity-affecting fix, lost job, new GO) and must carry `prior_run_id` differing
from the current `run_id`; `attempt` equals the number of boundaries + 1. `fixed`
counts must not exceed the attempt. A task with a `stopped` event may only sit in
RUNNING or BLOCKED — stopping never completes a task.

Bindings: started runs need `operator_id` (role run-operator). REVIEW needs
`reviewer_id` (role reviewer); ACCEPTING needs `checker_id` (role artifact-verifier);
paid DONE needs `auditor_id` (role usage-auditor). Independence rules are the same
as for code tasks. For `launch: "dsh-job"`, RUNNING requires its bound job in status
running; any status past RUNNING requires collected or killed. Bound jobs may not
repeat across run tasks.

Run reports live under `reports/<task>/attempt-<n>/`:

- `run-integrity.json` (kind run_integrity, written by reviewer_id): same shape as
  review, but identity is `run_id`, not change_id. Verdict PASS requires the operator
  to have held the job to a settled exit, stop rules respected, and artifacts
  complete relative to the tool's own manifest.
- `artifact-verification.json` (kind acceptance, written by checker_id): covers every
  task criterion exactly once, verified against the artifacts themselves.
- `usage-audit.json` (kind usage_audit, written by auditor_id, paid DONE only):
  `{calls_total, by_stage: [{stage, calls}], tokens: {input, output, cache_read,
  cache_write}, unknown_usage_calls: [], reconcile: [{source, note}], cost_estimate:
  {tariffs_ref, total, currency, note: "estimate, not invoice"}}` with task/attempt/
  run_id/agent_id identity.

`supervisor.py postrun-check <run> [--hashes NAME] [--limit N]` produces deterministic
post-run evidence for stopped or blocked runs: it re-hashes the freeze manifest named
in `input_freeze` (or `--hashes`), counts the files actually present under each
`run_dir`, tallies stop/boundary events and notes which audit reports exist. It is a
draft of mechanical facts (`deterministic: true`, `gate_validation: "not_run"`), not
a gate and not an acceptance report: matching hashes prove inputs are unchanged since
the freeze, not that the run was valid. Use it instead of spawning auditor agents to
re-verify hashes and directory listings. Project-specific metric recomputation stays
a project script.

`supervisor.py usage-audit <run> --jsonl NAME --usage NAME --summary NAME
[--tariffs FILE]` aggregates usage artifacts into a read-only draft on stdout.
Sources measure the same calls, so totals are never summed across them: the draft
takes tokens and calls from the most granular source (jsonl, then usage, then
summary) and lists every source with its own totals in reconcile. Tariff estimates
require a jsonl call log; unknown models land in unknown_usage_calls. The draft is
input for the auditor's report, not a report itself.

## Immutable review input and reports

Store exact snapshot bytes as `reports/<task>/attempt-<n>/CHANGE.diff` (Git) or
`CHANGE.json` with before/after hashes and content/diff (without Git). Include
untracked owned files; `git diff` alone omits them. Snapshot must describe the task,
not the entire dirty worktree. Lead calculates SHA-256 of those bytes into change_id.
The lead/checker must separately compare current owned file contents with snapshot
outputs before DONE. Do not trust a coder-supplied digest as a freshness check.

One report per gate per attempt. If a worker returns invalid JSON, request a repair
of its own report, not a silent PASS. Report JSON (review or acceptance):

```json
{
  "kind": "acceptance",
  "task": "task-1",
  "attempt": 1,
  "change_id": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "agent_id": "actual-checker-id",
  "verdict": "PASS",
  "summary": "Empty input is rejected by the public entry point",
  "checks": [
    {
      "criterion": "An empty input returns a validation error",
      "result": "PASS",
      "evidence": "python -m unittest tests.test_input: exit 0, 3 tests; empty-input assertion passed"
    }
  ]
}
```

Review `kind` = review at `reports/<id>/attempt-<n>/review.json`. Acceptance kind =
acceptance at `reports/<id>/attempt-<n>/acceptance.json`. Each must identify its bound
agent, attempt and change exactly. Acceptance lists every task criterion exactly
once. All checks need nonempty evidence and result PASS/FAIL/NOT_RUN. Any FAIL or
NOT_RUN forbids a PASS verdict. Read the actual command result, not just its label.

The lead writes `reports/verification.json` after integrated checks:

```json
{
  "verdict": "PASS",
  "checks": [
    {"name": "integration", "result": "PASS", "evidence": "actual command, exit code and relevant outcome"}
  ]
}
```

This validator enforces structure, identity, dependency/ownership constraints and
reported gates. It **does not** execute checks, inspect Git, authenticate report
authorship, enforce historical transition order or prove freshness/content safety.
It is a local consistency aid, not a trusted execution or security boundary.
A final verification report must be regenerated after any product edit; its
freshness is the lead's responsibility, just as for an ordinary human test report.
At complete it requires nonempty DONE tasks, passed reports, passed integrated
verification and no recorded running agent/job. Observe native reality separately.

## Tick / wait behavior

- tick reads state/reports only; it outputs a JSON summary + actionable refs.
- Invalid RUN.json/unsafe paths: stderr JSON, exit 2. No partial state rewrite.
- Valid state with P0/P1 actions: stdout JSON, exit 0. **Exit 0 does not mean accepted.**
- REVIEW with a report: P1 report_ready or report_failed. Malformed/stale: P0.
- ACCEPTING also validates its prior PASS review; DONE validates both gates.
- Run task RUNNING whose bound job is collected without a run-integrity report:
  P1 collect_pending. Paid DONE without a valid usage-audit report: P0.
- Valid usage audit above the recorded budget: P1 budget_exceeded.
- Agent count over `max_agents`, or a task attempt past `max_review_rounds`:
  P1 agent_budget_exceeded / review_rounds_exceeded. Neither is a parse error;
  a stopped run does not need its audit agents, so use `postrun-check` there.
- Ready independent TODOs: P1 task_ready, limited by free slots and owned files.
- In implementation, all DONE: P1 verification_needed. Planning does not dispatch.
- Running agent with no recorded event for ≥1800 seconds: P1 check_agent, not dead.
- No action: P4 quiet. P2/P3 are reserved.
- wait uses a bounded process-local sleep (not model polling), defaults to 20 seconds
  and a 900-second maximum (hard maximum 3600). It exits on action, RUN.json revision
  change or timeout and never modifies files. There is no detached daemon.
