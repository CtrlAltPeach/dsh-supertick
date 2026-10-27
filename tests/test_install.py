import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("supertick_install", ROOT / "install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallTests(unittest.TestCase):
    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "skills"
            result = installer.install(target, dry_run=True)
            self.assertTrue(result["dry_run"])
            self.assertFalse(target.exists())

    def test_install_exact_files_and_refuse_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "skills"
            result = installer.install(target)
            self.assertEqual(result["files"], 12)
            for name in installer.NAMES:
                self.assertEqual((target / name / "LICENSE").read_bytes(), (ROOT / "LICENSE").read_bytes())
            for relative in installer.FILES:
                self.assertEqual((target / relative).read_bytes(), (ROOT / "skills" / relative).read_bytes())
            before = (target / "supertick/SKILL.md").read_bytes()
            with self.assertRaises(FileExistsError):
                installer.install(target)
            self.assertEqual((target / "supertick/SKILL.md").read_bytes(), before)

    def test_preflight_refuses_all_if_one_skill_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            (target / "supertick-interview").mkdir()
            with self.assertRaises(FileExistsError):
                installer.install(target)
            self.assertFalse((target / "supertick").exists())
            self.assertFalse((target / "supertick-conventions").exists())

    def test_target_file_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "not-a-directory"
            target.write_text("leave this alone", encoding="utf-8")
            with self.assertRaises(ValueError):
                installer.install(target)
            self.assertEqual(target.read_text(encoding="utf-8"), "leave this alone")

    def test_all_markdown_local_links_exist(self):
        import re
        paths = set((ROOT / "skills").rglob("*.md")) | set((ROOT / "skills").rglob("*.MD"))
        paths.add(ROOT / "README.MD")
        for path in paths:
            self.check_links(path, re)

    def check_links(self, path, regex):
        for target in regex.findall(r"\]\(<([^>]+)>\)", path.read_text(encoding="utf-8")):
            if "://" not in target:
                self.assertTrue((path.parent / target).is_file(), (path, target))

    def test_documented_model_allowlist(self):
        for name in installer.NAMES:
            body = (ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn("name: " + name, body)
            self.assertIn("description:", body)
        skill = (ROOT / "skills/supertick/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("{{ALLOWED_MODELS}}", skill)
        self.assertIn("references/ALLOWED-MODELS.json", skill)


if __name__ == "__main__":
    unittest.main()
