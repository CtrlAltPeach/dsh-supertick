#!/usr/bin/env bash
# run-engine.sh — launch one external CLI engine call, detached, with its result kept on disk.
#
# Launch (returns at once; the caller never waits on this command):
#
#   run-engine.sh --role <name> --run-dir .claude/teams/<team> --prompt <file> \
#       [--label <NNN|tag>] [--engine cursor|codex|kimi|grok] [--task <id>] \
#       [--report <name.md>] [--session <id>] [--session-regex <re>] [--timeout <sec>] \
#       -- <engine argv, with {prompt} / {prompt_file} placeholders>
#
#   e.g. PATH="$HOME/.local/bin:$PATH" run-engine.sh --role second-reviewer-3 \
#          --run-dir .claude/teams/feature-x --prompt /tmp/p.md --engine cursor --task 3 \
#          --report review-task3-second-r1.md --timeout 1800 \
#          -- cursor-agent -p --trust --output-format json --model grok-4.7-xhigh-fast --mode ask -- '{prompt}'
#
# Wait (a separate Bash call, run_in_background: true):
#
#   until [ -f <out>.done ]; do sleep 5; done; cat <out>.done
#
# Status of a role's calls (running / done-unread / taken):
#
#   run-engine.sh --status .claude/teams/<team> <role>
#
# Files, all under <run-dir>/engine/<role>/ :
#   NNN.prompt.md        the prompt (copied here if it lived elsewhere)
#   NNN.out.part         stdout+stderr while the engine runs
#   NNN.out              the same, atomically renamed when the engine exits — never overwritten
#   NNN.out.pid          pid of the detached worker (the engine is its child)
#   NNN.out.result.md    the reply: the `result` field of the engine's JSON, else the whole output
#   NNN.out.done         written LAST: exit code, times, paths. Its presence means everything is ready
#   NNN.out.taken        written by the caller after it has read and relayed the result
#   session.txt          the engine session id, for `resume`
# Ledger: <run-dir>/ledger.jsonl gets `launch` (with pid) and `done` | `failed`, with real `date -Is` times.
# A label that already exists is never reused: the next free number (or tag-2, tag-3…) is taken.
set -u

die() { echo "run-engine: $*" >&2; exit 2; }

json_line() {  # json_line key value key value ... -> one JSON object on stdout
  python3 - "$@" <<'PY'
import json, sys
a = sys.argv[1:]
d = {}
for k, v in zip(a[0::2], a[1::2]):
    if v == "":
        continue
    if k in ("pid", "exit", "wall_s"):
        try: v = int(v)
        except ValueError: pass
    d[k] = v
print(json.dumps(d, ensure_ascii=False))
PY
}

