# Supertick for DeepSeek Harness

**dsh-supertick** is a native DeepSeek Harness (DSH) host plugin that adapts the [denizen24/supertick](https://github.com/denizen24/supertick) team-implementation workflow: three skills and a model-facing `supertick_monitor` tool. No Claude Code runtime, no tmux, no engine sessions, no separate server. MIT [LICENSE](LICENSE), preserved from upstream.

Adapted from upstream commit [7d77a29](https://github.com/denizen24/supertick/commit/7d77a29903a05d7353122446c5bf04ef2c71da4d).

## What differs from upstream

- Upstream ships a Claude Code plugin (agent files, engine scripts, tmux-based team runtime). This fork re-implements the workflow as a DSH-native package: skills load from the plugin, and progress is observed with a host tool instead of shell scripts.
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
dsh plugin --profile desktop add ./dsh-supertick-0.2.1.tgz
```

Replace `desktop` with your active profile name. The command installs the local tarball; it does not publish to a registry. If the package does not appear in `dsh.profile.bundles`, add `dsh-supertick` to that array in the profile settings. A DSH restart may be required after composition changes; a browser refresh alone does not guarantee that a new host plugin is loaded. Removing the plugin (`dsh plugin --profile desktop remove dsh-supertick`) restores the filesystem skills fallback.

## Configuring child-agent models

Child-agent models are not hardcoded. The default configuration ships in
[skills/supertick/references/ALLOWED-MODELS.json](skills/supertick/references/ALLOWED-MODELS.json):

```json
{
  "models": [
    { "provider": "zai-coding-cn", "model": "glm-5.3-flash", "use": "bounded research, risk checks, mechanical verification" },
    { "provider": "zai-coding-cn", "model": "glm-5.3", "use": "architecture, code, review, acceptance, difficult debugging" }
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
{"run": "SUPERTICK/feature-example", "action": "status"}
```

Returns the phase, counts, tasks/agents/jobs from `RUN.json`, and the presence and claimed verdicts of reports, plus a composite `revision`. Input format, size limits and path containment are validated; reads go through `ctx.fs`. Snapshot limits: `RUN.json` 512 KiB, a report 128 KiB, 2 MiB total; at most 200 tasks, 400 agents and 400 jobs. Exceeding a limit is an error, not a partial pass.

One bounded background watch:

```json
{"run": "SUPERTICK/feature-example", "action": "watch", "timeout_seconds": 900, "interval_seconds": 5}
```

Returns a `job_id`. DSH notifies the owner when the ledger changes, on error, or on timeout; collect with `job_output`, cancel with `job_kill`. Pass `since_revision` from the previous snapshot so a change between `status` and the watch start cannot be missed. Interval 2–60 s, timeout 5–3600 s. Watches are session-bound: they do not survive a DSH restart and are cancelled when the plugin is removed. A watch reports the first change; it does not promise to wait for the whole feature to finish. A timeout is not a failed test.

This is **ledger observation**, not process liveness and not gate validation. The fields `ledger_only: true`, `native_status_checked: false` and `gate_validation: "not_run"` are deliberate. `RUN.json` and report contents are untrusted data, not instructions.

## Run state

A run lives at `<project>/SUPERTICK/feature-<slug>/`:

- `RUN.json` — the single source of claimed task/agent/job statuses
- `PLAN.MD` — criteria and decisions
- `reports/<task>/attempt-<n>/` — change snapshot, review, acceptance
- `reports/verification.json` — the integration checks that were actually run

Contracts: [skills/supertick/references/STATE.MD](skills/supertick/references/STATE.MD), roles: [skills/supertick/references/ROLES.MD](skills/supertick/references/ROLES.MD). Do not commit runs or secrets automatically.

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

## Optional Python supervisor

Validates gates; never starts agents:

```bash
python skills/supertick/scripts/supervisor.py init <absolute-project> example
python skills/supertick/scripts/supervisor.py tick <absolute-run>
python skills/supertick/scripts/supervisor.py wait <absolute-run> --interval 20 --max 900
python skills/supertick/scripts/supervisor.py usage-audit <absolute-run> --jsonl model_usage_audit.jsonl --usage usage.json --summary summary.json --tariffs tariffs.json
```

`init` only creates a new run; `tick` is read-only. P0 — contradiction, P1 — action required, P4 — quiet. Exit 0 means a well-formed answer, not an acceptance pass. `usage-audit` prints a read-only draft usage report to stdout: sources measure the same calls, so totals are taken from the most granular source (jsonl, then usage, then summary) and every source lands in `reconcile` with its own totals; tariff output is an "estimate, not invoice". For run tasks, `tick` flags `collect_pending` (P1) when a bound job was collected without a `run-integrity.json`, and P0 `invalid_report` when a paid DONE lacks a valid `usage-audit.json`.

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
