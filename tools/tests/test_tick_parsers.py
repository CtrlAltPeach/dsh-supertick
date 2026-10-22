"""Контракт форматов, которые разбирают наши скрипты: тик (plan_tasks, state_phase, read_mail,
pending_open) и team-wait.sh.

Запуск: python3 -m unittest discover -s tools/tests   (из корня репозитория)
Тик и team-wait.sh читают шапку письма одинаково и терпимо (ключ без регистра, `#3` и
`3 (rework)` — задача 3, CRLF), вердикт приёмки — по первой непустой строке; канон записи —
team-mail.sh. Остальные негативные случаи фиксируют поведение «как раньше». Шаблоны из
agents/coder.md, agents/unified-reviewer.md и skills/team-feature/references/phase1-planning.md
проверяются, когда эти файлы есть.
"""
import importlib.util
import re
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parents[1] / "plugins" / "supertick"
SCRIPTS = PLUGIN / "scripts"
spec = importlib.util.spec_from_file_location("supervisor_tick_contract", SCRIPTS / "supervisor-tick.py")
st = importlib.util.module_from_spec(spec)
spec.loader.exec_module(st)

NOW = datetime(2026, 10, 21, 12, 0, 0, tzinfo=timezone.utc)
CODER_MD = PLUGIN / "agents" / "coder.md"
REVIEWER_MD = PLUGIN / "agents" / "unified-reviewer.md"
PHASE1_MD = PLUGIN / "skills" / "team-feature" / "references" / "phase1-planning.md"

PLAN_SAMPLE = """\
# Plan — feature-x

## Task 1: Каркас
Status: DONE
Blocked by: none
Files to create/edit: a.py, docs/b.md

## Task 2a: Вторая часть
Status: IN_PROGRESS(coder-2)
Blocked by: 1
Files to create/edit: c.sh
"""


class Run:
    """Каталог прогона во временном каталоге: PLAN.md, state.md, почта, отчёты."""

    def __init__(self, case):
        tmp = tempfile.TemporaryDirectory()
        case.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.dir = self.root / ".claude" / "teams" / "feature-x"
        for sub in ("runs", "mail/lead", "reports"):
            (self.dir / sub).mkdir(parents=True)

    def write(self, rel, text):
        path = self.dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def letter(self, box, name, head, body="текст письма"):
        return self.write(f"mail/{box}/{name}", f"{head}\n\n{body}\n")

    def actions(self):
        return [(a["priority"], a["kind"], a["run"], a["detail"]) for a in st.tick(self.dir, self.root, NOW)["actions"]]

    def wait(self, me, *args):
        return self.wait_run(me, *args).stdout

    def wait_run(self, me, *args):
        return subprocess.run(["bash", str(SCRIPTS / "team-wait.sh"), str(self.dir), me, "--interval", "1", *args],
                              capture_output=True, text=True, timeout=30)

    def mail(self, to, frm, kind, *task):
        """Письмо каноном team-mail.sh: `task` — пусто или ("task", id)."""
        subprocess.run(["bash", str(SCRIPTS / "team-mail.sh"), str(self.dir), to, frm, kind, *task, "--", f"{kind}: текст"],
                       check=True, capture_output=True, timeout=30)

    def accept_verdict(self, text):
        """Задача 3 в ACCEPTING, отчёт приёмки побайтно из `text` → строка accept_result тика."""
        self.write("PLAN.md", "## Task 3: Каркас\nStatus: ACCEPTING(coder-3)\n")
        (self.dir / "reports" / "accept-task3.md").write_bytes(text.encode("utf-8"))
        return " ".join(a[3] for a in self.actions() if a[1] == "accept_result")

    def accept_needed(self):
        return [a[3] for a in self.actions() if a[1] == "accept_needed"]


def head_of(frm="unified-reviewer", kind="VERDICT", task="3", key_case=str.lower):
    keys = [key_case(k) for k in ("from", "kind", "task", "ts")]
    return f"{keys[0]}: {frm}\n{keys[1]}: {kind}\n{keys[2]}: {task}\n{keys[3]}: 2026-10-21T11:59:00+00:00"


