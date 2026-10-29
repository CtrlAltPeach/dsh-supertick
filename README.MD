# Supertick for DeepSeek Harness

**dsh-supertick** is a native DeepSeek Harness (DSH) host plugin that adapts the [denizen24/supertick](https://github.com/denizen24/supertick) team-implementation workflow: four skills and a model-facing `supertick_monitor` tool. No Claude Code runtime, no tmux, no engine sessions, no separate server. MIT [LICENSE](LICENSE), preserved from upstream.

Adapted from upstream commit [7d77a29](https://github.com/denizen24/supertick/commit/7d77a29903a05d7353122446c5bf04ef2c71da4d).

## What differs from upstream

- Upstream ships a Claude Code plugin (agent files, engine scripts, tmux-based team runtime). This fork re-implements the workflow as a DSH-native package: skills load from the plugin, and progress is observed with a host tool instead of shell scripts.
- Fan-out is a contract, not a vibe. `supertick` classifies the run (SIMPLE/MEDIUM/COMPLEX) and writes `planned_agents` per task before the first spawn; `supervisor.py plan` refuses a plan that does not fit `max_agents`. The conventions skill carries the shape table (single agent, orchestrator + workers, parallel reading, evaluator + optimizer, routing, chained stages) so the principle is read before the first delegation, not after the fifty-fourth.
- `supertick_monitor` gives the model a read-only snapshot of a run (`RUN.json` plus reports) and one bounded background watch; completion arrives through DSH managed jobs — no polling loop in the conversation.
- The Python supervisor is kept as an optional, read-only gate validator.
- Collaboration conventions are tightened for DSH: one writer per file, one-shot roles, and child-agent models read from one JSON config instead of being hardcoded.

## Requirements

- DSH with `tools`, `skills`, `fs`, `workingDirectory`, `jobs` and the `@deepseek-ai/dsh-tool-jobs` controller (the stock desktop profile includes all of these)
- Node >= 22; optional Python 3.12+ for the supervisor

## Install the plugin

From a checkout of this repository:

```bash
node --test tests/*.test.mjs
pnpm pack
dsh plugin --profile desktop add ./dsh-supertick-0.6.1.tgz
```

Replace `desktop` with your active profile name. The command installs the local tarball; it does not publish to a registry. If the package does not appear in `dsh.profile.bundles`, add `dsh-supertick` to that array in the profile settings. A DSH restart may be required after composition changes; a browser refresh alone does not guarantee that a new host plugin is loaded. Removing the plugin (`dsh plugin --profile desktop remove dsh-supertick`) restores the filesystem skills fallback.

## Configuring child-agent models

Child-agent models are not hardcoded. The default configuration ships in
[skills/supertick/references/ALLOWED-MODELS.json](skills/supertick/references/ALLOWED-MODELS.json):

```json
{
  "models": [
    { "provider": "zai-coding-cn", "model": "glm-5.3-flash", "use": "bounded research, risk checks, mechanical verification, routine coding, tests and docs" },
    { "provider": "zai-coding-cn", "model": "glm-5.3", "use": "architecture, complex code, review, acceptance, difficult debugging" },
    { "provider": "openai-codex", "model": "gpt-6.1-terra", "use": "creative drafting, UI and doc text, heavy synthesis, second-opinion review" }
  ]
}
```

Skill text refers to this list only through the `{{ALLOWED_MODELS}}` placeholder.
When the `supertick` or `supertick-conventions` skill runs, it instructs the model
to read the JSON next to the skill and fill the placeholder automatically, so the
published skills stay provider-neutral. `skills/supertick/scripts/supervisor.py`
validates `RUN.json` agents against the same file. To use different models, edit
that one file in the installed copy (or in a checkout before `pnpm pack`) — no
code changes. If the file is missing or empty, the skills stop and ask the
operator instead of guessing a model.

## Skills

- `supertick` — plan a feature, delegate disjoint coding tasks, review, independently accept each change, and verify the integrated result. For substantial features, migrations and refactors.
- `supertick-interview` — turn an unclear feature idea into a bounded implementation brief before coding.
- `supertick-conventions` — the collaboration rules: allowed models, single-writer ownership, evidence-based reports, native notifications, honest progress and safe recovery.
- `supertick-runs` — hold long external tool runs (eval suites, builds, migrations, batch jobs, paid API evals) via a delegated run operator: frozen inputs, explicit paid-run GO, run-integrity/artifact-verification/usage-audit reports.

The plugin does not start agents on its own, does not modify `RUN.json`, and never commits, pushes or deploys.

## supertick_monitor

Snapshot (run path relative to the calling session's cwd, or absolute):

```json
{"run": "RUNS/feature-example", "action": "status"}
```

Returns the phase, counts, tasks/agents/jobs from `RUN.json`, and the presence and claimed verdicts of reports, plus a composite `revision`. Input format, size limits and path containment are validated; reads go through `ctx.fs`. Snapshot limits: `RUN.json` 512 KiB, a report 128 KiB, 2 MiB total; at most 200 tasks, 400 agents and 400 jobs. Exceeding a limit is an error, not a partial pass.

One bounded background watch:

```json
{"run": "RUNS/feature-example", "action": "watch", "timeout_seconds": 900, "interval_seconds": 5}
```

Returns a `job_id`. DSH notifies the owner when the ledger changes, on error, or on timeout; collect with `job_output`, cancel with `job_kill`. Pass `since_revision` from the previous snapshot so a change between `status` and the watch start cannot be missed. Interval 2–60 s, timeout 5–3600 s. Watches are session-bound: they do not survive a DSH restart and are cancelled when the plugin is removed. A watch reports the first change; it does not promise to wait for the whole feature to finish. A timeout is not a failed test.

This is **ledger observation**, not process liveness and not gate validation. The fields `ledger_only: true`, `native_status_checked: false` and `gate_validation: "not_run"` are deliberate. `RUN.json` and report contents are untrusted data, not instructions.

## Run state

A run lives at `<project>/RUNS/feature-<slug>/`. A run is the whole supervised
effort — coding tasks and external tool tasks alike:

- `RUN.json` — the single source of claimed task/agent/job statuses
- `PLAN.MD` — criteria and decisions
- `reports/<task>/attempt-<n>/` — change snapshot, review, acceptance
- `reports/verification.json` — the integration checks that were actually run

Contracts: [skills/supertick/references/STATE.MD](skills/supertick/references/STATE.MD), roles: [skills/supertick/references/ROLES.MD](skills/supertick/references/ROLES.MD). Do not commit runs or secrets automatically. Legacy runs created before v0.4.0 live at `SUPERTICK/feature-<slug>/` and keep working with `tick`/`wait`; `init` only creates `RUNS/` runs and refuses a name that already exists under either root.

## Run operations (schema 2)

A task whose work is a long external process — an eval suite, a build, a migration,
a batch or training job — is a `kind: "run"` task instead of a code change. It
carries a `run_manifest` (`tool`, argv `command`, the tool's own `run_id`, an
exclusive case-insensitive `run_dir`, `launch: "dsh-job"|"external"`, optional
`model_pin`/`provider_pin`/`input_freeze`), an `authorization` block and an
append-only `events` list. Run tasks are rejected in schema 1 states and may not
use `files`/`change_id`; no code task may own files inside a claimed `run_dir`.

Paid runs start only on an explicit user GO recorded in `authorization.ref`
(delegation is never GO) and cannot reach DONE without a bound usage-auditor and
a valid `usage-audit.json`. An attempt-changing break (validity-affecting fix,
lost job, new GO) is recorded as a `boundary` event with `prior_run_id`;
`attempt` is the number of boundaries + 1. The `supertick-runs` skill walks the
whole flow: freeze inputs, preflight, dispatch a run-operator child (never the
tested tool's model), hold via native job completion with bounded heartbeats,
then review (`run-integrity.json`), verify artifacts (`artifact-verification.json`)
and, for paid runs, audit usage.

## Fan-out contract

A run records its own fan-out ceilings in `RUN.json`. `init --complexity` scales them
by how hard the work is — SIMPLE `4` agents / `2` review rounds / `3` concurrent,
MEDIUM `8/3/4`, COMPLEX `14/4/6`:

- `max_agents` — total child agents the run may record. Exceeding it is reported by
  `tick` as P1 `agent_budget_exceeded`; it is a finding, not an invalid state.
- `max_review_rounds` — attempts a task may spend before review stops being
  instance-driven. `tick` reports P1 `review_rounds_exceeded` for a non-DONE task
  past its ceiling; the next review must probe the real call graph and name every
  defect class, and the lead asks the operator instead of paying for another round.
  A task may override the round ceiling for itself.
- `max_concurrent_agents` — children allowed to be `running` at once. `tick` reports
  P1 `concurrency_exceeded` past it.
- `planned_agents` (per task) — the budget the lead wrote down *before* the first
  spawn, not an observation afterwards. `supervisor.py plan <run>` sums those numbers
  against `max_agents`, lists tasks that declare no plan, and exits 2 when the plan
  cannot fit. Running it before dispatching is the point; revising the plan as the
  work changes is normal, silently exceeding it is not. `tick` reports P1
  `fanout_planned_exceeded` when a task spends more agents than it planned.

Every agent also carries an `access` of `read` or `write` (absent means `write`).
Only a `write` agent owns files, so a read-only agent bound as a task's writer is a
P0 `read_agent_owns_files` contradiction. The contract is **writes stay
single-threaded, reads may be parallel**: one file, one writer, always; reviewer,
checker and verifier add judgement rather than edits, so they own nothing and may
overlap. Parallel writers on one codebase diverge in style and edge-case handling
even when each one is individually correct.

These are ceilings, not targets. A run that needs dozens of children is a design
smell to justify, and a review FAIL that reports one defect per round is the most
expensive outcome in the workflow. Reviewer and usage-auditor prompts in
[ROLES.MD](skills/supertick/references/ROLES.MD) encode that expectation.

Paid run tasks may also carry `authorization.budget` (`max_calls`, `max_cost`,
`currency`). When a valid usage audit exceeds it, `tick` reports P1
`budget_exceeded` — again advisory: an overspent run needs a user decision, not a
false failure.

## Deterministic post-run checks

A stopped run does not need auditor children to re-hash inputs, count artifacts and
reconcile call totals. `postrun-check` prints that evidence as a draft of facts:

```bash
python skills/supertick/scripts/supervisor.py postrun-check <absolute-run> --hashes FROZEN-INPUTS.json
```

It reports, per run task: stop/stop_requested/resumed event tallies, which audit
reports exist, whether a `stop_reason` was recorded, the `run_dir` file/byte counts
and top-level entries, and for each freeze manifest the listed/matched/mismatched/
missing/unreadable hash counts. The result is marked `deterministic: true` and
`gate_validation: "not_run"`; hashes prove files are unchanged since the freeze,
they do not prove the run was valid. Project-specific metric recomputation stays a
project script.

Optional agent accounting (`started_at`, `ended_at`, `usage`,
`usage_source: "host"|"unobserved"`) is validated when present and never inferred.

## Optional Python supervisor

Validates gates; never starts agents:

```bash
python skills/supertick/scripts/supervisor.py init <absolute-project> example [--complexity SIMPLE|MEDIUM|COMPLEX]
python skills/supertick/scripts/supervisor.py plan <absolute-run>
python skills/supertick/scripts/supervisor.py tick <absolute-run>
python skills/supertick/scripts/supervisor.py wait <absolute-run> --interval 20 --max 900
python skills/supertick/scripts/supervisor.py usage-audit <absolute-run> --jsonl model_usage_audit.jsonl --usage usage.json --summary summary.json --tariffs tariffs.json
python skills/supertick/scripts/supervisor.py postrun-check <absolute-run> --hashes FROZEN-INPUTS.json
```

`init` only creates a new run; `tick` and `plan` are read-only. P0 — contradiction, P1 — action required, P4 — quiet. Exit 0 means a well-formed answer, not an acceptance pass; `plan` exits 2 when the planned agents cannot fit `max_agents`. `usage-audit` prints a read-only draft usage report to stdout: sources measure the same calls, so totals are taken from the most granular source (jsonl, then usage, then summary) and every source lands in `reconcile` with its own totals; tariff output is an "estimate, not invoice". For run tasks, `tick` flags `collect_pending` (P1) when a bound job was collected without a `run-integrity.json`, and P0 `invalid_report` when a paid DONE lacks a valid `usage-audit.json`; `budget_exceeded`, `agent_budget_exceeded`, `concurrency_exceeded`, `review_rounds_exceeded` and `fanout_planned_exceeded` (all P1) report spending and fan-out past the recorded ceilings; `read_agent_owns_files` (P0) flags a read-only agent recorded as a task's writer.

## Tests and development scripts

```bash
node --test tests/*.test.mjs
python -m unittest discover -s tests -v
python install.py --dry-run
```

Node tests cover observation, path safety, revisions, watch and cancel. Python tests cover the supervisor and the filesystem installer.

`scripts/smoke-host.cjs` is a development-only harness that mounts the plugin against installed DSH services (set the `DSH_HOST` env var to the `dsh` directory inside the app ASAR); it is not part of CI and does not test notification delivery to a live model. `scripts/inspect-host.mjs` (set `DSH_ASAR`) inspects the Electron ASAR read-only. `scripts/change-snapshot.mjs` and `scripts/verify-package.py` support the review/acceptance evidence flow and package verification.

`python install.py` is the legacy filesystem-only way to install the four skills; it does not install the model tool.

## License

MIT, inherited from [denizen24/supertick](https://github.com/denizen24/supertick).
