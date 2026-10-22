#!/usr/bin/env python3
"""Собирает белый список сверки чистой комнаты (tools/overlap-whitelist.txt).

  build_whitelist.py --spec-dir <каталог spec> [--out <файл>]

Источники в каталоге спецификации: protocol.md (все `код`-вставки и блоки с отступом 4),
engines.md §2, §5 и §7 (реестр ролей, команды пресетов и argv run-engine.sh), roles.md
(строки frontmatter агентов: `model:`, `color:`, `tools`). Ручной раздел файла (всё после
строки MANUAL_MARK) сохраняется как есть. Спецификация в публичный репозиторий не входит,
поэтому каталог передаётся явно; без --spec-dir скрипт выходит с кодом 2.

Две ловушки tools/overlap_check.py, которые генератор обходит:
  1. Строка белого списка, начинающаяся с `#`, — комментарий и молча пропускается. Поэтому
     заголовки (`## Task <id>:`) пишутся без решёток в начале.
  2. Строка из одной подстановки (`<RUN DIR>`) после снятия `<…>` пуста и становится шаблоном
     «любой якорь» — она погасила бы весь канал якорей. Такие строки генератор отбрасывает.
"""
import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "tools" / "overlap-whitelist.txt"
MANUAL_MARK = "# === ручные строки (каждая с пометкой-комментарием над ней) ==="
SPAN = re.compile(r"`([^`\n]+)`")


def sections(text, numbers):
    """Текст разделов `## <n>.` с номерами из numbers."""
    out, keep = [], False
    for line in text.splitlines():
        m = re.match(r"^## (\d+)\.", line)
        if m:
            keep = int(m.group(1)) in numbers
        if keep:
            out.append(line)
    return "\n".join(out)


def items(text):
    got = []
    for line in text.splitlines():
        if line.startswith("    ") and not line.lstrip().startswith("|"):
            got.append(line.strip())
        got.extend(s.strip() for s in SPAN.findall(line))
    return got


def frontmatter(roles):
    """Строки frontmatter агентов из roles.md: `model: X`, `color: Y`, `tools`: список."""
    out, lines = [], roles.splitlines()
    for i, line in enumerate(lines):
        if "`model:" not in line or "`tools`:" not in line:
            continue
        text = line
        j = i + 1
        while j < len(lines) and lines[j].startswith("  ") and not lines[j].lstrip().startswith("- "):
            text += " " + lines[j].strip()
            j += 1
        out += re.findall(r"`((?:model|color): [^`]+)`", text)
        tools = text.split("`tools`:", 1)[1].split("(")[0].strip().rstrip(".").strip()
        out.append("tools: " + re.sub(r"\s+", " ", tools))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Сборка белого списка сверки из спецификации.")
    ap.add_argument("--spec-dir", help="каталог спецификации (protocol.md, engines.md, roles.md)")
    ap.add_argument("--out", default=str(OUT), help="файл белого списка (по умолчанию tools/overlap-whitelist.txt)")
    a = ap.parse_args(argv)
    if not a.spec_dir:
        ap.error("укажите путь: --spec-dir <каталог спецификации с protocol.md, engines.md, roles.md>")
    spec = Path(a.spec_dir)
    for name in ("protocol.md", "engines.md", "roles.md"):
        if not (spec / name).is_file():
            ap.error(f"нет файла {spec / name}: укажите --spec-dir каталога спецификации")
    out_path = Path(a.out)

    proto = (spec / "protocol.md").read_text(encoding="utf-8")
    eng = (spec / "engines.md").read_text(encoding="utf-8")
    gen, seen = [], set()
    proto = proto[proto.index("\n## "):]   # без вступления файла
    roles = (spec / "roles.md").read_text(encoding="utf-8")
    for s in items(proto) + items(sections(eng, {2, 5, 7})) + frontmatter(roles):
        # ловушка 1: `#` в начале строки белого списка — комментарий, решётки снимаем
        s = s.lstrip("#").strip()
        # ловушка 2: строка из одной подстановки погасила бы канал якорей
        if not re.search(r"[^\W_]", re.sub(r"<[^>]*>", "", s)):
            continue
        if s and s not in seen:
            seen.add(s)
            gen.append(s)
    manual = []
    if out_path.exists():
        old = out_path.read_text(encoding="utf-8").splitlines()
        if MANUAL_MARK in old:
            manual = old[old.index(MANUAL_MARK) + 1:]
    head = [
        "# Белый список сверки чистой комнаты (tools/overlap_check.py --whitelist).",
        "# Строка — элемент интерфейса: токен протокола, имя файла, ключ, команда пресета.",
        "# Сгенерированная часть: все вставки `кода` и блоки кода реестра протокола supertick",
        "# (spec/protocol.md прогона 121-398), реестр ролей и команды пресетов движков (spec/engines.md §2, §5, §7),",
        "# строки frontmatter агентов (model, color, tools из spec/roles.md). Решётки в начале заголовков сняты.",
        "# Гасится только n-грамма, все слова которой есть в этом списке.",
        f"# Сгенерировано строк: {len(gen)}.",
        "",
    ]
    out_path.write_text("\n".join(head + gen + ["", MANUAL_MARK] + manual).rstrip("\n") + "\n", encoding="utf-8")
    print(f"generated={len(gen)} manual={sum(1 for l in manual if l.strip() and not l.startswith('#'))} -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
