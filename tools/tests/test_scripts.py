"""Скрипты плагина снаружи: run-engine.sh с заглушкой-движком, team-mail.sh, engine-sessions.py.

Запуск: python3 -m unittest discover -s tools/tests   (из корня репозитория)
Поведение скриптов тесты только фиксируют. Боевой HOME не читается: везде HOME=<tmp>.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parents[1] / "plugins" / "supertick" / "scripts"
DONE_WAIT_SEC = 20

STUB_ENGINE = """\
#!/usr/bin/env bash
# заглушка движка: печатает JSON-ответ с промптом внутри, как cursor-agent --output-format json
prompt="$1"
printf '{"type":"result","result":"stub reply: %s","session_id":"stub-session-1"}\\n' "$prompt"
"""

FIXED_DATE = """\
#!/usr/bin/env bash
# date с замороженным временем: два письма гарантированно в одну секунду
case "${1:-}" in
  -Is) echo "2026-10-21T20:00:00+03:00" ;;
  +%Y%m%dT%H%M%S) echo "20261021T200000" ;;
  *) exec /bin/date "$@" ;;
esac
"""


def tmpdir(case):
    tmp = tempfile.TemporaryDirectory()
    case.addCleanup(tmp.cleanup)
    return Path(tmp.name)


def executable(path, text):
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)
    return path


@unittest.skipUnless(shutil.which("setsid"), "нет setsid: run-engine.sh отцепляет движок через него")
class RunEngine(unittest.TestCase):
    def setUp(self):
        self.root = tmpdir(self)
        self.run_dir = self.root / "run"
        self.prompt = self.root / "p.md"
        self.prompt.write_text("hello engine\n", encoding="utf-8")
        self.stub = executable(self.root / "stub-engine", STUB_ENGINE)
        self.env = {**os.environ, "HOME": str(self.root)}

    def launch(self, *engine_argv):
        return subprocess.run(
            ["bash", str(SCRIPTS / "run-engine.sh"), "--role", "second-reviewer-1", "--run-dir", str(self.run_dir),
             "--prompt", str(self.prompt), "--task", "3", "--timeout", "5", "--", *engine_argv],
            capture_output=True, text=True, timeout=30, env=self.env)

    def wait_done(self, launched):
        info = dict(line.split("=", 1) for line in launched.stdout.splitlines() if line.startswith(("out=", "done=")))
        done = Path(info["done"])
        deadline = time.monotonic() + DONE_WAIT_SEC
        while not done.exists() and time.monotonic() < deadline:
            time.sleep(0.2)
        self.assertTrue(done.exists(), f".done не появился за {DONE_WAIT_SEC} с: {done}")
        return Path(info["out"]), dict(l.split("=", 1) for l in done.read_text(encoding="utf-8").splitlines() if "=" in l)

    def test_stub_engine_finishes_with_a_done_marker_and_reply(self):
        launched = self.launch(str(self.stub), "{prompt}")
        self.assertEqual(launched.returncode, 0, launched.stderr)
        out, done = self.wait_done(launched)
        self.assertEqual(out.name, "001.out")
        self.assertEqual(done["status"], "done")
        self.assertEqual(done["session"], "stub-session-1")
        self.assertIn("stub reply: hello engine", Path(done["result"]).read_text(encoding="utf-8"))
        events = [json.loads(l)["event"] for l in (self.run_dir / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(events, ["launch", "done"])
        status = subprocess.run(["bash", str(SCRIPTS / "run-engine.sh"), "--status", str(self.run_dir), "second-reviewer-1"],
                                capture_output=True, text=True, timeout=30, env=self.env).stdout
        self.assertTrue(status.startswith("DONE-UNREAD "), status)

    def test_second_launch_takes_a_new_label(self):
        first, _ = self.wait_done(self.launch(str(self.stub), "{prompt}"))
        second, _ = self.wait_done(self.launch(str(self.stub), "{prompt}"))
        self.assertEqual((first.name, second.name), ("001.out", "002.out"))

    def test_missing_binary_fails_without_done(self):
        launched = self.launch(str(self.root / "no-such-engine"), "{prompt}")
        self.assertNotEqual(launched.returncode, 0)
        self.assertIn("engine binary not found", launched.stderr)
        engine_dir = self.run_dir / "engine"
        self.assertEqual(list(engine_dir.rglob("*.done")) if engine_dir.exists() else [], [])


class TeamMail(unittest.TestCase):
    def test_two_letters_in_one_second_are_two_files(self):
        root = tmpdir(self)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        executable(bin_dir / "date", FIXED_DATE)
        run_dir = root / "run"
        run_dir.mkdir()
        env = {**os.environ, "HOME": str(root), "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"}
        paths = []
        for body in ("первое", "второе"):
            proc = subprocess.run(["bash", str(SCRIPTS / "team-mail.sh"), str(run_dir), "lead", "coder-3", "DONE",
                                   "task", "3", "--", body], capture_output=True, text=True, timeout=30, env=env)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            paths.append(Path(proc.stdout.strip()))
        self.assertEqual([p.name for p in paths], ["20261021T200000_coder-3_DONE_task3.md",
                                                   "20261021T200000_coder-3_DONE_task3-2.md"])
        self.assertEqual(sorted(p.name for p in (run_dir / "mail" / "lead").glob("*.md")), sorted(p.name for p in paths))
        self.assertEqual(paths[0].read_text(encoding="utf-8"),
                         "from: coder-3\nkind: DONE\ntask: 3\nts: 2026-10-21T20:00:00+03:00\n\nпервое\n")
        self.assertTrue(paths[1].read_text(encoding="utf-8").endswith("\nвторое\n"))


class EngineSessions(unittest.TestCase):
    def test_empty_home_finds_nothing_and_exits_0(self):
        home = tmpdir(self)
        project = home / "project"
        project.mkdir()
        proc = subprocess.run([sys.executable, str(SCRIPTS / "engine-sessions.py"), str(project)],
                              capture_output=True, text=True, timeout=30, env={**os.environ, "HOME": str(home)})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("No engine sessions found", proc.stdout)


if __name__ == "__main__":
    unittest.main()
