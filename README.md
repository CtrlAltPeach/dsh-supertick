# supertick

English · [Русский](README.ru.md)

supertick is a Claude Code plugin that turns one slash command, `/supertick:team-feature`, into a
small software team: a lead, researchers, one coder per task, a single reviewer for every task, a
one-shot acceptance checker and a set of verifiers at the end. What sets it apart is the
**supervisor tick**, an event loop that runs outside the model. The lead does not sit in its own
turn waiting for news. It goes to sleep on a background script, and that script wakes it only
when the team needs a decision.

This is 1.0.0. The loop itself has been used on real runs since September 2026 (our earlier 0.14
to 0.17 line); before release, supertick ran on its own repository at the SIMPLE, MEDIUM and
COMPLEX levels.

## What it is

You describe a feature or point at a plan file. The lead splits the work into tasks, rates the
complexity with a fixed checklist (SIMPLE, MEDIUM or COMPLEX) and staffs the team accordingly:

| Complexity | Who takes part |
|---|---|
| SIMPLE | lead, coders, reviewer |
| MEDIUM | the above, plus a tech lead and risk testers |
| COMPLEX | the above, plus three architects (frontend, backend, systems) who argue over the plan in rounds and leave before the first coder starts |

Every task goes through the same gates: the coder's own checks, a single `unified-reviewer` pass
(security, logic, quality, and whether the change matches the plan) and acceptance by a fresh
checker that reads only the diff. After the last task come a cross-task consistency pass, build and
tests, browser checks when the feature has a UI, a sweep for leftover legacy code and a list of
manual checks for a human.

Design choices that matter:

- **The lead never waits inside a turn.** A turn costs as much as the lead's context weighs, and
  halfway through a run that is hundreds of thousands of tokens. Polling for news is therefore done
  by a script with no model behind it; the lead wakes up only when there is work. See "The
  supervisor tick" below.
- **Messages are also files.** Copies of the letters to the lead, and of REVIEW, VERDICT and ANSWER between teammates, are files in `mail/<recipient>/`. Anything a
  teammate is waiting for, such as a review verdict or the lead's answer to a question, is written
  as a file first and sent as a message second. The waiting side picks the file up with
  `team-wait.sh` in the foreground, because a background wait does not wake an idle teammate; when SendMessage answers `queued`, nobody has to resend anything by hand.
- **DONE is a claim, not a result.** A task is closed neither by its coder nor by the lead.
  Acceptance belongs to a fresh checker that gets only the task's section of the plan plus what its
  commits changed, and gives a verdict on each criterion in turn. The lead never reads the code; it
  reads a single line, `ACCEPT: task #N — PASS` or `ACCEPT: task #N — FAIL`.
- **Other CLIs can fill roles.** Any role in the registry can be handed to Codex, Kimi, Grok or
  Cursor through `~/.claude/agent-teams.json`. No engine is called directly: the
  launcher `run-engine.sh` puts it in the background, records it in the ledger and leaves a
  `.done` marker at the end, so a restarted session can still collect the answer.
- **State lives on disk.** Plan, state, mail, teammate cards and review and acceptance reports all
  sit under `.claude/teams/feature-<name>/`. After context compaction the lead rebuilds its picture
  from these files and one tick, without asking anyone.

What ships in the plugin:

| Kind | Items |
|---|---|
| Skills | `team-feature` (the team run), `interviewed-team-feature` (2 to 6 questions, a brief, then the run), `conventions` (builds `.conventions/` with reference files for coders) |
| Agents | `coder`, `unified-reviewer`, `tech-lead`, `architect`, `proxy-teammate`, `codebase-researcher`, `reference-researcher`, `risk-tester`, `spec-verifier`, `ci-verifier`, `browser-verifier` |
| Scripts | `supervisor-tick.py`, `supervisor-wait.sh`, `run-state.py`, `team-mail.sh`, `team-wait.sh`, `run-engine.sh`, `engine-sessions.py` |

Requirements: Claude Code (builds 2.1.27x to 2.1.308, per the dated notes in
`plugins/supertick/skills/team-feature/references/team-runtime.md`), `bash`,
`python3` with the standard library only, and `git`. The tick's `busy` rule reads `/proc`, so it
applies on Linux and is simply skipped elsewhere. Role and skill prompts are written in Russian;
the progress feed follows the user's language.

## The supervisor tick

The problem. The lead only wakes up on an outside event: a teammate's message, a subagent
completion notice, a background call returning. It cannot tell a quiet team from a dead one, and
the old workaround was an idle turn to look around. With a context of several hundred thousand
tokens that is the most expensive move in the whole team; relaying one message through the lead
was measured at $0.38 (see `docs/measurements.md`).

The mechanism is two scripts and zero model calls:

