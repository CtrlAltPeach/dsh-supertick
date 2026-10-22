#!/usr/bin/env bash
# team-mail.sh — положить письмо в почтовый каталог команды: один файл на письмо.
#
#   team-mail.sh <run-dir> <кому> <от кого> <KIND> [task <id>] [--] <текст…>
#   echo "текст" | team-mail.sh <run-dir> <кому> <от кого> <KIND> [task <id>]
#
# Каталог: <run-dir>/mail/<кому>/, имя файла: <время>_<от кого>_<KIND>[_task<id>].md,
# в файле заголовок (from/kind/task/ts) и тело. KIND любой, первое слово письма: DONE,
# QUESTION, REVIEW, VERDICT, ANSWER (ответ ведущего кодеру, с 0.17.0) и т. д.; `ts:` ставит
# `date -Is` — роли без Bash берут его взаймы из письма, на которое отвечают. Почта — долговечная копия того,
# что уже отправлено через SendMessage: её читает тик супервизора и ведущий после
# восстановления, а не получатель в реальном времени. Роли без Bash пишут такой
# же файл инструментом Write — формат один.
set -u
run="${1:-}"; to="${2:-}"; from="${3:-}"; kind="${4:-}"; shift 4 2>/dev/null || { echo "team-mail: run-dir кому от-кого KIND [task id] текст" >&2; exit 2; }
task=""
if [ "${1:-}" = "task" ]; then task="${2:-}"; shift 2; fi
[ "${1:-}" = "--" ] && shift
[ -d "$run" ] && [ -n "$to" ] && [ -n "$from" ] && [ -n "$kind" ] || { echo "team-mail: нужны run-dir, кому, от кого и KIND" >&2; exit 2; }
if [ $# -gt 0 ]; then body="$*"; else body=$(cat); fi
ts=$(date -Is)
stamp=$(date +%Y%m%dT%H%M%S)
dir="$run/mail/$to"; mkdir -p "$dir"
name="${stamp}_${from}_${kind}"; [ -n "$task" ] && name="${name}_task${task}"
file="$dir/$name.md"; n=2
while [ -e "$file" ]; do file="$dir/$name-$n.md"; n=$((n + 1)); done
{
  echo "from: $from"; echo "kind: $kind"; [ -n "$task" ] && echo "task: $task"; echo "ts: $ts"; echo
  printf '%s\n' "$body"
} > "$file.tmp" && mv "$file.tmp" "$file"
echo "$file"
