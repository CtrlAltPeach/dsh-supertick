#!/usr/bin/env python3
"""Сверка токенов протокола: каждый токен из protocol.md §1 пишется одинаково
у отправителя и у получателя в тексте плагина.

  token_check.py --spec-dir <каталог спецификации с protocol.md> [--plugin <каталог плагина>]

Спецификация в публичный репозиторий не входит, поэтому --spec-dir обязателен: без него
скрипт выходит с кодом 2, а не проходит молча. --plugin по умолчанию plugins/supertick.

Каналы:
  1. Токены сообщений (§1): каноническая голова токена (до первой подстановки, например
     `VERDICT: task #`) должна найтись хотя бы в одном файле каждой стороны — отправителя и
     получателя. Плюс поиск искажённых написаний по всем текстам плагина: голова без двоеточия
     перед `task`, `task <id>` без `#` после двоеточия, слово токена в другом регистре в начале
     сообщения.
  2. KIND писем (§4 F6): каждый KIND в вызовах `team-mail.sh … <KIND> task` и в шапках `kind:`
     входит в канонический список; каждый `--kind K` у team-wait.sh кем-то отправляется.
Код выхода: 0 — расхождений нет, 1 — есть, 2 — ошибка аргументов.
"""
import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

REF = "skills/team-feature/references/"
LEAD = ["skills/team-feature/SKILL.md", REF + "phase1-planning.md", REF + "phase2-monitoring.md",
        REF + "phase3-verification.md", REF + "team-runtime.md", REF + "supervisor.md", REF + "engines.md"]
# Сторона протокола → файлы плагина, где эта сторона описана.
PARTY = [
    (r"ведущ", LEAD),
    (r"кодер", ["agents/coder.md"]),
    (r"unified-reviewer|рецензент", ["agents/unified-reviewer.md"]),
    (r"техлид", ["agents/tech-lead.md"]),
    (r"архитектор|главный", ["agents/architect.md"]),
    (r"прокси", ["agents/proxy-teammate.md"]),
    (r"second-reviewer", ["agents/unified-reviewer.md", REF + "engines.md"]),
    (r"проверяющий приёмки", ["agents/spec-verifier.md", REF + "phase2-monitoring.md"]),
    (r"внешний движок", [REF + "engines.md", "agents/proxy-teammate.md"]),
    (r"получатель RESEND|адресат|любой|участник", LEAD + ["agents/coder.md", "agents/unified-reviewer.md",
                                                       "agents/tech-lead.md", "agents/proxy-teammate.md"]),
]
SPAN = re.compile(r"`([^`\n]+)`")


def files_for(cell):
    out = []
    for pat, files in PARTY:
        if re.search(pat, cell):
            out += [f for f in files if f not in out]
    return out


def head(token):
    """Каноническая голова: до первой подстановки `<…>`, без хвостовых пробелов."""
    h = re.split(r"<|\{|…", token, 1)[0].rstrip()
    return h


