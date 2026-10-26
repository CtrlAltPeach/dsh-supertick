# State contract (schema 1)

Paths below are relative to one SUPERTICK/feature-<slug> run. The lead is the only
writer of RUN.json and PLAN.MD; agents write their own attempt-local reports.
Do not edit RUN.json via a shell while another writer is active. Use inspect/stat
and the returned SHA-256 revision in a CAS file-tool edit. Preserve earlier reports.

## RUN.json

`supervisor.py init` creates an empty planning run. Extend that object, not a second
status file. All keys shown below are mandatory except null bindings/change_id may
be absent in TODO. Timestamps are ISO 8601 with a timezone. Store actual native IDs.

```json
{
  "schema": 1,
  "name": "example",
  "project_root": "C:/work/example",
  "created_at": "2026-01-01T00:00:00Z",
  "updated_at": "2026-01-01T00:00:00Z",
  "phase": "implementation",
  "complexity": "SIMPLE",
  "max_coders": 2,
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
risk-tester, coder, reviewer, checker, verifier. Planning/verifier task may be null.
Only provider/model pairs from references/ALLOWED-MODELS.json are allowed. No status derives from
an invented ID, file existence alone, or an interruption acknowledgement.

Jobs: `{"id":"actual-job-id","purpose":"integration tests","status":"running"}`.
Status is running/collected/killed; collected means the owner obtained its result,
not necessarily success. Child-owned jobs must be collected by that child, whose
report includes exit codes. Lead cannot assume it owns every child's job.

Exact file ownership is case-insensitive even on Linux to catch Windows collisions.
Paths use `/` and are relative to project_root (no directories/globs, `..`, drive
prefixes, reserved Windows devices, symlinks or junctions). Lead also verifies real
project files and hardlink aliases: the validator checks path syntax and state,
not filesystem aliases or real code modifications. Untouched untracked files are
not permission to overwrite them. A task may claim more files only after the lead
serializes and records the new ownership.

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
- Ready independent TODOs: P1 task_ready, limited by free slots and owned files.
- In implementation, all DONE: P1 verification_needed. Planning does not dispatch.
- Running agent with no recorded event for ≥1800 seconds: P1 check_agent, not dead.
- No action: P4 quiet. P2/P3 are reserved.
- wait uses a bounded process-local sleep (not model polling), defaults to 20 seconds
  and a 900-second maximum (hard maximum 3600). It exits on action, RUN.json revision
  change or timeout and never modifies files. There is no detached daemon.
