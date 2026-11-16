"""Tests for ``atb watch`` (status change, new commit, new sheet, timeout)."""

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from agent_taskboard import sheet as sheet_mod

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


class WatchTests(unittest.TestCase):
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
        (repo / "src").mkdir()
        (repo / "src" / "app.py").write_text("line1\n", encoding="utf-8")
        self._git(repo, "add", "-A")
        self._git(repo, "commit", "-qm", "base")
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

    def _watch(self, repo: Path, args: list[str], mutate, *, delay: float = 1.5):
        """Start ``atb watch``, apply ``mutate`` after ``delay``, then wait."""
        proc = subprocess.Popen(
            [sys.executable, "-m", "agent_taskboard", "watch", *args],
            cwd=str(repo), env=self._env(repo),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            time.sleep(delay)  # let the baseline snapshot settle
            mutate()
            out, err = proc.communicate(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate()
            self.fail(f"watch did not exit in time: {out!r} {err!r}")
        return proc.returncode, out, err

    def _args(self) -> list[str]:
        return ["--interval", "0.2", "--timeout", "6"]

    def test_detects_status_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            self._write_sheet(repo, "T001", "Task")
            self.assertEqual(self._atb(repo, "start", "T001").returncode, 0)

            rc, out, err = self._watch(
                repo, self._args(),
                lambda: self._atb(repo, "rework", "T001", "--note", "x"),
            )
            self.assertEqual(rc, 0, out + err)
            self.assertIn("T001 doing → rework", out)

    def test_detects_new_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            self._write_sheet(repo, "T001", "Task")
            self.assertEqual(self._atb(repo, "start", "T001").returncode, 0)
            wt = base / "wts" / "T001"

            def mutate():
                (wt / "src" / "app.py").write_text("changed\n", encoding="utf-8")
                self._git(wt, "add", "-A")
                self._git(wt, "commit", "-qm", "work")

            rc, out, err = self._watch(repo, self._args(), mutate)
            self.assertEqual(rc, 0, out + err)
            self.assertIn("T001 新提交 1 个", out)

    def test_detects_new_task_sheet(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            self._write_sheet(repo, "T001", "Task")

            rc, out, err = self._watch(
                repo, self._args(),
                lambda: self._write_sheet(repo, "T002", "Another"),
            )
            self.assertEqual(rc, 0, out + err)
            self.assertIn("T002 新增任务", out)

    def test_timeout_returns_3(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._setup(Path(tmp))
            self._write_sheet(repo, "T001", "Task")

            result = self._atb(repo, "watch", "--interval", "0.1", "--timeout", "1")
            self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
            self.assertIn("无变化", result.stdout)

    def test_timeout_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            import json

            repo = self._setup(Path(tmp))
            self._write_sheet(repo, "T001", "Task")

            result = self._atb(
                repo, "watch", "--interval", "0.1", "--timeout", "1", "--json"
            )
            self.assertEqual(result.returncode, 3, result.stdout)
            data = json.loads(result.stdout)
            self.assertFalse(data["changed"])
            self.assertTrue(data["timeout"])


if __name__ == "__main__":
    unittest.main()
