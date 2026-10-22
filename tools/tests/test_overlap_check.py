"""Инструмент сверки чистой комнаты на синтетических фикстурах: чужой корпус в тестах не нужен.

Запуск: python3 -m unittest discover -s tools/tests   (из корня репозитория)
Пара EN-абзац / близкий RU-перевод / независимый RU-пересказ — синтетика риск-теста R2
(.claude/teams/feature-supertick/tmp/r2/r2_crosslang_verify.py), перенесённая сюда.
"""
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOL = HERE.parent / "overlap_check.py"
spec = importlib.util.spec_from_file_location("overlap_check", TOOL)
oc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oc)

EN_SOURCE = """\
## Phase distribution

The team lead runs an event loop instead of blocking on each teammate turn. During phase one the lead writes PLAN.md, splits work into numbered tasks, and assigns each item to a coder or a one-shot verifier. Every three completed reviews the lead rotates unified-reviewer so no single reviewer owns the whole run. When a coder finishes they send `REVIEW: task #7` through team-mail.sh and wait on team-wait.sh for a VERDICT file.

Sensitive changes trigger second-reviewer-2 and a `## Second opinions` block before ACCEPT. The supervisor reads pending.log lines ending with `| OPEN`, parses ACCEPT: task #N — PASS from reports/accept-task14.md, and rejects duplicate DONE: spam. Proxies launch codex through run-engine.sh under engine/ and require a .done marker. Acceptance checks plugins/supertick/scripts/supervisor-tick.py for 50 unittest cases and records SHA ffc350e5abb5. Risk testers use tmp/r2 before clean tasks 5 through 11; facts come only from spec/pipeline.md while ledger.jsonl closes phase three when twelve PLAN rows read DONE.
"""

RU_CLOSE_TRANSLATION = """\
## Распределение фаз

Ведущий команды ведёт цикл событий вместо блокировки на каждом ходу участника. В фазе один ведущий пишет PLAN.md, дробит работу на пронумерованные задачи и назначает пункт кодеру или одноразовому проверяющему. Каждые три завершённых ревью ведущий ротирует unified-reviewer, чтобы один рецензент не владел прогоном. Когда кодер заканчивает, он шлёт `REVIEW: task #7` через team-mail.sh и ждёт файл VERDICT на team-wait.sh.

Чувствительные правки вызывают second-reviewer-2 и блок `## Second opinions` до ACCEPT. Супервизор читает pending.log с хвостом `| OPEN`, разбирает ACCEPT: task #N — PASS из reports/accept-task14.md и режет повторный DONE:. Прокси запускают codex через run-engine.sh в engine/ и требуют маркер .done. Приёмка проверяет plugins/supertick/scripts/supervisor-tick.py на 50 unittest и пишет SHA ffc350e5abb5. Тесты рисков идут в tmp/r2 до чистых задач 5–11; факты только из spec/pipeline.md, а ledger.jsonl закрывает фазу три, когда двенадцать строк PLAN показывают DONE.
"""

RU_INDEPENDENT = """\
## Как ведущий делит работу

Супертик будит ведущего из supervisor-wait.sh почтой, а не длинным чатом. План собирают из spec/pipeline.md; блок задач 5–11 — чистая комната без чужого кэша. Кодеру #3 дают протокол, #8 — скрипты, ci-verifier гоняет проверки после карточки merge.

Спорные диффы уходят к second-reviewer-3 в раздел `## Second opinions`, а unified-reviewer меняют после четвёртой приёмки, не третьей. `REVIEW:` кладут через team-mail.sh; ответ — файл VERDICT. Супервизор смотрит pending.log (`| OPEN`) и PASS в reports/accept-task9.md, отсекая лишние DONE:.

run-engine.sh стартует codex на три минуты в engine/ и ждёт .done. overlap_check с SHA 9ab12cd34ef0 относится к задаче 12. spec-verifier пишет ACCEPT: task #11 — PASS первой строкой. VERIFICATION_REPORT.md и ledger.jsonl фиксируют фазу 3, когда в PLAN.md двенадцать DONE; tmp/r2 гоняют риски заранее, а supervisor-tick.py держит 48 unittest.
"""

PROTOCOL_ONLY_EN = """\
Send `REVIEW: task #1` then wait for VERDICT.
Write DONE: only once. Use ACCEPT: task #1 — PASS in reports/accept-task1.md.
Read PLAN.md and state.md; tail pending.log for `| OPEN`.
"""

PROTOCOL_ONLY_RU = """\
Сначала `REVIEW: task #1`, потом жди VERDICT.
DONE: один раз. ACCEPT: task #1 — PASS клади в reports/accept-task1.md.
Смотри PLAN.md, state.md; в pending.log ищи `| OPEN`.
"""

