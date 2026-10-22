#!/usr/bin/env python3
"""Сверка чистой комнаты: не повторяют ли наши тексты чужой корпус.

  overlap_check.py <цели…> --corpus <каталог> [--exclude <путь> …] [--whitelist <файл>]
                   [--n 8] [--min-line 40] [--anchors-k 5] [--anchors-strict]
                   [--corpus-sha <sha>] [--json <отчёт>]

Цель — файл или каталог (обходится целиком, кроме .git и __pycache__). Каналы:
  ngrams    общие n-граммы слов (по умолчанию 8). Слово — непрерывная последовательность
            букв и цифр любого алфавита после NFKC, нижнего регистра и снятия разметки.
            Гасится только n-грамма, у которой ВСЕ слова есть в белом списке.
  lines     дословно общие строки длиной ≥ --min-line (без пробелов по краям, со словами).
  headings  порядок заголовков `##` в паре «наш файл / файл корпуса» (≥ 2 заголовков,
            вне блоков кода) совпал целиком. Печатается только «совпало», без текста корпуса.
            Ограничение: сравнивается нормализованный ТЕКСТ заголовков, поэтому русские
            заголовки против английского корпуса здесь не совпадут никогда; headings=0 не
            доказывает другой порядок разделов при переводе — это задача канала anchors
            и ручной проверки.
  anchors   «подозрение на перевод»: общее окно из --anchors-k якорей подряд в том же
            порядке. Якорь — путь, имя файла .md/.py/.sh/.json/.jsonl, hex-SHA ≥ 7 знаков,
            id вида `x-N`, число из ≥ 2 цифр; протокольные имена и строки белого списка
            якорями не считаются. Канал отчётный: код выхода меняет только --anchors-strict.

Белый список — по строке на элемент, пустые строки и строки с `#` в начале пропускаются;
`<…>` в строке — подстановка для сверки якорей. Код выхода: 0 — чисто, 1 — есть совпадения
вне белого списка (или окна якорей при --anchors-strict), 2 — ошибка аргументов.
Чужой текст не печатается: только наш фрагмент, номер нашей строки и путь файла корпуса.
"""
import argparse
import json
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "node_modules"}
WORD_RE = re.compile(r"[^\W_]+")
LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
HEADING_RE = re.compile(r"^##\s+(.+?)\s*#*\s*$")

ANCHOR_RE = re.compile(
    r"(?P<path>(?:[\w.~-]+/)+[\w.-]*\w)"
    r"|(?P<file>\b[\w.-]+\.(?:md|py|sh|jsonl|json)\b)"
    r"|(?P<sha>\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,}\b)"
    r"|(?P<id>\b[a-z][a-z0-9]*(?:-[a-z][a-z0-9]*)*-\d+\b)"
    r"|(?P<num>\b\d{2,}\b)",
    re.I,
)
# Имена интерфейса прогона: их делят все тексты о командном прогоне, на перевод они не указывают.
PROTOCOL_ANCHORS = {
    "plan.md", "state.md", "decisions.md", "verification_plan.md", "verification_report.md",
    "legacy_report.md", "ledger.jsonl", "pending.log", "supervisor.json", "skill.md",
}


def nfkc(text):
    return unicodedata.normalize("NFKC", text).casefold()


def words_of(text):
    """Слова строки: NFKC, нижний регистр, без адресов ссылок, разметки и пунктуации."""
    return WORD_RE.findall(nfkc(LINK_RE.sub(r"\1", text)))


def read_text(path):
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def collect(paths, excludes=()):
    """[(путь, текст)] по файлам и каталогам; исключения — файл или каталог-префикс."""
    ex = [p.resolve() for p in excludes]
    out = []
    for p in paths:
        p = Path(p)
        files = [p] if p.is_file() else sorted(x for x in p.rglob("*") if x.is_file()) if p.is_dir() else []
        for f in files:
            if SKIP_DIRS & set(f.parts):
                continue
            rf = f.resolve()
            if any(rf == e or e in rf.parents for e in ex):
                continue
            text = read_text(f)
            if text is not None:
                out.append((f, text))
    return out


def word_stream(text):
    """[(слово, номер строки)] по всему тексту: n-граммы идут через переносы строк."""
    stream = []
    for no, line in enumerate(text.splitlines(), 1):
        stream.extend((w, no) for w in words_of(line))
    return stream