def tokens_table(text):
    rows = []
    sec = text.split("## 1.", 1)[1].split("\n## 2.", 1)[0]
    for line in sec.splitlines():
        if not line.startswith("| `"):
            continue
        cells = [c.strip() for c in line.strip("|").split(" | ")]
        if len(cells) < 3:
            continue
        # только первая строка сообщения: всё до «+ строки…», «(+ …», «, вторая строка» — тело
        first = re.split(r"\s\+\s|\(\+|, вторая строка", cells[0], 1)[0]
        spans = SPAN.findall(first)
        rows.append((spans, cells[1], cells[2]))
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description="Сверка токенов протокола у отправителя и получателя.")
    ap.add_argument("--spec-dir", help="каталог спецификации с protocol.md")
    ap.add_argument("--plugin", default=str(REPO / "plugins" / "supertick"),
                    help="каталог плагина (по умолчанию plugins/supertick)")
    a = ap.parse_args(argv)
    if not a.spec_dir:
        ap.error("укажите путь: --spec-dir <каталог спецификации с protocol.md>")
    proto_path = Path(a.spec_dir) / "protocol.md"
    if not proto_path.is_file():
        ap.error(f"нет файла {proto_path}: укажите --spec-dir каталога спецификации")
    plugin = Path(a.plugin).resolve()
    if not plugin.is_dir():
        ap.error(f"нет каталога плагина: {plugin}")
    proto = proto_path.read_text(encoding="utf-8")
    texts = {str(p.relative_to(plugin)): p.read_text(encoding="utf-8")
             for p in sorted(plugin.rglob("*.md"))}
    problems, checked = [], 0

    # 1. Токены сообщений
    for spans, frm, to in tokens_table(proto):
        main_tok = spans[0]
        heads = []
        for s in spans:
            # только сами токены (верхний регистр в начале), без вспомогательных вставок вроде `Commits:`
            h = head(s)
            if h:
                if h not in heads:
                    heads.append(h)
        for h in heads:
            for side, cell in (("отправитель", frm), ("получатель", to)):
                files = files_for(cell)
                if not files:
                    problems.append(f"TOKEN {h!r}: сторона «{cell}» не сопоставлена файлам")
                    continue
                checked += 1
                hit = [f for f in files if h in texts.get(f, "")]
                if not hit:
                    problems.append(f"TOKEN {h!r}: нет у стороны {side} «{cell}» ({', '.join(files)})")

    # Искажённые написания голов `WORD: task #`
    words = set()
    for spans, _, _ in tokens_table(proto):
        m = re.match(r"^([A-Z_ ]+[A-Z]):\s*task #", spans[0])
        if m:
            words.add(m.group(1))
    for f, t in texts.items():
        for no, line in enumerate(t.splitlines(), 1):
            for w in words:
                for bad, why in ((rf"(?<![\w-]){re.escape(w)} task #", "без двоеточия"),
                                 (rf"(?<![\w-]){re.escape(w)}:\s*task (?!#)", "task без #"),
                                 (rf"(?<![\w-]){re.escape(w)}:task", "без пробела после двоеточия"),
                                 (rf"(?<![\w])(?i:{re.escape(w)}):\s*task #", "регистр")):
                    for m in re.finditer(bad, line):
                        if why == "регистр" and m.group(0).startswith(w):
                            continue
                        problems.append(f"VARIANT {f}:{no}: {w} {why}: {line.strip()[:110]}")

    # 2. KIND писем
    kinds = set(re.findall(r"`([A-Z_]+)`", proto.split("| F6 |", 1)[1].split("\n", 1)[0]))
    sent, waited = set(), {}
    for f, t in texts.items():
        for no, line in enumerate(t.splitlines(), 1):
            # подстановки с пробелами (`<RUN DIR>`, `{run dir}`) считаем одним словом
            line = re.sub(r"<[^<>]*>|\{[^{}]*\}", lambda m: m.group(0).replace(" ", "_"), line)
            for m in re.finditer(r"team-mail\.sh\s+\S+\s+\S+\s+\S+\s+([A-Za-z_<>]+)\s+task\b", line):
                k = m.group(1)
                if k.startswith("<"):
                    continue
                checked += 1
                sent.add(k)
                if k not in kinds:
                    problems.append(f"KIND {f}:{no}: {k!r} нет в списке F6")
            for m in re.finditer(r"^\s*kind:\s*(\S+)\s*$", line):
                k = m.group(1)
                if k.startswith("<"):
                    continue
                checked += 1
                sent.add(k)
                if k not in kinds:
                    problems.append(f"KIND {f}:{no}: шапка kind: {k!r} нет в списке F6")
            for m in re.finditer(r"--kind\s+([A-Za-z_]+)", line):
                waited.setdefault(m.group(1), f"{f}:{no}")
    for k, where in sorted(waited.items()):
        checked += 1
        if k not in kinds:
            problems.append(f"WAIT {where}: --kind {k!r} нет в списке F6")
        elif k not in sent:
            problems.append(f"WAIT {where}: --kind {k!r} никто не отправляет (team-mail.sh / kind:)")

    for p in problems:
        print(p)
    print(f"ИТОГ: проверок={checked} расхождений={len(problems)} KIND отправляются={sorted(sent)} ждут={sorted(waited)}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
