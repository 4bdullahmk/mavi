from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_release.py"
SPEC = importlib.util.spec_from_file_location("mavi_release_hygiene", SCRIPT)
assert SPEC and SPEC.loader
hygiene = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(hygiene)


class ReleaseHygieneTests(unittest.TestCase):
    def test_checks_untracked_source_for_absolute_user_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fake_path = "fixture /" + "Users/" + "sampleaccount/Documents/private.txt\n"
            (root / "new-untracked.md").write_text(fake_path, encoding="utf-8")
            issues, count = hygiene.scan_source(root)
        self.assertEqual(count, 1)
        self.assertTrue(any("absolute macOS user path" in issue for issue in issues))

    def test_rejects_private_data_files_and_credential_shaped_text(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "examples.jsonl").write_text("synthetic training fixture\n", encoding="utf-8")
            fake_key = "ghp_" + ("A" * 32)
            (root / "untracked.py").write_text(f"FIXTURE = '{fake_key}'\n", encoding="utf-8")
            issues, _ = hygiene.scan_source(root)
        self.assertTrue(any("user data" in issue for issue in issues))
        self.assertTrue(any("credential-shaped" in issue for issue in issues))

    def test_rejects_symlinks_without_following_them(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root.parent / (root.name + "-outside")
            target.write_text("outside fixture", encoding="utf-8")
            try:
                (root / "linked.txt").symlink_to(target)
            except (OSError, NotImplementedError):
                target.unlink(missing_ok=True)
                self.skipTest("Symlinks are unavailable in this environment.")
            try:
                issues, count = hygiene.scan_source(root)
                self.assertEqual(count, 0)
                self.assertTrue(any("symbolic link" in issue for issue in issues))
            finally:
                target.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