def ngrams(stream, n):
    """{n-грамма: номер строки первого слова первого вхождения}."""
    grams = {}
    for i in range(len(stream) - n + 1):
        grams.setdefault(tuple(w for w, _ in stream[i:i + n]), stream[i][1])
    return grams


def long_lines(text, min_len):
    """{строка без краевых пробелов: номер} для строк ≥ min_len, где есть хотя бы одно слово."""
    out = {}
    for no, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if len(s) >= min_len and words_of(s):
            out.setdefault(s, no)
    return out


def headings(text):
    """Нормализованные заголовки уровня `##` вне блоков кода, по порядку."""
    out, fenced = [], False
    for line in text.splitlines():
        if FENCE_RE.match(line):
            fenced = not fenced
            continue
        m = None if fenced else HEADING_RE.match(line)
        if m:
            out.append(" ".join(words_of(m.group(1))))
    return out


class Whitelist:
    def __init__(self, lines=()):
        self.words, self.exact, self.patterns = set(), set(), []
        for raw in lines:
            s = raw.strip()
            if not s or s.startswith("#"):
                continue
            self.words.update(words_of(s))
            self.exact.add(nfkc(s))
            if "<" in s:
                parts = re.split(r"<[^>]*>", nfkc(s))
                self.patterns.append(re.compile(".+?".join(re.escape(x) for x in parts)))

    @classmethod
    def load(cls, path):
        return cls(Path(path).read_text(encoding="utf-8").splitlines() if path else ())

    def covers_words(self, words):
        return bool(words) and all(w in self.words for w in words)

    def covers_anchor(self, anchor):
        return anchor in self.exact or any(p.fullmatch(anchor) for p in self.patterns)


def anchors_of(text, whitelist):
    """Якоря содержания по порядку первого появления, без протокольных и белых."""
    out, seen = [], set()
    for no, line in enumerate(text.splitlines(), 1):
        for m in ANCHOR_RE.finditer(line):
            a = nfkc(m.group(0)).rstrip(".,:;)")
            if not a or a in seen:
                continue
            if Path(a).name in PROTOCOL_ANCHORS or whitelist.covers_anchor(a):
                continue
            seen.add(a)
            out.append((a, no))
    return out


def windows(anchors, k):
    """{окно из k якорей подряд: номер строки первого}."""
    out = {}
    for i in range(len(anchors) - k + 1):
        out.setdefault(tuple(a for a, _ in anchors[i:i + k]), anchors[i][1])
    return out


def fragment(text, no, width=120):
    lines = text.splitlines()
    s = lines[no - 1].strip() if 0 < no <= len(lines) else ""
    return s if len(s) <= width else s[:width - 1] + "…"


