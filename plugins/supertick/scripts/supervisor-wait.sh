#!/usr/bin/env bash
# supervisor-wait.sh — сон ведущего: крутит тики, пока не появится действие нужного приоритета.
#
#   supervisor-wait.sh <run-dir> [--wake-on P0|P1|P2|P3] [--interval <сек>] [--max <сек>] [--root <корень>]
#
# Запускается ведущим в фоне (run_in_background: true) после спавна команды и после
# каждого обработанного пробуждения. Завершается — и тем самым будит ведущего —
# в трёх случаях: тик нашёл действие с приоритетом не ниже --wake-on (по
# умолчанию P1), истёк --max (по умолчанию 3600 с), каталог прогона пропал.
# На выходе печатает причину и действия тика: это всё, что ведущему нужно
# прочитать, полный снимок лежит в state/supervisor.json.
#
# Ожидание стоит ноль токенов: между тиками спит sleep, а не модель.
set -u
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
run="${1:-}"; shift || true
[ -n "$run" ] && [ -d "$run" ] || { echo "supervisor-wait: нужен каталог прогона" >&2; exit 2; }
# Абсолютный путь сразу: фоновый Bash ведущего может крутиться не в том каталоге,
# где его запустили, и относительный путь «пропадал» на первом же тике (121-374).
run=$(cd "$run" && pwd)
wake="P1"; interval=20; max=3600; root=""
while [ $# -gt 0 ]; do
  case "$1" in
    --wake-on) wake="$2"; shift 2 ;;
    --interval) interval="$2"; shift 2 ;;
    --max) max="$2"; shift 2 ;;
    --root) root="$2"; shift 2 ;;
    *) echo "supervisor-wait: неизвестный аргумент $1" >&2; exit 2 ;;
  esac
done
case "$wake" in P0|P1|P2|P3) ;; *) echo "supervisor-wait: --wake-on ждёт P0..P3" >&2; exit 2 ;; esac
rootarg=(); [ -n "$root" ] && rootarg=(--root "$(cd "$root" && pwd)")
started=$(date +%s)
echo "supervisor-wait: сплю на $run, бужу на $wake и выше, тик каждые ${interval} с, не дольше ${max} с"
while :; do
  [ -d "$run" ] || { echo "WAKE: каталог прогона пропал"; exit 0; }
  out=$(python3 "$here/supervisor-tick.py" "$run" "${rootarg[@]}" 2>&1) || { echo "WAKE: тик сломался"; echo "$out"; exit 0; }
  # Действие уровня --wake-on и выше: строка тика начинается с P0..P<wake>.
  if echo "$out" | grep -qE "^P[0-${wake#P}] "; then
    echo "WAKE: есть действие уровня $wake или выше"; echo "$out"; exit 0
  fi
  if [ $(( $(date +%s) - started )) -ge "$max" ]; then
    echo "WAKE: истёк максимум ожидания ${max} с — плановая проверка"; echo "$out"; exit 0
  fi
  sleep "$interval"
done