```
lead ──(background Bash)──> supervisor-wait.sh ──every 20 s──> supervisor-tick.py
  ^                                 │                                 │
  │                                 │               reads PLAN.md, state.md, pending.log,
  │                                 │               runs/*.json, mail/, ledger.jsonl, engine/
  │                                 │                                 │
  └──── call returns = lead wakes <── exits once an action is P1 or higher
```

- `supervisor-tick.py <run-dir>` reads the run files once and lists what needs doing, each item
  tagged P0 (most urgent) to P4, and keeps a snapshot plus its own memory in `state/supervisor.json`.
- `supervisor-wait.sh <run-dir>` repeats the tick with `sleep` in between and exits when an action
  at P1 or above shows up (the threshold is `--wake-on`) or when `--max` runs out (one hour by
  default). The lead launches it as a background call and ends its turn; the call returning is
  the wake-up. While the loop spins, no tokens are spent.
- On waking, the lead reads about ten lines of output and does what they name, highest priority
  first. A handled letter is marked with `supervisor-tick.py ack <run-dir> <letter file>`; a task
  counts as finished once its `Status:` line in `PLAN.md` says so.

What counts as a reason to wake:

| Priority | Examples |
|---|---|
| P0 | a `QUEUED` copy undelivered for 2 minutes; a letter untaken in a mailbox for 2 minutes; STUCK or ENGINE_DOWN; an engine process gone without a marker; an engine answer nobody picked up for 15 minutes; a teammate silent for two check intervals with no edits to its task files |
| P1 | a coder reported DONE and acceptance has not started; an acceptance report is ready; a question, escalation or SENSITIVE request is unanswered; a teammate showed no sign of life 3 minutes after start |
| P2 to P3 | the first 60 quiet seconds; a scheduled checkpoint; a teammate running pytest or jest started inside the quiet window (`busy`, instead of a false P0) |
| P4 | `ok`, all quiet, keep sleeping |

Teammates report their own signs of life. A coder's first command is
`run-state.py set … status=running`, and the first-minute clock starts there. The lead creates the
card `runs/<name>.json` right before the spawn; after that only the teammate writes to it. A role
with no task yet (reviewer, tech lead) starts as `idle` and is not treated as silent, and each
first-minute alarm fires once.

What the tick cannot see: a Claude teammate that died, because there is no process or marker to
look at. So on every P0 wake the lead compares the roster with the list of running agents.

Before-and-after numbers, each with its source and with the reasons the comparison is not clean,
are in [docs/measurements.md](docs/measurements.md) (in Russian).

## Install

From your terminal:

```bash
claude plugin marketplace add denizen24/supertick
claude plugin install supertick@supertick
```

Or from inside a running Claude Code session (v2.1.275+):

```
/plugin install supertick --marketplace denizen24/supertick
```

Restart the Claude Code session afterwards; skills and agents are loaded at startup. An update
is only picked up when `version` in the manifest goes up; the same number with new content will
not install.

To confirm the scripts are in place and the tick runs (the cache path of your copy may differ):

```bash
root="$(ls -d ~/.claude/plugins/cache/*/supertick/* | sort -V | tail -1)"
mkdir -p /tmp/supertick-smoke
python3 "$root/scripts/supervisor-tick.py" /tmp/supertick-smoke
# P4 ok: ... (all quiet; the message text is in Russian)
```

Tests, from a clone of this repository:

```bash
cd plugins/supertick && python3 -m unittest discover -s scripts/tests   # 50 tests for the tick and scripts
cd ../.. && python3 -m unittest discover -s tools/tests                 # clean-room overlap tool and format contracts
```

## Quick start

From the root of your project's git repository:

```
/supertick:team-feature add CSV export to the orders page
```

A path to a `.md` plan works in place of a description. Flags:

- `--coders=N` sets how many coders run in parallel (5 by default);
- `--no-research` skips the codebase research step;
- `--engines=off` keeps every role on Claude and ignores the engine config.

For a vague idea, start with `/supertick:interviewed-team-feature`: a short conversation, a brief
saved under `.briefs/`, then the same run. `/supertick:conventions` builds `.conventions/` once:
reference files the lead pastes into every coder prompt, which is the biggest single lever on
code quality.

What happens next:

1. The lead prints a route map and locates the plugin root. It only asks you about things you
   would see: the plan brief on MEDIUM and COMPLEX, design forks (two or three options), legacy
   cleanup, manual checks. Every other technical call it makes on its own.
2. A short feed appears in the chat: decisions, confirmed risks, tasks entering review and
   acceptance, finished tasks with a `{done}/{total}` counter, anything stuck. You can interrupt at
   any point.
