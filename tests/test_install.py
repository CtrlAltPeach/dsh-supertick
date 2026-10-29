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

    def test_installer_lists_every_packaged_skill(self):
        # The explicit list is a safety allow-list (it must not install
        # __pycache__); this test is what keeps it from drifting when a skill
        # directory is added.
        on_disk = sorted(path.name for path in (ROOT / "skills").iterdir() if path.is_dir())
        self.assertEqual(sorted(installer.NAMES), on_disk)
        packaged = {path.relative_to(ROOT / "skills").as_posix() for path in (ROOT / "skills").rglob("*") if path.is_file() and path.suffix != ".pyc"}
        self.assertEqual(set(installer.FILES), packaged)

    def test_documented_model_allowlist(self):
        for name in installer.NAMES:
            body = (ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn("name: " + name, body)
            self.assertIn("description:", body)
        skill = (ROOT / "skills/supertick/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("{{ALLOWED_MODELS}}", skill)
        self.assertIn("references/ALLOWED-MODELS.json", skill)

    def test_documented_fanout_contract(self):
        # The v0.6.0 fan-out contract must be described in every place a lead reads.
        def flat(path):
            return " ".join((ROOT / path).read_text(encoding="utf-8").split())

        skill = flat("skills/supertick/SKILL.md")
        state = flat("skills/supertick/references/STATE.MD")
        roles = flat("skills/supertick/references/ROLES.MD")
        readme = flat("README.MD")
        for name in ("planned_agents", "max_concurrent_agents", "access"):
            self.assertIn(name, state)
            self.assertIn(name, skill)
            self.assertIn(name, readme)
        self.assertIn("supervisor.py plan", skill)
        self.assertIn("--complexity", skill)
        self.assertIn("## Fan-out contract", state)
        self.assertIn("writes stay single-threaded, reads may be parallel", state)
        self.assertIn("read_agent_owns_files", readme)
        for role in ("Research / risk", "Coder", "Unified reviewer",
                     "Independent acceptance checker", "Usage auditor"):
            self.assertIn(f"## {role}", roles)
        self.assertIn("access `write`", roles)
        self.assertIn("access `read`", roles)

    def test_skill_frontmatter_survives_crlf_checkout(self):
        # A Windows git checkout (core.autocrlf=true) rewrites SKILL.md to CRLF;
        # the packer must still accept it.
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "skills"
            result = installer.install(target)
            self.assertEqual(len(result["skills"]), len(installer.NAMES))


if __name__ == "__main__":
    unittest.main()
