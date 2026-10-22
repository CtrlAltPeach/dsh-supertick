#!/usr/bin/env bash
# team-wait.sh — участник ждёт письмо в своём почтовом ящике, не заканчивая работу вслепую.
#
#   team-wait.sh <run-dir> <моё имя> [--kind VERDICT] [--task <id>] [--interval <сек>] [--max <сек>]
#
# Участник запускает его в переднем плане сразу после запроса, на который нужен
# ответ: кодер — после REVIEW и после QUESTION / ESCALATION / STUCK, вернувшегося
# `queued` (с 0.17.0 ведущий кладёт ответ файлом до сообщения); вызов —
# `--task <id> --max 540` без --kind, Bash timeout 570000, повтор до письма. KIND
# любой — сверяется со строкой `kind:` письма без учёта регистра; без --kind подходит
# любое письмо. Завершается, как только в <run-dir>/mail/<моё имя>/ появилось ещё не
# прочитанное письмо нужного вида и задачи: печатает путь и текст письма и отмечает
# его прочитанным в mail/<моё имя>/.seen. Фоном не запускать: конец фоновой задачи
# простаивающего участника не будит (22.10.2026, feature-trial-fixes), а `.seen`,
# записанный за спящего, прячет от тика undelivered_mail. Файл надёжнее сообщения:
# SendMessage, вернувшийся `queued`, теряется (прогон 121-374: ~12 ручных пересылок;
# прогон fix-391-388 с ожиданием файла — ни одной).
#
# Если ответ уже пришёл сообщением раньше файла — ничего страшного: ожидание
# выйдет, когда файл ляжет, и участник увидит, что это тот же ответ.
# По --max (по умолчанию 3600 с) печатает TIMEOUT: повторить вызов; около часа
# всего — спросить ведущего.
# Ожидание стоит ноль токенов: между проверками спит sleep, а не модель.
set -u
run="${1:-}"; me="${2:-}"; shift 2 2>/dev/null || { echo "team-wait: run-dir и имя" >&2; exit 2; }
[ -d "$run" ] && [ -n "$me" ] || { echo "team-wait: нужен каталог прогона и имя участника" >&2; exit 2; }
kind=""; task=""; interval=15; max=3600
while [ $# -gt 0 ]; do
  case "$1" in
    --kind) kind=$(printf '%s' "$2" | tr '[:lower:]' '[:upper:]'); shift 2 ;;
    --task) task="$2"; shift 2 ;;
    --interval) interval="$2"; shift 2 ;;
    --max) max="$2"; shift 2 ;;
    *) echo "team-wait: неизвестный аргумент $1" >&2; exit 2 ;;
  esac
done
# значение ключа шапки (до первой пустой строки): ключ без регистра, пробелы и \r по краям не в счёт,
# повтор ключа — побеждает последний
head_value() {
  awk -v want="$2" '{ gsub(/\r/, "") } $0 == "" { exit }
    { i = index($0, ":"); if (!i) next
      key = tolower(substr($0, 1, i - 1)); gsub(/^[ \t]+|[ \t]+$/, "", key)
      if (key == want) { val = substr($0, i + 1); gsub(/^[ \t]+|[ \t]+$/, "", val) } }
    END { print val }' "$1"
}
# id задачи, как в тике: обрезать, первое слово, снять один ведущий # (`#3`, `3 (rework)` → `3`)
task_id() {
  v=$(printf '%s\n' "$1" | tr -d '\r' | awk 'NR == 1 { print $1 }')
  printf '%s' "${v#\#}"
}
want_task=$(task_id "$task")
box="$(cd "$run" && pwd)/mail/$me"
mkdir -p "$box"; touch "$box/.seen"
started=$(date +%s)
while :; do
  for f in "$box"/*.md; do
    [ -e "$f" ] || continue
    name=$(basename "$f")
    grep -qxF "$name" "$box/.seen" && continue
    if [ -n "$kind" ]; then
      [ "$(head_value "$f" kind | tr '[:lower:]' '[:upper:]')" = "$kind" ] || continue
    fi
    if [ -n "$task" ]; then
      [ "$(task_id "$(head_value "$f" task)")" = "$want_task" ] || continue
    fi
    echo "$name" >> "$box/.seen"
    echo "MAIL: $f"
    cat "$f"
    exit 0
  done
  if [ $(( $(date +%s) - started )) -ge "$max" ]; then
    echo "TIMEOUT: за ${max} с в $box нет письма${kind:+ $kind}${task:+ по задаче $task} — спросите ведущего"
    exit 0
  fi
  sleep "$interval"
done