3. `.claude/teams/feature-<name>/` is the one place that answers "what is going on": `PLAN.md`
   with task statuses, `state.md`, `VERIFICATION_PLAN.md`, `reports/`, `mail/`, `runs/`. Nothing in
   it is deleted when the run ends.
4. At the end you get `VERIFICATION_REPORT.md` and a numbered list of manual checks with commands
   and what success looks like.

Coders commit, each one only its own paths. The lead neither edits nor commits code itself.

## Engines

The plugin works fully with Claude Code alone; the engine config is an optional enhancement.

**Without `~/.claude/agent-teams.json`:** all roles run on Claude, no extra calls are made, and
nothing about engines is printed. Researchers, risk testers, spec verifier and legacy scanner are
done by Claude subagents; there are no proxy teammates. The one exception is `second-reviewer`
(second opinion on SENSITIVE tasks: auth/permissions, money/balance, DB migrations, shared infra):
it is skipped rather than falling back to Claude, because asking the same model twice won't
surface anything new. Each task gets one reviewer. The same applies when the config exists but the
binary it names (e.g. `cursor-agent`) isn't installed: silent fallback to Claude, second opinion
skipped.

**Recommendation if you add a config:** the most valuable addition is a different-vendor model for
`second-reviewer`, for example `cursor` with `gpt-5.6-sol-high` in read-only ask mode. After that,
offload bulk one-shot roles (researchers, risk testers, spec verifier, legacy scanner) to a
cheaper model to save Claude request limits. Keep `lead`, `unified-reviewer`, `tech-lead` and
`coder` on Claude.

Handing a role over to an outside CLI takes one entry in `~/.claude/agent-teams.json`:

```json
{
  "fallback": "claude",
  "roles": {
    "codebase-researcher": "codex",
    "second-reviewer": { "engine": "cursor", "model": "gpt-5.6-sol-high" }
  }
}
```

- Presets exist for `codex`, `kimi`, `grok` and `cursor` (binary `cursor-agent`). An
  `engines.<name>` block overrides a preset one field at a time: `cmd`, `resume`, `model`,
  `effort`, `mode_flags` (an object `{read, write}`), `session`, `result`.
- Before the run the lead checks `command -v` once for every binary the config mentions. A missing
  binary sends the role to `fallback`: `"claude"` (the default) or `"fail"`, which stops the run.
- A one-shot role on an engine runs without a subagent. A long-lived teammate on an engine joins
  as `supertick:proxy-teammate` under its usual name, and the rest of the team sees no difference.
- `scripts/run-engine.sh` is the only way to start an engine: it runs detached, the answer lands in
  `engine/<role>/`, the `.done` marker is written last, and `launch` / `done` / `failed` lines go
  to `ledger.jsonl`. Wait for the marker, never for a timer. `run-engine.sh --status` tells a
  running engine from an unread finished answer and from a dead process.
- `second-reviewer` gives a second opinion on tasks the reviewer flags SENSITIVE (auth, money,
  migrations). It is off unless the config names it, and only when its engine differs from the
  reviewer's. What it finds is passed to the reviewer, and the coder still gets a single verdict.
- `acceptance-checker` always runs on Claude: a read-only engine is unable to execute the
  task's tests, so its acceptance could not be trusted.
- Engines advise; Claude roles decide. Approving deviations, settling review loops, picking design
  options and deleting code stay with them.

The full role registry, the presets, a worked example of turning `cmd` into argv and the waiting
rules are in `plugins/supertick/skills/team-feature/references/engines.md`. A complete example
config is `plugins/supertick/agent-teams.example.json`.

## Compatibility with agent-teams.json

The file is called `~/.claude/agent-teams.json` for historical reasons: the agent-teams plugin
uses the same path and format. supertick reads an existing file as it is, with no edits:

- the same top-level keys (`enabled`, `fallback`, `roles`, `engines`) and the same role ids;
- any key starting with `_`, at any depth, is a comment and is skipped;
- roles that belong to other plugins (for example `research-challenger`, `rollback-critic`) are
  not an error and stay out of the engine table;
- `roles.<id>` may be an engine name or an object `{engine, model?, effort?, sandbox?}`;
- the retired ids `security-reviewer`, `logic-reviewer` and `quality-reviewer` produce one warning
  and are ignored;
- a missing or unreadable file, or broken JSON, puts everything on Claude, with a one-line warning
  for the broken case.

If another team plugin with the same role names is enabled next to it, supertick still spawns only
its own `supertick:<role>` types. The single exception is Claude Code's built-in agent types, used
for the few roles the plugin names itself: `general-purpose` for the web researcher, `Explore` for
the cross-task check and the leftover-code scan. Types from any other plugin are never used; an
unknown type leads to a question, never to borrowing someone else's. A run started on a different
plugin should be finished there: there is no migration of the run directory.

## License

MIT, see [LICENSE](LICENSE).

