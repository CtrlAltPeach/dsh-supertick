#!/bin/bash
# Сверка чистой комнаты: тексты supertick против каталога plugins/agent-teams исходника
# на коммите ffc350e, без наших девяти файлов (наш код линии 0.14–0.17, три из них изменены в 0.18.1).
#
#   tools/run_overlap.sh --corpus <каталог plugins/agent-teams> [аргументы overlap_check.py …]
#
# Остальные аргументы уходят в tools/overlap_check.py как есть (например --json docs/overlap-report.json).
# SHA корпуса в отчёте: git HEAD корпуса, иначе --corpus-sha <sha>; иначе код 2. Оба есть и расходятся — код 2.
CORPUS=
SHA=
REST=()
while [ $# -gt 0 ]; do
  case "$1" in
    --corpus) CORPUS="$2"; shift 2 || { echo "run_overlap.sh: после --corpus нужен путь" >&2; exit 2; } ;;
    --corpus=*) CORPUS="${1#--corpus=}"; shift ;;
    --corpus-sha) SHA="$2"; shift 2 || { echo "run_overlap.sh: после --corpus-sha нужен SHA" >&2; exit 2; } ;;
    --corpus-sha=*) SHA="${1#--corpus-sha=}"; shift ;;
    *) REST+=("$1"); shift ;;
  esac
done
if [ -z "$CORPUS" ]; then
  echo "run_overlap.sh: укажите путь: --corpus <каталог plugins/agent-teams исходника на ffc350e>" >&2
  exit 2
fi
if [ ! -d "$CORPUS" ]; then
  echo "run_overlap.sh: нет каталога корпуса: $CORPUS" >&2
  exit 2
fi
CORPUS=$(cd "$CORPUS" && pwd)
HEAD=$(git -C "$CORPUS" rev-parse --verify -q HEAD 2>/dev/null)
if [ -n "$HEAD" ]; then
  if [ -n "$SHA" ] && [ "$(git -C "$CORPUS" rev-parse --verify -q "$SHA^{commit}" 2>/dev/null)" != "$HEAD" ]; then
    echo "run_overlap.sh: --corpus-sha $SHA расходится с git HEAD корпуса $HEAD; уберите --corpus-sha или переключите корпус" >&2
    exit 2
  fi
  SHA=$HEAD
elif [ -z "$SHA" ]; then
  echo "run_overlap.sh: SHA корпуса неизвестен: $CORPUS не в git; положите корпус в git-клон или укажите --corpus-sha <sha>" >&2
  exit 2
fi
cd "$(dirname "$0")/.." || exit 2
EX=()
for f in scripts/supervisor-tick.py scripts/supervisor-wait.sh scripts/team-mail.sh scripts/team-wait.sh \
         scripts/run-state.py scripts/run-engine.sh scripts/engine-sessions.py \
         scripts/tests/test_supervisor_tick.py skills/team-feature/references/supervisor.md; do
  EX+=(--exclude "$f")
done
exec python3 tools/overlap_check.py plugins/supertick tools README.md README.ru.md docs/measurements.md \
  --corpus "$CORPUS" "${EX[@]}" --whitelist tools/overlap-whitelist.txt \
  --corpus-sha "$SHA" "${REST[@]}"
