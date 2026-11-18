"""Corrupt or unreadable task sheets must never crash the CLI."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

GOOD_SHEET = (
    '+++\nid = "T001"\ntitle = "Good"\nagent = "opencode"\nstatus = "todo"\n'
    "+++\n\n## 验收\n\n- [ ] x\n"
)


class CorruptSheetTests(unittest.TestCase):
    def _env(self, base: Path) -> dict[str, str]:
        env = os.environ.copy()
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        env["GIT_CONFIG_SYSTEM"] = os.devnull
        env["GIT_CONFIG_GLOBAL"] = os.devnull
        env["HOME"] = str(base)
        env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
        return env

    def _git(self, cwd: Path, *args: str) -> None:
        subprocess.run(
            ["git", *args], cwd=str(cwd), env=self._env(cwd),
            capture_output=True, text=True, check=True,
        )

    def _atb(self, repo: Path, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "agent_taskboard", *args],
            cwd=str(repo), env=self._env(repo), capture_output=True, text=True,
        )

    def _setup(self, base: Path) -> Path:
        repo = base / "repo"
        repo.mkdir()
        self._git(repo, "init", "-q", "-b", "main")
        self._git(repo, "config", "user.name", "t")
        self._git(repo, "config", "user.email", "t@t")
        (repo / "f.txt").write_text("x\n", encoding="utf-8")
        self._git(repo, "add", "-A")
        self._git(repo, "commit", "-qm", "base")
        (repo / ".taskboard" / "tasks").mkdir(parents=True)
        (repo / ".taskboard" / "config.toml").write_text(
            'worktree_root = "../wts"\n', encoding="utf-8"
        )
        (repo / ".taskboard" / "tasks" / "T001.md").write_text(
            GOOD_SHEET, encoding="utf-8"
        )
        return repo

    def _write_bad(self, repo: Path, task_id: str) -> None:
        # Invalid UTF-8 bytes: read_text(encoding="utf-8") raises UnicodeDecodeError.
        (repo / ".taskboard" / "tasks" / f"{task_id}.md").write_bytes(
            b'+++\nid = "T002"\n\xff\xfe\x00\n+++\n'
        )

    def test_show_reports_unreadable_sheet(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._setup(Path(tmp))
            self._write_bad(repo, "T002")

            result = self._atb(repo, "show", "T002")
            self.assertEqual(result.returncode, 2, result.stdout)
            self.assertNotIn("Traceback", result.stderr)
            self.assertIn("T002.md", result.stderr)

    def test_list_status_dispatch_skip_bad_sheet(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._setup(Path(tmp))
            self._write_bad(repo, "T002")

            for args in (["list"], ["status"], ["dispatch"]):
                result = self._atb(repo, *args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertIn("T001", result.stdout)

    def test_single_task_commands_fail_gracefully(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._setup(Path(tmp))
            self._write_bad(repo, "T002")

            for args in (["start", "T002"], ["check", "T002"], ["prompt", "T002"]):
                result = self._atb(repo, *args)
                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertNotIn("Traceback", result.stderr)
                self.assertIn("T002.md", result.stderr)

    def test_watch_survives_unreadable_sheet(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._setup(Path(tmp))
            self._write_bad(repo, "T002")

            result = self._atb(repo, "watch", "--interval", "0.1", "--timeout", "6")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            self.assertIn("T002 任务单无法解析", result.stdout)


if __name__ == "__main__":
    unittest.main()
