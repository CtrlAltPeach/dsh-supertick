"""tools/run_overlap.sh снаружи: откуда берётся SHA корпуса в JSON-отчёте.

Запуск: python3 -m unittest discover -s tools/tests   (из корня репозитория)
Корпус — синтетика во временном каталоге вне любого репозитория: git-клон с одним коммитом
и каталог без git. GIT_DIR/GIT_WORK_TREE из окружения убраны, глобальный конфиг git не нужен.
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
WRAPPER = HERE.parents[1] / "tools" / "run_overlap.sh"
ENV = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")}
GIT_ID = ["-c", "user.name=test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false"]


def tmpdir(case):
    tmp = tempfile.TemporaryDirectory()
    case.addCleanup(tmp.cleanup)
    return Path(tmp.name)


def git(corpus, *args):
    return subprocess.run(["git", *GIT_ID, "-C", str(corpus), *args], capture_output=True, text=True,
                          timeout=30, env=ENV, check=True).stdout.strip()


@unittest.skipUnless(shutil.which("git"), "нет git")
class RunOverlap(unittest.TestCase):
    def setUp(self):
        self.root = tmpdir(self)
        self.corpus = self.root / "corpus"
        self.corpus.mkdir()
        (self.corpus / "a.md").write_text("# Заголовок корпуса\n\nКороткий текст синтетического корпуса.\n",
                                          encoding="utf-8")
        self.report = self.root / "out" / "report.json"

    def in_git(self):
        git(self.corpus, "init", "-q")
        git(self.corpus, "add", "a.md")
        git(self.corpus, "commit", "-q", "-m", "корпус")
        return git(self.corpus, "rev-parse", "HEAD")

    def run_wrapper(self, *args):
        # cwd — временный каталог: обёртка обязана работать из любого места
        return subprocess.run(["bash", str(WRAPPER), "--corpus", str(self.corpus), *args,
                               "--json", str(self.report)],
                              capture_output=True, text=True, timeout=120, cwd=str(self.root), env=ENV)

    def reported_sha(self, proc):
        self.assertIn(proc.returncode, (0, 1), proc.stderr)
        return json.loads(self.report.read_text(encoding="utf-8"))["corpus_sha"]

    def test_corpus_in_git_reports_its_head(self):
        head = self.in_git()
        self.assertEqual(self.reported_sha(self.run_wrapper()), head)

    def test_corpus_in_git_with_matching_sha_works(self):
        head = self.in_git()
        self.assertEqual(self.reported_sha(self.run_wrapper("--corpus-sha", head[:7])), head)

    def test_corpus_without_git_takes_explicit_sha(self):
        self.assertEqual(self.reported_sha(self.run_wrapper("--corpus-sha", "abc1234")), "abc1234")

    def test_corpus_without_git_takes_explicit_sha_with_equals(self):
        self.assertEqual(self.reported_sha(self.run_wrapper("--corpus-sha=abc1234")), "abc1234")

    def test_corpus_without_git_and_without_sha_fails(self):
        proc = self.run_wrapper()
        self.assertEqual(proc.returncode, 2)
        self.assertIn("--corpus-sha", proc.stderr)
        self.assertIn("git", proc.stderr)
        self.assertFalse(self.report.exists())

    def test_corpus_in_git_with_other_sha_fails(self):
        self.in_git()
        proc = self.run_wrapper("--corpus-sha", "abc1234")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("расходится", proc.stderr)
        self.assertFalse(self.report.exists())

    def test_without_corpus_fails(self):
        proc = subprocess.run(["bash", str(WRAPPER)], capture_output=True, text=True, timeout=30,
                              cwd=str(self.root), env=ENV)
        self.assertEqual(proc.returncode, 2)


if __name__ == "__main__":
    unittest.main()
