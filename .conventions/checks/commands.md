# Проверки перед REVIEW и перед коммитом

Все команды — из корня репозитория. Раздел «CI» — тела шагов `.github/workflows/tests.yml`
дословно: меняешь проверку — меняй её там, а сюда переноси копию. Второго набора нет.

## CI (`.github/workflows/tests.yml`, задача `tests`)

Тела шагов по порядку, каждый — отдельный шаг (имя шага в комментарии):

```bash
# Script tests (scripts/tests) — Ran 50 tests, OK; этот каталог не пополняется
cd plugins/supertick && python3 -m unittest discover -s scripts/tests -v

# Tool tests (tools/tests) — OK; новые тесты кладутся сюда
if [ -d tools/tests ]; then python3 -m unittest discover -s tools/tests -v; fi

# Shell syntax (bash -n)
for f in plugins/supertick/scripts/*.sh; do bash -n "$f"; done

# No foreign plugin types in plugins/supertick — пусто
if grep -RIn "agent-teams:" plugins/supertick; then exit 1; fi

# Manifest versions match
python3 -c "import json,sys; m=json.load(open('.claude-plugin/marketplace.json')); p=json.load(open('plugins/supertick/.claude-plugin/plugin.json')); v=[x['version'] for x in m['plugins'] if x['name']=='supertick']; print('marketplace', v, 'plugin', p['version']); sys.exit(0 if v==[p['version']] else 1)"

# No caches tracked in git — пусто
if git ls-files | grep -E '__pycache__|pytest_cache|\.pyc$'; then exit 1; fi
```

Шаг CI проверяется так же, как его запустит GitHub: `bash -e -c '<тело шага>'` — и один раз с
подложенной порчей, чтобы убедиться, что он умеет падать (`anti-patterns/avoid-ci-step-false-result.md`).

## Локально (из `docs/clean-room.md` и плана проверки)

| Что | Команда | Ждём |
|---|---|---|
| манифесты и frontmatter | `claude plugin validate .` | 0 ошибок |
| токены протокола | `python3 tools/token_check.py --spec-dir <каталог spec прогона>` | `ИТОГ: … расхождений=0` |
| авторский текст (чистая комната) | `tools/run_overlap.sh --corpus <git-клон исходника на ffc350e>/plugins/agent-teams --json docs/overlap-report.json` — SHA корпуса берётся из git HEAD клона; корпус без git — добавить `--corpus-sha <sha>`; `--corpus-sha` расходится с HEAD — код 2 | 0 8-грамм и 0 строк ≥ 40 вне белого списка; окна якорей — каждое обосновано в `docs/clean-room.md` |
| бит x у скриптов в коммите | см. ниже | только `scripts/tests/test_supervisor_tick.py` |

Бит x смотреть в коммите, а не в индексе:

```bash
git ls-tree -r HEAD plugins/supertick/scripts | awk '$1!="100755"'
```

## Потолки длины

Непустые строки: `grep -cv '^\s*$' <файл>`. Потолок, не цель.

| Файл | Потолок |
|---|---|
| `skills/team-feature/SKILL.md` | 200 |
| `references/phase1-planning.md` | 600 |
| `references/engines.md` | 450 |
| `references/phase3-verification.md` | 320 |
| `references/phase2-monitoring.md` | 280 |
| `references/team-runtime.md` | 150 |
| `agents/unified-reviewer.md` | 250 |
| `agents/proxy-teammate.md` | 300 |
| `agents/coder.md` | 220 |
| `agents/tech-lead.md`, `agents/architect.md` | по 160 |
| одноразовые роли (`*-verifier`, `*-researcher`, `risk-tester`) | по 120 |
| `skills/conventions` (SKILL.md + references) | 150 |
| `skills/interviewed-team-feature` (SKILL.md + references) | 200 |

## Frontmatter

| Что | Правило |
|---|---|
| агент | `name` = имя файла; `description` ≤ 300 символов, есть подстрока «спавнит только /supertick:team-feature», без `<example>` |
| навык | `description` ≤ 400 символов, триггер — явная просьба или вызов по имени; `model:` не закрепляется |
| тип агента в тексте | только `supertick:<роль>`; встроенные `general-purpose`, `Explore` — у ролей, которые называет плагин |
| корень плагина | литерал `CLAUDE_PLUGIN_ROOT` только в `skills/team-feature/SKILL.md`; в остальных — `{plugin}` |