class Samples(unittest.TestCase):
    """Эталонные образцы каждого формата разбираются как задумано."""

    def test_plan_tasks_reads_heading_status_and_files(self):
        r = Run(self)
        r.write("PLAN.md", PLAN_SAMPLE)
        self.assertEqual(st.plan_tasks(r.dir), {
            "1": {"status": "DONE", "files": ["a.py", "docs/b.md"]},
            "2a": {"status": "IN_PROGRESS(coder-2)", "files": ["c.sh"]}})

    def test_state_phase_reads_the_phase_line(self):
        r = Run(self)
        r.write("state.md", "# State\n\nPlugin root: /x\n## Phase: EXECUTION\n")
        self.assertEqual(st.state_phase(r.dir), "EXECUTION")

    def test_read_mail_reads_the_head_and_first_body_line(self):
        r = Run(self)
        r.letter("lead", "20261021T115900_coder-3_DONE_task3.md", head_of("coder-3", "done", "3"), "DONE: task #3\nещё")
        [m] = st.read_mail(r.dir, "lead")
        self.assertEqual((m["from"], m["kind"], m["task"], m["first"]), ("coder-3", "DONE", "3", "DONE: task #3"))

    def test_pending_open_reads_time_and_open_tail(self):
        r = Run(self)
        local = NOW.astimezone() - timedelta(minutes=3)
        r.write("pending.log", f"{local:%H:%M} coder-3 -> unified-reviewer REVIEW task 3 | OPEN\n"
                               f"{local:%H:%M} coder-4 -> lead DONE task 4 | DELIVERED\n"
                               "без времени | OPEN\n")
        got = st.pending_open(r.dir, NOW)
        self.assertEqual(len(got), 2)
        self.assertEqual(got[0][1], 180)
        self.assertIsNone(got[1][1])


class CurrentBehaviour(unittest.TestCase):
    """Негативные случаи из reports/risk-6.md, которые выравнивание парсеров не меняет."""

    def test_lowercase_kind_stuck_is_p0(self):
        r = Run(self)
        r.letter("lead", "a.md", head_of("coder-3", "STUCK", "3"))
        self.assertIn(("P0", "stuck", "coder-3"), [a[:3] for a in r.actions()])

    def test_capitalised_kind_still_wakes_team_wait(self):
        r = Run(self)
        r.letter("coder-3", "a.md", head_of(key_case=lambda k: k.capitalize() if k == "kind" else k))
        self.assertIn("MAIL:", r.wait("coder-3", "--kind", "VERDICT", "--task", "3", "--max", "2"))

    def test_task_with_hash_wakes_team_wait(self):
        r = Run(self)
        r.letter("coder-3", "a.md", head_of(task="#3"))
        self.assertIn("MAIL:", r.wait("coder-3", "--kind", "VERDICT", "--task", "3", "--max", "2"))

    def test_task_heading_with_a_dash_is_not_a_task(self):
        r = Run(self)
        r.write("PLAN.md", "## Task 3 — Каркас\nStatus: IN_PROGRESS(coder-3)\n")
        self.assertEqual(st.plan_tasks(r.dir), {})

    def test_lowercase_status_line_leaves_the_status_empty(self):
        r = Run(self)
        r.write("PLAN.md", "## Task 3: Каркас\nstatus: DONE\n")
        self.assertEqual(st.plan_tasks(r.dir)["3"]["status"], "")

    def test_pass_without_accept_prefix_gives_no_pass(self):
        r = Run(self)
        r.write("PLAN.md", "## Task 3: Каркас\nStatus: ACCEPTING(coder-3)\n")
        r.write("reports/accept-task3.md", "PASS\n")
        self.assertIn("(FAIL/?)", " ".join(a[3] for a in r.actions() if a[1] == "accept_result"))

    def test_done_without_task_is_not_task_3(self):
        r = Run(self)
        r.write("PLAN.md", "## Task 3: Каркас\nStatus: IN_PROGRESS(coder-3)\n")
        r.letter("lead", "a.md", "from: coder-3\nkind: DONE\nts: 2026-10-21T11:59:00+00:00")
        self.assertFalse([d for d in r.accept_needed() if d.startswith("task 3:")])

    def test_empty_or_blank_accept_report_gives_no_pass(self):
        for text in ("", "\n\n  \n"):
            with self.subTest(text=text):
                self.assertIn("(FAIL/?)", Run(self).accept_verdict(text))

    def test_bom_before_accept_gives_no_pass(self):
        self.assertIn("(FAIL/?)", Run(self).accept_verdict("﻿ACCEPT: task #3 — PASS\n"))

    def test_a_foreign_key_ends_the_tick_head(self):
        r = Run(self)
        r.letter("lead", "a.md", "from: coder-3\nto: lead\nkind: STUCK\ntask: 3")
        self.assertEqual(st.read_mail(r.dir, "lead")[0]["kind"], "")


