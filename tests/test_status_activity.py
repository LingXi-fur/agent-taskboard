"""Tests for the ``status`` DIRTY and IDLE columns."""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from agent_taskboard import sheet as sheet_mod
from agent_taskboard.cli import format_idle

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


class FormatIdleTests(unittest.TestCase):
    def test_buckets(self):
        self.assertEqual(format_idle(0), "0s")
        self.assertEqual(format_idle(45), "45s")
        self.assertEqual(format_idle(180), "3m")
        self.assertEqual(format_idle(3600), "1h")
        self.assertEqual(format_idle(7200), "2h")
        self.assertEqual(format_idle(86400 * 3), "3d")


class StatusActivityTests(unittest.TestCase):
    def _env(self, base: Path, extra: dict | None = None) -> dict[str, str]:
        env = os.environ.copy()
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        env["GIT_CONFIG_SYSTEM"] = os.devnull
        env["GIT_CONFIG_GLOBAL"] = os.devnull
        env["HOME"] = str(base)
        env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
        if extra:
            env.update(extra)
        return env

    def _git(self, cwd: Path, *args: str, extra_env: dict | None = None) -> None:
        subprocess.run(
            ["git", *args], cwd=str(cwd), env=self._env(cwd, extra_env),
            capture_output=True, text=True, check=True,
        )

    def _atb(self, repo: Path, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "agent_taskboard", *args],
            cwd=str(repo), env=self._env(repo), capture_output=True, text=True,
        )

    def _setup(self, base: Path, commit_epoch: float) -> Path:
        repo = base / "repo"
        repo.mkdir()
        self._git(repo, "init", "-q", "-b", "main")
        self._git(repo, "config", "user.name", "t")
        self._git(repo, "config", "user.email", "t@t")
        (repo / "src").mkdir()
        (repo / "src" / "app.py").write_text("line1\n", encoding="utf-8")
        self._git(repo, "add", "-A")
        stamp = f"@{int(commit_epoch)}"
        self._git(repo, "commit", "-qm", "base",
                  extra_env={"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp})
        (repo / ".taskboard" / "tasks").mkdir(parents=True)
        (repo / ".taskboard" / "config.toml").write_text(
            'worktree_root = "../wts"\n', encoding="utf-8"
        )
        return repo

    def _write_sheet(self, repo: Path, tid: str, title: str) -> None:
        sheet = sheet_mod.Sheet(
            id=tid, title=title, agent="opencode", status="todo",
            allow=["src/**"], body="\n## 验收\n\n- [ ] item\n",
        )
        (repo / ".taskboard" / "tasks" / f"{tid}.md").write_text(
            sheet_mod.dump(sheet), encoding="utf-8"
        )

    def test_dirty_and_idle(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            now = time.time()
            repo = self._setup(base, commit_epoch=now - 7200)  # base commit 2h old
            self._write_sheet(repo, "T001", "Task")

            self.assertEqual(self._atb(repo, "start", "T001").returncode, 0)
            # pretend the worktree was also created 2h ago
            wt_git = base / "wts" / "T001" / ".git"
            os.utime(wt_git, (now - 7200, now - 7200))

            # no uncommitted files: DIRTY 0, IDLE from the (2h old) commit
            result = self._atb(repo, "status")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("2h", result.stdout)

            data = json.loads(self._atb(repo, "status", "--json").stdout)
            row = data["tasks"][0]
            self.assertEqual(row["dirty"], 0)
            self.assertGreaterEqual(row["idle_seconds"], 7200)
            self.assertLess(row["idle_seconds"], 7400)

            # one uncommitted file, mtime set to ~1 minute ago
            wt = base / "wts" / "T001"
            extra = wt / "src" / "new.py"
            extra.write_text("x\n", encoding="utf-8")
            old = time.time() - 60
            os.utime(extra, (old, old))

            data = json.loads(self._atb(repo, "status", "--json").stdout)
            row = data["tasks"][0]
            self.assertEqual(row["dirty"], 1)
            self.assertGreaterEqual(row["idle_seconds"], 55)
            self.assertLess(row["idle_seconds"], 130)

            result = self._atb(repo, "status")
            self.assertIn("1m", result.stdout)
            self.assertNotIn("2h", result.stdout)

    def test_fresh_worktree_is_not_idle_since_old_base(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base, commit_epoch=time.time() - 7200)  # base 2h old
            self._write_sheet(repo, "T001", "Task")
            self.assertEqual(self._atb(repo, "start", "T001").returncode, 0)

            row = json.loads(self._atb(repo, "status", "--json").stdout)["tasks"][0]
            self.assertLess(row["idle_seconds"], 120, "just started, not idle for 2h")

    def test_missing_worktree_shows_dash(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base, commit_epoch=time.time() - 7200)
            self._write_sheet(repo, "T001", "Task")
            self.assertEqual(self._atb(repo, "start", "T001").returncode, 0)
            # delete the worktree branch; status must not crash and should show dashes
            self._git(repo, "worktree", "remove", str(base / "wts" / "T001"))
            branch = sheet_mod.parse(
                (repo / ".taskboard" / "tasks" / "T001.md").read_text(encoding="utf-8")
            ).branch
            self._git(repo, "branch", "-D", branch)

            result = self._atb(repo, "status", "--json")
            self.assertEqual(result.returncode, 0, result.stderr)
            row = json.loads(result.stdout)["tasks"][0]
            self.assertTrue(row["missing"])
            self.assertIsNone(row["dirty"])
            self.assertIsNone(row["idle_seconds"])


if __name__ == "__main__":
    unittest.main()
