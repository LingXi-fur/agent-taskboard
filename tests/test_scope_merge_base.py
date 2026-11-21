"""Regression tests: scope checks diff from merge-base(target, branch).

A task branch that absorbs newer target commits (fast-forward, merge or
``atb sync``) must not have those target commits counted as task changes:
``git diff target...branch`` starts at ``merge-base(target, branch)``, not at
the base recorded when the task started.  When the target branch cannot be
resolved (e.g. it does not exist), the recorded ``sheet.base`` is used.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agent_taskboard import sheet as sheet_mod

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


class ScopeMergeBaseTests(unittest.TestCase):
    def _env(self, tmp: Path) -> dict[str, str]:
        env = os.environ.copy()
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        env["GIT_CONFIG_SYSTEM"] = os.devnull
        env["GIT_CONFIG_GLOBAL"] = os.devnull
        env["HOME"] = str(tmp)
        env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
        return env

    def _git(self, tmp: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args],
            cwd=str(tmp),
            env=self._env(tmp),
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            check=check,
        )

    def _atb(self, tmp: Path, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "agent_taskboard", *args],
            cwd=str(tmp),
            env=self._env(tmp),
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
        )

    def _setup_repo(self, tmp: Path, *, target: str = "main") -> None:
        self._git(tmp, "init", "-q", "-b", "main")
        self._git(tmp, "config", "user.name", "t")
        self._git(tmp, "config", "user.email", "t@t")
        (tmp / "src").mkdir()
        (tmp / "src" / "a.py").write_text("a\n", encoding="utf-8")
        (tmp / "DESIGN.md").write_text("# design\n", encoding="utf-8")
        self._git(tmp, "add", "-A")
        self._git(tmp, "commit", "-qm", "c1")
        (tmp / ".taskboard").mkdir()
        (tmp / ".taskboard" / "tasks").mkdir()
        (tmp / ".taskboard" / "config.toml").write_text(
            f'worktree_root = "worktrees"\ntarget = "{target}"\n', encoding="utf-8"
        )

    def _write_sheet(
        self, tmp: Path, tid: str, title: str, *,
        agent: str = "opencode", allow: tuple = ("src/**",),
        deny: tuple = ("DESIGN.md",),
    ) -> None:
        body = "\n## 背景\n\n## 要做的事\n\n## 验收\n- [ ] \n"
        sheet = sheet_mod.Sheet(
            id=tid, title=title, agent=agent, status="todo",
            allow=list(allow), deny=list(deny), depends=[],
            test="", branch="", body=body,
        )
        (tmp / ".taskboard" / "tasks" / f"{tid}.md").write_text(
            sheet_mod.dump(sheet), encoding="utf-8"
        )

    def _commit_on_target(self, tmp: Path) -> None:
        """Add an out-of-scope commit on the target branch (main worktree)."""
        docs = tmp / "docs"
        docs.mkdir(exist_ok=True)
        (docs / "new.md").write_text("target change\n", encoding="utf-8")
        self._git(tmp, "add", "-A")
        self._git(tmp, "commit", "-qm", "target: docs")

    def _commit_in_worktree(self, wt: Path, *extra_outside: str) -> None:
        """Commit one in-scope file plus optional out-of-scope files."""
        (wt / "src" / "b.py").write_text("task work\n", encoding="utf-8")
        for name in extra_outside:
            (wt / name).write_text("agent wrote this\n", encoding="utf-8")
        self._git(wt, "add", "-A")
        self._git(wt, "commit", "-qm", "task work")

    # ------------------------------------------------------------------ #

    def test_check_ignores_target_commits_absorbed_by_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            self._setup_repo(tmp)
            self._write_sheet(tmp, "T001", "任务")
            self.assertEqual(self._atb(tmp, "start", "T001").returncode, 0)
            wt = tmp / "worktrees" / "T001"

            # main gains an out-of-scope commit; the task branch fast-forwards
            # to it, then makes one in-scope commit of its own
            self._commit_on_target(tmp)
            self._git(wt, "merge", "main")
            self._commit_in_worktree(wt)

            result = self._atb(tmp, "check", "T001", "--json")
            self.assertEqual(result.returncode, 0, result.stdout)
            data = json.loads(result.stdout)
            self.assertTrue(data["ok"])
            self.assertEqual(data["denied"], [])
            self.assertEqual(data["outside"], [])
            self.assertEqual(data["changed_files"], 1)
            self.assertEqual(data["uncommitted"], [])
            # ahead/behind are relative to the target too: only the task's
            # own commit counts
            self.assertEqual(data["ahead"], 1)
            self.assertEqual(data["behind"], 0)

            # the status FILES/VIOL columns come from the same logic
            result = self._atb(tmp, "status", "--json")
            self.assertEqual(result.returncode, 0, result.stderr)
            row = json.loads(result.stdout)["tasks"][0]
            self.assertEqual(row["changed_files"], 1)
            self.assertEqual(row["violations"], 0)

            # review's diffstat counts the task's file, not the target's
            result = self._atb(tmp, "review", "T001", "--json")
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["diffstat"]["totals"]["files"], 1)

    def test_check_still_reports_own_violation_after_absorb(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            self._setup_repo(tmp)
            self._write_sheet(tmp, "T001", "任务")
            self.assertEqual(self._atb(tmp, "start", "T001").returncode, 0)
            wt = tmp / "worktrees" / "T001"

            # absorb target commits, then the agent itself writes one in-scope
            # file and one out-of-scope file
            self._commit_on_target(tmp)
            self._git(wt, "merge", "main")
            self._commit_in_worktree(wt, "outside.txt")

            result = self._atb(tmp, "check", "T001", "--json")
            self.assertEqual(result.returncode, 1, result.stdout)
            data = json.loads(result.stdout)
            self.assertFalse(data["ok"])
            self.assertEqual(data["denied"], [])
            self.assertEqual(data["outside"], ["outside.txt"])
            self.assertEqual(data["changed_files"], 2)

    def test_check_no_false_positive_after_sync_rebase(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            self._setup_repo(tmp)
            self._write_sheet(tmp, "T001", "任务")
            self.assertEqual(self._atb(tmp, "start", "T001").returncode, 0)
            wt = tmp / "worktrees" / "T001"

            self._commit_in_worktree(wt)
            self._commit_on_target(tmp)
            result = self._atb(tmp, "sync", "T001")
            self.assertEqual(result.returncode, 0, result.stderr)

            result = self._atb(tmp, "check", "T001", "--json")
            self.assertEqual(result.returncode, 0, result.stdout)
            data = json.loads(result.stdout)
            self.assertTrue(data["ok"])
            self.assertEqual(data["outside"], [])
            self.assertEqual(data["changed_files"], 1)
            self.assertEqual(data["ahead"], 1)
            self.assertEqual(data["behind"], 0)

    def test_check_no_false_positive_after_sync_merge(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            self._setup_repo(tmp)
            self._write_sheet(tmp, "T001", "任务")
            self.assertEqual(self._atb(tmp, "start", "T001").returncode, 0)
            wt = tmp / "worktrees" / "T001"

            self._commit_in_worktree(wt)
            self._commit_on_target(tmp)
            result = self._atb(tmp, "sync", "T001", "--merge")
            self.assertEqual(result.returncode, 0, result.stderr)

            result = self._atb(tmp, "check", "T001", "--json")
            self.assertEqual(result.returncode, 0, result.stdout)
            data = json.loads(result.stdout)
            self.assertTrue(data["ok"])
            self.assertEqual(data["outside"], [])
            self.assertEqual(data["changed_files"], 1)
            self.assertEqual(data["behind"], 0)

    def test_check_falls_back_to_recorded_base_without_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            self._setup_repo(tmp, target="ghost")
            self._write_sheet(tmp, "T001", "任务")
            result = self._atb(tmp, "start", "T001", "--base", "main")
            self.assertEqual(result.returncode, 0, result.stderr)
            wt = tmp / "worktrees" / "T001"

            self._commit_in_worktree(wt)

            # the target branch does not exist: fall back to sheet.base
            # without errors, still reporting the task's own change
            result = self._atb(tmp, "check", "T001", "--json")
            self.assertEqual(result.returncode, 0, result.stdout)
            data = json.loads(result.stdout)
            self.assertTrue(data["ok"])
            self.assertEqual(data["outside"], [])
            self.assertEqual(data["changed_files"], 1)
            self.assertEqual(data["ahead"], 1)
            self.assertEqual(data["behind"], 0)


if __name__ == "__main__":
    unittest.main()