# ---------------------------------------------------------------- status mode
if [ "${1:-}" = "--status" ]; then
  run="${2:-}"; role="${3:-}"
  [ -n "$run" ] && [ -n "$role" ] || die "usage: --status <run-dir> <role>"
  dir="$run/engine/$role"
  [ -d "$dir" ] || { echo "no calls for $role"; exit 0; }
  shopt -s nullglob
  for f in "$dir"/*.out.pid; do
    base="${f%.pid}"; pid=$(cat "$f" 2>/dev/null)
    if [ -f "$base.done" ]; then
      if [ -f "$base.taken" ]; then st="taken"; else st="DONE-UNREAD"; fi
      echo "$st $base $(tr '\n' ' ' < "$base.done")"
    elif [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      echo "RUNNING $base pid=$pid"
    else
      echo "DEAD $base pid=$pid (no .done, process gone)"
    fi
  done
  exit 0
fi

# ---------------------------------------------------------------- worker (detached)
if [ "${1:-}" = "__worker" ]; then
  shift
  out="$RE_OUT"; dir=$(dirname "$out")
  echo $$ > "$out.pid"
  started=$(date -Is); t0=$(date +%s)
  json_line ts "$started" event launch role "$RE_ROLE" engine "$RE_ENGINE" task "$RE_TASK" \
    label "$RE_LABEL" pid "$$" session "$RE_SESSION" prompt "$RE_PROMPT" out "$out" >> "$RE_LEDGER"

  prompt_text=$(cat "$RE_PROMPT")
  argv=()
  for a in "$@"; do
    a="${a//\{prompt_file\}/"$RE_PROMPT"}"   # quoted: bash 5.2 treats a bare & in the
    a="${a//\{prompt\}/"$prompt_text"}"      # replacement as "the matched text"
    argv+=("$a")
  done

  timeout -k 30 "$RE_TIMEOUT" "${argv[@]}" < /dev/null > "$out.part" 2>&1
  rc=$?
  mv "$out.part" "$out"
  ended=$(date -Is); wall=$(( $(date +%s) - t0 ))

  # session id and reply
  session=$(python3 - "$out" "$out.result.md" "${RE_SESSION_REGEX:-}" "$RE_ENGINE" <<'PY'
import json, re, sys
out, res, rx, engine = sys.argv[1:5]
text = open(out, encoding="utf-8", errors="replace").read()
session, result, is_error = "", None, False
for line in reversed(text.splitlines()):
    line = line.strip()
    if not line.startswith("{"):
        continue
    try:
        obj = json.loads(line)
    except ValueError:
        continue
    if isinstance(obj, dict) and ("result" in obj or "session_id" in obj):
        session = obj.get("session_id") or ""
        result = obj.get("result")
        is_error = bool(obj.get("is_error"))
        break
if not rx:
    rx = {"codex": r"session id:\s*([0-9a-fA-F-]{36})",
          "kimi": r"kimi -r (session_[0-9a-f-]+)"}.get(engine, "")
if not session and rx:
    m = re.findall(rx, text)
    if m:
        session = m[-1] if isinstance(m[-1], str) else m[-1][0]
if result is None:
    result = text
if result.strip():
    open(res, "w", encoding="utf-8").write(result if result.endswith("\n") else result + "\n")
print(session + ("\tERROR" if is_error else ""))
PY
)
  is_error=""
  case "$session" in *$'\t'ERROR) is_error=1; session="${session%$'\t'ERROR}" ;; esac
  [ -z "$session" ] && session="$RE_SESSION"
  [ -n "$session" ] && printf '%s\n' "$session" > "$dir/session.txt"

  status=done; reason=""
  if [ "$rc" -eq 124 ] || [ "$rc" -eq 137 ]; then status=failed; reason="timeout ${RE_TIMEOUT}s"
  elif [ "$rc" -ne 0 ]; then status=failed; reason="exit $rc"
  elif [ -n "$is_error" ]; then status=failed; reason="engine reported is_error"
  elif [ ! -s "$out.result.md" ]; then status=failed; reason="no reply in output"
  elif [ "$RE_ENGINE" = "cursor" ] && [ -z "$session" ]; then status=failed; reason="no JSON reply (no session_id)"
  fi

  report=""
  if [ -n "$RE_REPORT" ] && [ "$status" = done ]; then   # a failed call never lands in reports/
    mkdir -p "$(dirname "$RE_REPORT")"
    report="$RE_REPORT"
    if [ -e "$report" ]; then   # never overwrite: name-<label>.md, then -2, -3…
      stem="${RE_REPORT%.md}"; report="$stem-$RE_LABEL.md"; n=2
      while [ -e "$report" ]; do report="$stem-$RE_LABEL-$n.md"; n=$((n+1)); done
    fi
    cp "$out.result.md" "$report"
  fi

  json_line ts "$ended" event "$status" role "$RE_ROLE" engine "$RE_ENGINE" task "$RE_TASK" \
    label "$RE_LABEL" pid "$$" exit "$rc" wall_s "$wall" session "$session" reason "$reason" \
    out "$out" result "$out.result.md" report "$report" >> "$RE_LEDGER"

  {
    echo "status=$status"
    echo "exit=$rc"
    [ -n "$reason" ] && echo "reason=$reason"
    echo "started=$started"
    echo "ended=$ended"
    echo "wall_s=$wall"
    echo "session=$session"
    echo "out=$out"
    [ -s "$out.result.md" ] && echo "result=$out.result.md"
    [ -n "$report" ] && echo "report=$report"
  } > "$out.done.tmp"
  mv "$out.done.tmp" "$out.done"
  exit 0
fi

# ---------------------------------------------------------------- launcher
role="" run="" prompt="" label="" engine="" task="" report="" session="" sregex="" tmo=3600
while [ $# -gt 0 ]; do
  case "$1" in
    --role) role="$2"; shift 2 ;;
    --run-dir) run="$2"; shift 2 ;;
    --prompt) prompt="$2"; shift 2 ;;
    --label) label="$2"; shift 2 ;;
    --engine) engine="$2"; shift 2 ;;
    --task) task="$2"; shift 2 ;;
    --report) report="$2"; shift 2 ;;
    --session) session="$2"; shift 2 ;;
    --session-regex) sregex="$2"; shift 2 ;;
    --timeout) tmo="$2"; shift 2 ;;
    -h|--help) sed -n '2,34p' "$0"; exit 0 ;;
    --) shift; break ;;
    *) die "unknown option: $1 (the engine command goes after --)" ;;
  esac
done
[ -n "$role" ] && [ -n "$run" ] && [ -n "$prompt" ] || die "--role, --run-dir and --prompt are required"
[ $# -gt 0 ] || die "no engine command after --"
[ -s "$prompt" ] || die "prompt file missing or empty: $prompt"
command -v "$1" >/dev/null 2>&1 || die "engine binary not found: $1 (for cursor-agent try PATH=\"\$HOME/.local/bin:\$PATH\")"
case "$tmo" in ''|*[!0-9]*) die "--timeout must be seconds" ;; esac
command -v python3 >/dev/null || die "python3 is required"

mkdir -p "$run" || die "cannot create $run"
run=$(cd "$run" && pwd)
dir="$run/engine/$role"; mkdir -p "$dir" "$run/reports"
prompt=$(cd "$(dirname "$prompt")" && pwd)/$(basename "$prompt")

# pick a label that has never been used, and reserve it atomically (noclobber on .part)
next_num() {
  local max=0 f n
  for f in "$dir"/*.out "$dir"/*.out.part; do
    [ -e "$f" ] || continue
    n=$(basename "$f"); n="${n%%.*}"
    case "$n" in ''|*[!0-9]*) continue ;; esac
    n=$((10#$n)); [ "$n" -gt "$max" ] && max=$n
  done
  printf '%03d' $((max + 1))
}
[ -z "$label" ] && label=$(next_num)
base="$label"; k=2
while :; do
  out="$dir/$label.out"
  if [ ! -e "$out" ] && [ ! -e "$out.done" ] && ( set -C; : > "$out.part" ) 2>/dev/null; then break; fi
  if [[ "$base" =~ ^[0-9]+$ ]]; then label=$(printf "%0${#base}d" $((10#$label + 1))); else label="$base-$k"; k=$((k+1)); fi
done

# keep the prompt next to its output
target="$dir/$label.prompt.md"
if [ "$prompt" != "$target" ]; then
  if [ ! -e "$target" ]; then cp "$prompt" "$target"; prompt="$target"; fi
fi
[ -n "$session" ] && printf '%s\n' "$session" > "$dir/session.txt"

rep=""
if [ -n "$report" ]; then
  case "$report" in /*) rep="$report" ;; *) rep="$run/reports/$report" ;; esac
fi

RE_OUT="$out" RE_ROLE="$role" RE_ENGINE="$engine" RE_TASK="$task" RE_LABEL="$label" \
RE_SESSION="$session" RE_SESSION_REGEX="$sregex" RE_PROMPT="$prompt" RE_REPORT="$rep" \
RE_TIMEOUT="$tmo" RE_LEDGER="$run/ledger.jsonl" \
  setsid nohup "$(readlink -f "$0")" __worker "$@" < /dev/null > /dev/null 2>&1 &

for _ in $(seq 1 50); do [ -s "$out.pid" ] && break; sleep 0.1; done
pid=$(cat "$out.pid" 2>/dev/null || echo "$!")
echo "pid=$pid"
echo "out=$out"
echo "done=$out.done"
echo "result=$out.result.md"
[ -n "$rep" ] && echo "report=$rep (or a -$label suffixed name if that one exists; see the .done file)"
echo "wait: until [ -f '$out.done' ]; do sleep 5; done; cat '$out.done'"
exit 0