class TolerantHead(unittest.TestCase):
    """Тик и team-wait одинаково читают шапку письма; вердикт приёмки — по первой непустой строке."""

    def test_capitalised_keys_are_seen_by_the_tick(self):
        r = Run(self)
        r.letter("lead", "a.md", head_of("coder-3", "STUCK", "3", key_case=str.capitalize))
        self.assertIn(("P0", "stuck", "coder-3"), [a[:3] for a in r.actions()])
        self.assertEqual(st.read_mail(r.dir, "lead")[0]["kind"], "STUCK")

    def test_kind_key_in_any_case_and_spacing_is_seen_by_the_tick(self):
        for line in ("Kind: DONE", "KIND: DONE", " kind :  DONE "):
            with self.subTest(line=line):
                r = Run(self)
                r.letter("lead", "a.md", f"from: coder-3\n{line}\ntask: 3")
                self.assertEqual(st.read_mail(r.dir, "lead")[0]["kind"], "DONE")

    def test_task_with_a_tail_wakes_team_wait(self):
        r = Run(self)
        r.letter("coder-3", "a.md", head_of(task="3 (rework)"))
        self.assertIn("MAIL:", r.wait("coder-3", "--kind", "VERDICT", "--task", "3", "--max", "2"))

    def test_spaced_task_keys_wake_team_wait(self):
        for line in (" task :  3 ", " Task : #3 "):
            with self.subTest(line=line):
                r = Run(self)
                r.letter("coder-3", "a.md", f"from: unified-reviewer\nkind: VERDICT\n{line}")
                self.assertIn("MAIL:", r.wait("coder-3", "--kind", "VERDICT", "--task", "3", "--max", "2"))

    def test_hash_in_the_task_argument_matches_a_bare_id(self):
        r = Run(self)
        r.letter("coder-3", "a.md", head_of(task="3"))
        self.assertIn("MAIL:", r.wait("coder-3", "--kind", "VERDICT", "--task", "#3", "--max", "2"))

    def test_task_id_keeps_its_case(self):
        r = Run(self)
        r.letter("coder-3", "a.md", head_of(task="2A"))
        self.assertIn("TIMEOUT", r.wait("coder-3", "--kind", "VERDICT", "--task", "2a", "--max", "2"))
        self.assertEqual(st.read_mail(r.dir, "coder-3")[0]["task"], "2A")

    def test_tick_and_team_wait_agree_on_the_task_id(self):
        is_three = {"3", "#3", "3 (rework)", " 3 "}
        for value in ("3", "#3", "3 (rework)", " 3 ", "33", "3x", "3,", "# 3", "##3", "2A"):
            with self.subTest(task=value):
                r = Run(self)
                r.letter("coder-3", "a.md", head_of(task=value))
                tick = st.read_mail(r.dir, "coder-3")[0]["task"] == "3"
                wait = "MAIL:" in r.wait("coder-3", "--task", "3", "--max", "2")
                self.assertEqual((tick, wait), (value in is_three,) * 2)

    def test_task_ids_from_the_tick(self):
        cases = {"#3": "3", "3 (rework)": "3", "# 3": "", "##3": "#3", "3,": "3,", "2A": "2A"}
        self.assertEqual({v: st.task_id(v) for v in cases}, cases)

    def test_tick_matches_a_hash_or_tail_task_to_the_plan(self):
        for value, is_three in (("#3", True), ("3 (rework)", True), ("33", False), ("3x", False)):
            with self.subTest(task=value):
                r = Run(self)
                r.write("PLAN.md", "## Task 3: Каркас\nStatus: IN_PROGRESS(coder-3)\n")
                r.letter("lead", "a.md", head_of("coder-3", "DONE", value))
                self.assertEqual(bool([d for d in r.accept_needed() if d.startswith("task 3:")]), is_three)

    def test_plan_heading_with_a_hash_is_not_the_letter_task(self):
        r = Run(self)
        r.write("PLAN.md", "## Task #3: Каркас\nStatus: IN_PROGRESS(coder-3)\n")
        r.letter("lead", "a.md", head_of("coder-3", "DONE", "#3"))
        self.assertEqual(r.accept_needed(), ["task 3: текст письма (PLAN: нет задачи)"])

    def test_a_repeated_key_the_last_one_wins(self):
        head = "from: coder-3\nkind: DONE\nKind: STUCK\ntask: 3"
        r = Run(self)
        r.letter("lead", "a.md", head)
        self.assertIn(("P0", "stuck", "coder-3"), [a[:3] for a in r.actions()])
        for kind, want in (("DONE", "TIMEOUT"), ("STUCK", "MAIL:")):
            with self.subTest(kind=kind):
                r = Run(self)
                r.letter("coder-3", "a.md", head)
                self.assertIn(want, r.wait("coder-3", "--kind", kind, "--max", "2"))

    def test_crlf_head_is_read_by_both(self):
        r = Run(self)
        (r.dir / "mail" / "coder-3").mkdir()
        (r.dir / "mail" / "coder-3" / "a.md").write_bytes(f"{head_of(task='3')}\n\nтекст\n".replace("\n", "\r\n").encode())
        self.assertEqual(st.read_mail(r.dir, "coder-3")[0]["task"], "3")
        self.assertIn("MAIL:", r.wait("coder-3", "--kind", "VERDICT", "--task", "3", "--max", "2"))

    def test_canonical_verdict_wakes_team_wait_in_any_kind_case(self):
        for kind in ("VERDICT", "verdict"):
            with self.subTest(kind=kind):
                r = Run(self)
                r.mail("coder-3", "unified-reviewer", "VERDICT", "task", "3")
                got = r.wait_run("coder-3", "--kind", kind, "--task", "3", "--max", "2")
                self.assertIn("MAIL:", got.stdout)
                self.assertEqual(got.stderr, "")

    def test_answer_without_task_wakes_only_a_wait_without_task(self):
        for args, want in (((), "MAIL:"), (("--task", "3"), "TIMEOUT")):
            with self.subTest(args=args):
                r = Run(self)
                r.mail("coder-3", "lead", "ANSWER")
                self.assertIn(want, r.wait("coder-3", "--kind", "ANSWER", *args, "--max", "2"))

    def test_only_the_first_non_empty_accept_line_counts(self):
        cases = {
            "Отчёт приёмки\nACCEPT: task #3 — FAIL\nACCEPT: task #3 — PASS\n": "FAIL/?",
            "ACCEPT: task #3 — FAIL\nACCEPT: task #3 — PASS\n": "FAIL/?",
            "\n  \nACCEPT: task #3 — PASS\n": "PASS",
            "ACCEPT: task #3 — FAIL (PASS 9 из 10)\n": "PASS",
            "  ACCEPT: task #3 — PASS\n": "FAIL/?",
            "\tACCEPT: task #3 — PASS\n": "FAIL/?",
            "\r\nACCEPT: task #3 — PASS\r\n": "PASS",
            "# Отчёт\nACCEPT: task #3 — PASS\n": "FAIL/?",
        }
        for text, want in cases.items():
            with self.subTest(text=text):
                self.assertIn(f"({want})", Run(self).accept_verdict(text))


