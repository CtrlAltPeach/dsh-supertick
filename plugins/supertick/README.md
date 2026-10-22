# supertick (plugin)

Agent team for Claude Code built around the supervisor tick: an event loop outside the model that
lets a thin lead sleep until the team needs it.

- Skills: `team-feature` (`/supertick:team-feature`), `interviewed-team-feature`, `conventions`.
- Agents: `agents/*.md`, spawned only by `/supertick:team-feature` as `supertick:<role>`.
- Scripts: `scripts/` (tick, wait loop, teammate cards, mail, engine launcher); to run their
  tests, stay in this directory and call `python3 -m unittest discover -s scripts/tests`.
- Example engine config: `agent-teams.example.json` (optional; without it all roles run on Claude).

## Install

```
/plugin install supertick --marketplace denizen24/supertick
```

For full instructions, quick start, engines and config compatibility: see the
[repository README](../../README.md) ([по-русски](../../README.ru.md)).
