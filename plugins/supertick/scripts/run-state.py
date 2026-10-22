#!/usr/bin/env python3
"""run-state.py — карточка участника runs/<name>.json: создать при спавне, обновить по ходу.

  run-state.py new <run-dir> <name> role=<роль> task=<id> [files=a.py,b.py] [checkAfterSec=900] \
                   [acceptance=...] [nonGoals=...]          — ведущий, прямо перед спавном
                   (роль без задачи — рецензент, техлид — рождается `idle`: ждёт первый запрос)
  run-state.py set <run-dir> <name> status=<running|in_review|fixing|reviewing|done|stuck|idle|stopped> \
                   [note=...] [task=<id>]                    — сам участник, при каждом событии
  run-state.py show <run-dir> [<name>]

`set` всегда ставит lastEventAt=сейчас; первый `set status=running` ставит ещё и
startedAt — от него тик считает первую минуту, а не от карточки, которую ведущий
завёл за несколько минут до спавна. Файл переписывается целиком через
временный и rename: у карточки один владелец, гонок нет. Роль без Bash пишет
тот же JSON инструментом Write — поля те же.
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

LISTS = ("files", "acceptance", "nonGoals")


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def parse(pairs):
    out = {}
    for p in pairs:
        if "=" not in p:
            raise SystemExit(f"run-state: ожидается key=value, а не {p!r}")
        k, v = p.split("=", 1)
        if k in LISTS:
            v = [x.strip() for x in v.split(",") if x.strip()]
        elif k == "checkAfterSec":
            v = int(v)
        out[k] = v
    return out


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def main(argv):
    if len(argv) < 2:
        raise SystemExit(__doc__)
    cmd, run = argv[0], Path(argv[1])
    if cmd == "show":
        names = [argv[2]] if len(argv) > 2 else [p.stem for p in sorted((run / "runs").glob("*.json"))]
        for n in names:
            print(n, json.dumps(json.loads((run / "runs" / f"{n}.json").read_text(encoding="utf-8")), ensure_ascii=False))
        return 0
    if len(argv) < 3:
        raise SystemExit(__doc__)
    name, fields = argv[2], parse(argv[3:])
    path = run / "runs" / f"{name}.json"
    if cmd == "new":
        role, task = fields.pop("role", ""), fields.pop("task", "")
        data = {"name": name, "role": role, "task": task,
                "spawnedAt": now(), "lastEventAt": now(), "status": "running" if task else "idle",
                "files": fields.pop("files", []), "acceptance": fields.pop("acceptance", []),
                "nonGoals": fields.pop("nonGoals", []), "checkAfterSec": fields.pop("checkAfterSec", 900), "note": ""}
        data.update(fields)
        write(path, data)
    elif cmd == "set":
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"name": name, "spawnedAt": now()}
        data.update(fields)
        data["lastEventAt"] = now()
        if fields.get("status") == "running" and not data.get("startedAt"):
            data["startedAt"] = data["lastEventAt"]
        write(path, data)
    else:
        raise SystemExit(__doc__)
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