# ---- шаблоны из текстов плагина

FENCE = re.compile(r"^(```|~~~)[^\n]*\n(.*?)^\1\s*$", re.M | re.S)
PLACEHOLDER = re.compile(r"<[^>\n]*>|\{[^}\n]*\}")
# аргумент вызова: плейсхолдер с пробелами (`{run dir}`, `<имя роли>`), строка в кавычках или слово
ARG = r"(?:\{[^}\n]*\}|<[^>\n]*>|\"[^\"\n]*\"|\S+)"
MAIL_CALL = re.compile(rf"team-mail\.sh\s+{ARG}\s+{ARG}\s+{ARG}\s+([A-Z][A-Z_]*)\b(\s+task\s+{ARG})?")


def code_blocks(text):
    return [m.group(2) for m in FENCE.finditer(text)]


def letter_heads(text):
    """Шапки писем в блоках кода: [(строки `from:` … подряд, следом пустая строка или конец блока?)]."""
    heads = []
    for block in code_blocks(text):
        lines = block.splitlines()
        for i, line in enumerate(lines):
            if not line.startswith("from:"):
                continue
            head = []
            for nxt in lines[i:]:
                if not re.match(r"^(from|kind|task|ts):", nxt):
                    break
                head.append(nxt)
            after = lines[i + len(head)] if i + len(head) < len(lines) else ""
            if any(h.startswith("kind:") for h in head):
                heads.append((head, not after.strip()))
    return heads