def corpus_sha(corpus):
    try:
        r = subprocess.run(["git", "-C", str(corpus), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def check(targets, corpus_files, whitelist, n=8, min_line=40, anchors_k=5):
    """Сверка: словарь каналов, у каждого `count` и `items` (только наш текст и путь корпуса)."""
    gram_index, line_index, head_index, corpus_anchor_windows = {}, {}, [], []
    for path, text in corpus_files:
        for g in ngrams(word_stream(text), n):
            gram_index.setdefault(g, str(path))
        for s in long_lines(text, min_line):
            line_index.setdefault(s, str(path))
        hs = headings(text)
        if len(hs) >= 2:
            head_index.append((hs, str(path)))
        corpus_anchor_windows.append((windows(anchors_of(text, whitelist), anchors_k), str(path)))

    res = {"ngrams": [], "lines": [], "headings": [], "anchors": [], "whitelisted_ngrams": 0}
    for path, text in targets:
        for g, no in sorted(ngrams(word_stream(text), n).items(), key=lambda x: x[1]):
            if g not in gram_index:
                continue
            if whitelist.covers_words(g):
                res["whitelisted_ngrams"] += 1
                continue
            res["ngrams"].append({"file": str(path), "line": no, "ngram": " ".join(g),
                                  "fragment": fragment(text, no), "corpus_file": gram_index[g]})
        for s, no in sorted(long_lines(text, min_line).items(), key=lambda x: x[1]):
            if s in line_index and not (whitelist.covers_words(words_of(s)) or nfkc(s) in whitelist.exact):
                res["lines"].append({"file": str(path), "line": no, "fragment": fragment(text, no),
                                     "corpus_file": line_index[s]})
        hs = headings(text)
        for chs, cpath in head_index:
            if hs == chs:
                res["headings"].append({"file": str(path), "count": len(hs), "corpus_file": cpath})
        ours = windows(anchors_of(text, whitelist), anchors_k)
        for cw, cpath in corpus_anchor_windows:
            for w, no in sorted(ours.items(), key=lambda x: x[1]):
                if w in cw:
                    res["anchors"].append({"file": str(path), "line": no, "anchors": list(w), "corpus_file": cpath})
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description="Сверка наших текстов с чужим корпусом (чистая комната).")
    ap.add_argument("targets", nargs="+", help="наши файлы или каталоги")
    ap.add_argument("--corpus", required=True, help="каталог чужого корпуса")
    ap.add_argument("--exclude", action="append", default=[],
                    help="файл или каталог корпуса, который не сверять (путь от --corpus или абсолютный), можно повторять")
    ap.add_argument("--whitelist", help="файл белого списка: строка на элемент, # — комментарий")
    ap.add_argument("--n", type=int, default=8, help="длина n-граммы в словах (8)")
    ap.add_argument("--min-line", type=int, default=40, help="минимальная длина общей строки (40)")
    ap.add_argument("--anchors-k", type=int, default=5, help="окно якорей подряд (5)")
    ap.add_argument("--anchors-strict", action="store_true", help="окна якорей тоже дают код выхода 1")
    ap.add_argument("--corpus-sha", help="SHA корпуса для отчёта (по умолчанию git rev-parse HEAD в корпусе)")
    ap.add_argument("--json", help="куда записать отчёт JSON")
    a = ap.parse_args(argv)

    corpus = Path(a.corpus)
    if not corpus.is_dir():
        ap.error(f"нет каталога корпуса: {corpus}")
    for t in a.targets:
        if not Path(t).exists():
            ap.error(f"нет цели: {t}")
    if a.n < 1 or a.anchors_k < 1:
        ap.error("--n и --anchors-k должны быть ≥ 1")
    excludes = [Path(e) if Path(e).is_absolute() else corpus / e for e in a.exclude]
    whitelist = Whitelist.load(a.whitelist)
    targets = collect(a.targets)
    corpus_files = collect([corpus], excludes)
    res = check(targets, corpus_files, whitelist, a.n, a.min_line, a.anchors_k)

    grouped = {}   # одна строка вывода на нашу строку и файл корпуса, сколько бы n-грамм там ни было
    for m in res["ngrams"]:
        grouped.setdefault((m["file"], m["line"], m["corpus_file"]), [m["fragment"], 0])[1] += 1
    for (f, no, cf), (frag, count) in grouped.items():
        print(f"NGRAM {f}:{no} (x{count}): {frag}  <- {cf}")
    for m in res["lines"]:
        print(f"LINE {m['file']}:{m['line']}: {m['fragment']}  <- {m['corpus_file']}")
    for m in res["headings"]:
        print(f"HEADINGS {m['file']}: порядок {m['count']} заголовков ## совпал  <- {m['corpus_file']}")
    for m in res["anchors"]:
        print(f"ANCHORS {m['file']}:{m['line']}: {' | '.join(m['anchors'])}  <- {m['corpus_file']}")

    counts = {k: len(res[k]) for k in ("ngrams", "lines", "headings", "anchors")}
    failed = counts["ngrams"] + counts["lines"] + counts["headings"] + (counts["anchors"] if a.anchors_strict else 0)
    print(f"ИТОГ: ngrams={counts['ngrams']} lines={counts['lines']} headings={counts['headings']} "
          f"anchors={counts['anchors']} (белых n-грамм погашено: {res['whitelisted_ngrams']}) "
          f"целей={len(targets)} файлов корпуса={len(corpus_files)}")
    if a.json:
        report = {"corpus": str(corpus.resolve()), "corpus_sha": a.corpus_sha or corpus_sha(corpus),
                  "excluded": [str(e) for e in excludes], "targets": [str(p) for p, _ in targets],
                  "corpus_files": len(corpus_files),
                  "params": {"n": a.n, "min_line": a.min_line, "anchors_k": a.anchors_k,
                             "anchors_strict": a.anchors_strict, "whitelist": a.whitelist},
                  "counts": counts, "whitelisted_ngrams": res["whitelisted_ngrams"],
                  "ngrams": res["ngrams"], "lines": res["lines"], "headings": res["headings"],
                  "anchors": res["anchors"]}
        Path(a.json).parent.mkdir(parents=True, exist_ok=True)
        Path(a.json).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
