"""Сверка токенов протокола на синтетике: маленький реестр и маленький плагин во временном каталоге.

Запуск: python3 -m unittest discover -s tools/tests   (из корня репозитория)
Совпадающие токены проходят; три порчи из сверки задачи 12 (голова без двоеточия, другое
написание токена у отправителя, `--kind` вне списка) ловятся; без --spec-dir — код 2.
"""
import importlib.util
import io
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOL = HERE.parent / "token_check.py"
spec = importlib.util.spec_from_file_location("token_check", TOOL)
tc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tc)

PROTOCOL = """\
# Реестр протокола (синтетика)

## 1. Токены сообщений

| Токен | От | Кому | Когда | Скрипт |
|---|---|---|---|---|
| `IN_REVIEW: task #<id>. Files: <список>` | кодер | ведущий | перед ревью | нет |
| `VERDICT: task #<id> — APPROVED` | `unified-reviewer` | кодер | после ревью | KIND `VERDICT` |

## 2. Команды

## 4. Форматы
| F6 | `kind:` | `IN_REVIEW`, `VERDICT` | team-mail.sh | tick |
"""

CODER = """\
# Кодер
Перед ревью ведущему: `IN_REVIEW: task #<id>. Files: <список>`.
Жди в фоне: team-wait.sh <RUN DIR> <имя> --kind VERDICT --task <id>
Ответ рецензента `VERDICT: task #<id> — APPROVED` — задача принята.
"""

REVIEWER = """\
# Рецензент
Кодеру: `VERDICT: task #<id> — APPROVED`, копия файлом:
team-mail.sh <RUN DIR> coder-1 unified-reviewer VERDICT task <id> -- "текст"
"""

LEAD = """\
# Ведущий
Кодер пишет `IN_REVIEW: task #<id>. Files: <список>` перед ревью.
"""


class TokenCheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.spec = root / "spec"
        self.plugin = root / "plugin"
        self.spec.mkdir()
        (self.spec / "protocol.md").write_text(PROTOCOL, encoding="utf-8")
        self.files = {"agents/coder.md": CODER, "agents/unified-reviewer.md": REVIEWER,
                      "skills/team-feature/SKILL.md": LEAD}
        for rel, text in self.files.items():
            self.write(rel, text)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rel, text):
        p = self.plugin / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def spoil(self, rel, old, new):
        text = self.files[rel]
        self.assertIn(old, text)
        self.write(rel, text.replace(old, new, 1))

    def run_check(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                rc = tc.main(list(argv))
            except SystemExit as e:
                rc = e.code
        return rc, out.getvalue(), err.getvalue()

    def check(self):
        return self.run_check("--spec-dir", str(self.spec), "--plugin", str(self.plugin))

    def test_matching_tokens_pass(self):
        rc, out, _ = self.check()
        self.assertEqual(rc, 0, out)
        self.assertIn("расхождений=0", out)

    def test_head_without_colon_is_caught(self):
        self.spoil("agents/unified-reviewer.md", "`VERDICT: task #", "`VERDICT task #")
        rc, out, _ = self.check()
        self.assertEqual(rc, 1)
        self.assertIn("VARIANT agents/unified-reviewer.md:2: VERDICT без двоеточия", out)
        self.assertIn("нет у стороны отправитель", out)

    def test_renamed_token_at_sender_is_caught(self):
        self.spoil("agents/coder.md", "`IN_REVIEW:", "`IN-REVIEW:")
        rc, out, _ = self.check()
        self.assertEqual(rc, 1)
        self.assertIn("TOKEN 'IN_REVIEW: task #': нет у стороны отправитель «кодер»", out)

    def test_unknown_wait_kind_is_caught(self):
        self.spoil("agents/coder.md", "--kind VERDICT", "--kind VERDIKT")
        rc, out, _ = self.check()
        self.assertEqual(rc, 1)
        self.assertIn("WAIT agents/coder.md:3: --kind 'VERDIKT' нет в списке F6", out)

    def test_wait_kind_nobody_sends_is_caught(self):
        self.spoil("agents/unified-reviewer.md", "unified-reviewer VERDICT task", "unified-reviewer REVIEW task")
        rc, out, _ = self.check()
        self.assertEqual(rc, 1)
        self.assertIn("KIND agents/unified-reviewer.md:3: 'REVIEW' нет в списке F6", out)
        self.assertIn("--kind 'VERDICT' никто не отправляет", out)

    def test_without_spec_dir_fails_loudly(self):
        rc, out, err = self.run_check("--plugin", str(self.plugin))
        self.assertEqual(rc, 2)
        self.assertIn("укажите путь: --spec-dir", err)
        self.assertNotIn("ИТОГ", out)

    def test_spec_dir_without_protocol_fails(self):
        rc, _, err = self.run_check("--spec-dir", str(self.plugin), "--plugin", str(self.plugin))
        self.assertEqual(rc, 2)
        self.assertIn("нет файла", err)

    def test_cli_without_arguments_exits_nonzero(self):
        p = subprocess.run([sys.executable, str(TOOL)], capture_output=True, text=True)
        self.assertEqual(p.returncode, 2)
        self.assertIn("--spec-dir", p.stderr)


if __name__ == "__main__":
    unittest.main()