def fill_head(head, task="7"):
    """Подставить значения вместо `<…>`/`{…}`: task → 7, ts → время, остальное → слово."""
    out = []
    for line in head:
        key, _, value = line.partition(":")
        if key == "task":
            value = " " + PLACEHOLDER.sub(task, value.strip())
        elif key == "ts":
            value = " 2026-10-21T11:59:00+00:00" if PLACEHOLDER.search(value) else value
        else:
            value = PLACEHOLDER.sub("someone", value)
        out.append(f"{key}:{value}")
    return out


class Templates(unittest.TestCase):
    """Шаблоны писем ролей проходят team-wait.sh и read_mail, шаблоны PLAN/state — тик."""

    def check_letters(self, md, need_head_kind=None):
        text = md.read_text(encoding="utf-8")
        found = letter_heads(text)
        for head, blank_after in found:
            self.assertTrue(blank_after, f"{md.name}: после шапки {head[0]!r} нет пустой строки — канон шапки")
        heads = [fill_head(h) for h, _ in found]
        calls = MAIL_CALL.findall(text)
        self.assertTrue(heads or calls, f"{md.name}: нет ни шапки письма в блоке кода, ни вызова team-mail.sh")
        if need_head_kind:
            kinds = [h.split(":", 1)[1].strip().upper() for head in heads for h in head if h.startswith("kind:")]
            self.assertIn(need_head_kind, kinds, f"{md.name}: нет шапки письма с kind: {need_head_kind} (роль без Bash)")
        for head in heads:
            kind = next(h.split(":", 1)[1].strip() for h in head if h.startswith("kind:"))
            task = next((h.split(":", 1)[1].strip() for h in head if h.startswith("task:")), "")
            with self.subTest(head=head):
                r = Run(self)
                r.letter("coder-7", "t.md", "\n".join(head))
                [m] = st.read_mail(r.dir, "coder-7")
                self.assertEqual(m["kind"], kind.upper(), "тик не видит kind")
                args = ["--kind", kind] + (["--task", "7"] if task else [])
                self.assertIn("MAIL:", r.wait("coder-7", *args, "--max", "2"))
        for kind, has_task in calls:
            with self.subTest(call=kind):
                r = Run(self)
                extra = ["task", "7"] if has_task else []
                subprocess.run(["bash", str(SCRIPTS / "team-mail.sh"), str(r.dir), "coder-7", "someone", kind, *extra,
                                "--", f"{kind}: шаблон"], check=True, capture_output=True, timeout=30)
                self.assertEqual(st.read_mail(r.dir, "coder-7")[0]["kind"], kind)
                args = ["--kind", kind] + (["--task", "7"] if has_task else [])
                self.assertIn("MAIL:", r.wait("coder-7", *args, "--max", "2"))

    def test_the_template_finder_on_a_synthetic_role(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        md = Path(tmp.name) / "role.md"
        md.write_text("Текст.\n\n```\nfrom: unified-reviewer\nkind: VERDICT\ntask: <N>\nts: <ts из письма REVIEW>\n\n"
                      "APPROVED\n```\n\n```bash\n$S/team-mail.sh $R lead coder-<N> DONE task <N> -- \"DONE: task #<N>\"\n```\n",
                      encoding="utf-8")
        self.assertEqual(len(letter_heads(md.read_text(encoding="utf-8"))), 1)
        self.assertEqual(MAIL_CALL.findall(md.read_text(encoding="utf-8")), [("DONE", " task <N>")])
        self.check_letters(md, need_head_kind="VERDICT")

    def test_mail_calls_with_spaced_placeholders_are_found(self):
        text = ("{plugin}/scripts/team-mail.sh {run dir} lead <имя> IN_REVIEW task <id> -- \"REVIEW: task #<id>\"\n"
                "{plugin}/scripts/team-mail.sh {run dir} unified-reviewer <имя кодера> REVIEW task {task id} -- …\n"
                "team-mail.sh \"$R\" lead coder-3 DONE -- \"DONE: task #3\"\n")
        self.assertEqual(MAIL_CALL.findall(text), [("IN_REVIEW", " task <id>"), ("REVIEW", " task {task id}"), ("DONE", "")])
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        md = Path(tmp.name) / "role.md"
        md.write_text(f"```bash\n{text}```\n", encoding="utf-8")
        self.check_letters(md)

    def test_head_without_blank_line_or_without_verdict_fails(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        md = Path(tmp.name) / "role.md"
        md.write_text("```\nfrom: unified-reviewer\nkind: VERDICT\ntask: <N>\nAPPROVED\n```\n", encoding="utf-8")
        with self.assertRaises(AssertionError):
            self.check_letters(md)
        md.write_text("```\nfrom: unified-reviewer\nkind: QUESTION\ntask: <N>\n\nтекст\n```\n", encoding="utf-8")
        self.check_letters(md)
        with self.assertRaises(AssertionError):
            self.check_letters(md, need_head_kind="VERDICT")

    @unittest.skipUnless(CODER_MD.exists(), "agents/coder.md ещё не написан (задача 5)")
    def test_coder_letter_templates(self):
        self.check_letters(CODER_MD)

    @unittest.skipUnless(REVIEWER_MD.exists(), "agents/unified-reviewer.md ещё не написан (задача 5)")
    def test_reviewer_letter_templates(self):
        self.check_letters(REVIEWER_MD, need_head_kind="VERDICT")

    @unittest.skipUnless(PHASE1_MD.exists(), "phase1-planning.md ещё не написан (задача 8)")
    def test_phase1_plan_and_state_templates(self):
        blocks = code_blocks(PHASE1_MD.read_text(encoding="utf-8"))
        plans = [b for b in blocks if re.search(r"^## Task ", b, re.M)]
        states = [b for b in blocks if re.search(r"^## Phase:", b, re.M)]
        self.assertTrue(plans, "нет шаблона PLAN.md с `## Task `")
        self.assertTrue(states, "нет шаблона state.md с `## Phase:`")
        for block in plans:
            with self.subTest(plan=block[:60]):
                r = Run(self)
                r.write("PLAN.md", block)
                tasks = st.plan_tasks(r.dir)
                self.assertTrue(tasks, "тик не нашёл ни одной задачи")
                self.assertTrue(all(t["status"] for t in tasks.values()), "у задачи нет `Status:`")
            for line in (x for x in block.splitlines() if x.startswith("## Task ")):
                with self.subTest(heading=line):
                    r = Run(self)
                    r.write("PLAN.md", f"{line}\nStatus: TODO\n")
                    self.assertTrue(st.plan_tasks(r.dir), "заголовок не стал задачей")
        for block in states:
            with self.subTest(state=block[:60]):
                r = Run(self)
                r.write("state.md", block)
                self.assertTrue(st.state_phase(r.dir), "фаза не найдена")


if __name__ == "__main__":
    unittest.main()