RU_PHRASE = "ведущий спит на ожидании почты и просыпается только когда тик супервизора поднял тревогу первого уровня"
EN_PHRASE = "the lead sleeps on the mail waiter and wakes up only when the supervisor tick raises an alarm"


class Dirs:
    """Каталоги «наше» и «корпус» во временном каталоге теста."""

    def __init__(self, case):
        tmp = tempfile.TemporaryDirectory()
        case.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.ours = self.root / "ours"
        self.corpus = self.root / "corpus"
        self.ours.mkdir()
        self.corpus.mkdir()

    def write(self, side, rel, text):
        path = (self.ours if side == "ours" else self.corpus) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def run(self, *extra, targets=None):
        """Вызов CLI в процессе: (код выхода, stdout)."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = oc.main([*(targets or [str(self.ours)]), "--corpus", str(self.corpus), *extra])
        return rc, buf.getvalue()


def pair(our_text, corpus_text, whitelist=(), **kw):
    return oc.check([(Path("ours.md"), our_text)], [(Path("corpus.md"), corpus_text)], oc.Whitelist(whitelist), **kw)


class Words(unittest.TestCase):
    def test_russian_text_gives_words(self):
        words = oc.words_of("Ведущий **спит** на `supervisor-wait.sh`, а не ждёт ход!")
        self.assertEqual(words, ["ведущий", "спит", "на", "supervisor", "wait", "sh", "а", "не", "ждёт", "ход"])

    def test_nfkc_and_case_are_normalized(self):
        self.assertEqual(oc.words_of("ＲＥＶＩＥＷ Ｔask"), ["review", "task"])


class NGrams(unittest.TestCase):
    def test_english_copy_is_caught_with_exit_1(self):
        d = Dirs(self)
        d.write("corpus", "a.md", f"Intro.\n\n{EN_PHRASE}.\n")
        d.write("ours", "b.md", f"# Ours\n\nWe say: {EN_PHRASE.upper()}!\n")
        rc, out = d.run()
        self.assertEqual(rc, 1)
        self.assertIn("NGRAM", out)
        self.assertIn("b.md:3", out)

    def test_russian_copy_is_caught_with_exit_1(self):
        d = Dirs(self)
        d.write("corpus", "a.md", RU_PHRASE.replace(" и ", "\nи ", 1) + ".\n")   # перенос строки не спасает
        d.write("ours", "b.md", f"Итак, {RU_PHRASE}.\n")
        rc, out = d.run()
        self.assertEqual(rc, 1)
        self.assertIn("NGRAM", out)

    def test_seven_shared_words_are_not_an_8gram(self):
        res = pair("один два три четыре пять шесть семь восемь", "ноль два три четыре пять шесть семь восемь")
        self.assertEqual(res["ngrams"], [])
        res = pair("один два три четыре пять шесть семь восемь", "один два три четыре пять шесть семь восемь", n=8)
        self.assertEqual(len(res["ngrams"]), 1)

    def test_whitelist_silences_only_fully_white_ngrams(self):
        text = "from kind task ts review verdict done accept pass"
        white = ["from:", "kind:", "task:", "ts:", "REVIEW:", "VERDICT", "DONE:", "ACCEPT:", "PASS"]
        res = pair(text, text, white)
        self.assertEqual(res["ngrams"], [])
        self.assertEqual(res["whitelisted_ngrams"], 2)
        # одно небелое слово внутри фразы — восьмёрка снова совпадение
        mixed = "from kind task ts review verdict lazily done accept pass"
        res = pair(mixed, mixed, white)
        self.assertEqual(len(res["ngrams"]), 3)

    def test_empty_corpus_is_clean(self):
        d = Dirs(self)
        d.write("ours", "b.md", f"{RU_PHRASE}\n{EN_PHRASE}\n")
        rc, out = d.run()
        self.assertEqual(rc, 0)
        self.assertIn("ngrams=0 lines=0 headings=0 anchors=0", out)

    def test_excluded_corpus_files_are_not_compared(self):
        d = Dirs(self)
        d.write("corpus", "ours-copy/b.md", f"{EN_PHRASE}\n")
        d.write("ours", "b.md", f"{EN_PHRASE}\n")
        self.assertEqual(d.run()[0], 1)
        self.assertEqual(d.run("--exclude", "ours-copy")[0], 0)


class Lines(unittest.TestCase):
    def test_long_verbatim_line_is_caught_short_one_is_not(self):
        long_line = "| `supervisor-wait.sh` | --wake-on P1 | 900 |"
        res = pair(f"  {long_line}\nкоротко: ok\n", f"{long_line}\nкоротко: ok\n", n=50)
        self.assertEqual([m["line"] for m in res["lines"]], [1])

    def test_lines_without_words_do_not_count(self):
        sep = "|---------------|-----------------|----------------|"
        self.assertEqual(pair(sep, sep, n=50)["lines"], [])


class Headings(unittest.TestCase):
    def test_same_heading_order_is_flagged_without_corpus_text(self):
        d = Dirs(self)
        d.write("corpus", "a.md", "## Alpha step\nsecret corpus sentence\n## Beta step\n")
        d.write("ours", "b.md", "## alpha  STEP\nнаш текст\n## Beta step!\n")
        rc, out = d.run("--n", "50")
        self.assertEqual(rc, 1)
        self.assertIn("HEADINGS", out)
        self.assertNotIn("secret corpus sentence", out)

    def test_other_order_and_headings_in_code_are_not_flagged(self):
        res = pair("## Beta step\n## Alpha step\n", "## Alpha step\n## Beta step\n", n=50)
        self.assertEqual(res["headings"], [])
        res = pair("```\n## Alpha step\n## Beta step\n```\n", "## Alpha step\n## Beta step\n", n=50)
        self.assertEqual(res["headings"], [])


class Anchors(unittest.TestCase):
    """Канал R2: перевод не даёт общих 8-грамм, но сохраняет пути и числа в том же порядке."""

    def test_close_translation_has_no_ngrams_but_anchor_windows(self):
        res = pair(RU_CLOSE_TRANSLATION, EN_SOURCE)
        self.assertEqual(res["ngrams"], [])
        self.assertEqual(res["lines"], [])
        self.assertGreaterEqual(len(res["anchors"]), 1)

    def test_independent_rewrite_has_no_anchor_windows(self):
        res = pair(RU_INDEPENDENT, EN_SOURCE)
        self.assertEqual(res["ngrams"], [])
        self.assertEqual(res["anchors"], [])

    def test_protocol_only_text_has_no_anchor_windows(self):
        self.assertEqual(pair(PROTOCOL_ONLY_RU, PROTOCOL_ONLY_EN)["anchors"], [])
        self.assertEqual(pair(PROTOCOL_ONLY_EN, PROTOCOL_ONLY_EN)["anchors"], [])

    def test_anchor_kinds_and_protocol_names(self):
        text = ("see plugins/x/run.sh, notes.md and ledger.jsonl at ffc350e; ask coder-3 "
                "after 120 s, not 5; PLAN.md, state.md")
        got = [a for a, _ in oc.anchors_of(text, oc.Whitelist())]
        self.assertEqual(got, ["plugins/x/run.sh", "notes.md", "ffc350e", "coder-3", "120"])
        got = [a for a, _ in oc.anchors_of(text, oc.Whitelist(["coder-<n>", "notes.md"]))]
        self.assertEqual(got, ["plugins/x/run.sh", "ffc350e", "120"])

    def test_anchor_channel_is_report_only_unless_strict(self):
        d = Dirs(self)
        d.write("corpus", "a.md", EN_SOURCE)
        d.write("ours", "b.md", RU_CLOSE_TRANSLATION)
        rc, out = d.run()
        self.assertEqual(rc, 0)
        self.assertIn("ANCHORS", out)
        self.assertNotIn("event loop", out)   # чужой текст не печатается
        self.assertEqual(d.run("--anchors-strict")[0], 1)


class Cli(unittest.TestCase):
    def test_help_lists_the_parameters(self):
        out = subprocess.run([sys.executable, str(TOOL), "--help"], capture_output=True, text=True, timeout=30).stdout
        for opt in ("--corpus", "--exclude", "--whitelist", "--n", "--min-line", "--json", "--anchors-k",
                    "--anchors-strict"):
            self.assertIn(opt, out)

    def test_json_report_has_sha_and_counts(self):
        d = Dirs(self)
        d.write("corpus", "a.md", f"{RU_PHRASE}\n")
        d.write("ours", "b.md", f"{RU_PHRASE}\n")
        wl = d.write("ours", "white.txt", "# комментарий\nREVIEW:\n")
        report = d.root / "out" / "overlap-report.json"
        proc = subprocess.run([sys.executable, str(TOOL), str(d.ours / "b.md"), "--corpus", str(d.corpus),
                               "--whitelist", str(wl), "--corpus-sha", "abc1234", "--json", str(report)],
                              capture_output=True, text=True, timeout=30, cwd=str(d.root))
        self.assertEqual(proc.returncode, 1)
        data = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(data["corpus_sha"], "abc1234")
        self.assertEqual(set(data["counts"]), {"ngrams", "lines", "headings", "anchors"})
        self.assertGreaterEqual(data["counts"]["ngrams"], 1)
        self.assertEqual(data["counts"]["lines"], 1)


if __name__ == "__main__":
    unittest.main()
