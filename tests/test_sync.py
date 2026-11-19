"""Tests for ``atb sync`` and the sync suggestions in ``atb status``."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agent_taskboard import sheet as sheet_mod

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


class SyncTests(unittest.TestCase):
    def _env(self, base: Path) -> dict[str, str]:
        env = os.environ.copy()
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        env["GIT_CONFIG_SYSTEM"] = os.devnull
        env["GIT_CONFIG_GLOBAL"] = os.devnull
        env["HOME"] = str(base)
        env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
        return env

    def _git(self, cwd: Path, *args: str, check: bool = True):
        return subprocess.run(
            ["git", *args], cwd=str(cwd), env=self._env(cwd),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=check,
        )

    def _atb(self, repo: Path, *args: str):
        return subprocess.run(
            [sys.executable, "-m", "agent_taskboard", *args],
            cwd=str(repo), env=self._env(repo), capture_output=True,
            text=True, encoding="utf-8", errors="replace",
        )

    def _setup(self, base: Path) -> Path:
        repo = base / "repo"
        repo.mkdir()
        self._git(repo, "init", "-q", "-b", "main")
        self._git(repo, "config", "user.name", "t")
        self._git(repo, "config", "user.email", "t@t")
        (repo / "src").mkdir()
        (repo / "src" / "app.py").write_text("line1\nline2\nline3\n", encoding="utf-8")
        self._git(repo, "add", "-A")
        self._git(repo, "commit", "-qm", "base")
        (repo / ".taskboard" / "tasks").mkdir(parents=True)
        (repo / ".taskboard" / "config.toml").write_text(
            'worktree_root = "../wts"\n', encoding="utf-8"
        )
        return repo

    def _start_task(
        self, base: Path, repo: Path, tid: str = "T001", *, edit_app: bool = False
    ) -> Path:
        sheet = sheet_mod.Sheet(
            id=tid, title=f"Task {tid}", agent="opencode", status="todo",
            allow=["src/**"], test="python3 -c 'pass'",
            body="\n## 背景\n\nx\n\n## 验收\n\n- [ ] item\n",
        )
        (repo / ".taskboard" / "tasks" / f"{tid}.md").write_text(
            sheet_mod.dump(sheet), encoding="utf-8"
        )
        self.assertEqual(self._atb(repo, "start", tid).returncode, 0)
        wt = base / "wts" / tid
        if edit_app:
            (wt / "src" / "app.py").write_text(
                "line1\nline2\nTASK\n", encoding="utf-8"
            )
        else:
            (wt / "src" / "feature.py").write_text("new\n", encoding="utf-8")
        self._git(wt, "add", "-A")
        self._git(wt, "commit", "-qm", f"work {tid}")
        return wt

    def _advance_main(self, repo: Path, *, conflict: bool = False) -> str:
        if conflict:
            (repo / "src" / "app.py").write_text(
                "line1\nline2\nMAIN\n", encoding="utf-8"
            )
        else:
            (repo / "src" / "main-side.py").write_text("main\n", encoding="utf-8")
        self._git(repo, "add", "-A")
        self._git(repo, "commit", "-qm", "main advances")
        return self._git(repo, "rev-parse", "HEAD").stdout.strip()

    def _head(self, wt: Path) -> str:
        return self._git(wt, "rev-parse", "HEAD").stdout.strip()

    def _status_clean(self, wt: Path) -> bool:
        proc = self._git(wt, "status", "--porcelain", check=False)
        return proc.stdout.strip() == ""

    # ------------------------------------------------------------------ #

    def test_sync_clean_rebase(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            wt = self._start_task(base, repo)
            main_tip = self._advance_main(repo)
            before = self._head(wt)

            result = self._atb(repo, "sync", "T001")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("rebase", result.stdout)
            self.assertNotEqual(self._head(wt), before)
            # the task branch now contains the target's new commit
            self.assertEqual(
                self._git(
                    wt, "merge-base", "--is-ancestor", main_tip, "HEAD",
                    check=False,
                ).returncode,
                0,
            )
            # the target's new file landed in the worktree after the rebase
            self.assertEqual(
                (wt / "src" / "main-side.py").read_text(encoding="utf-8"),
                "main\n",
            )

    def test_sync_merge(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            wt = self._start_task(base, repo)
            main_tip = self._advance_main(repo)

            result = self._atb(repo, "sync", "T001", "--merge")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("merge", result.stdout)
            parents = self._git(
                wt, "rev-list", "--parents", "-n", "1", "HEAD"
            ).stdout.split()
            self.assertEqual(len(parents), 3, "expected a merge commit")
            self.assertEqual(
                self._git(
                    wt, "merge-base", "--is-ancestor", main_tip, "HEAD",
                    check=False,
                ).returncode,
                0,
            )

    def test_sync_conflict_aborts_and_restores(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            wt = self._start_task(base, repo, edit_app=True)
            self._advance_main(repo, conflict=True)
            before = self._head(wt)

            result = self._atb(repo, "sync", "T001")
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertIn("已恢复原状", result.stderr)
            self.assertIn("src/app.py", result.stderr)
            self.assertEqual(self._head(wt), before)
            self.assertTrue(self._status_clean(wt), "worktree left dirty")

    def test_sync_refuses_uncommitted(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            wt = self._start_task(base, repo)
            self._advance_main(repo)
            before = self._head(wt)
            (wt / "src" / "dirty.py").write_text("dirty\n", encoding="utf-8")

            result = self._atb(repo, "sync", "T001")
            self.assertEqual(result.returncode, 2, result.stdout)
            self.assertIn("stash", result.stderr)
            self.assertEqual(self._head(wt), before)

    def test_sync_dry_run_predicts_without_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            wt = self._start_task(base, repo, edit_app=True)
            self._advance_main(repo, conflict=True)
            before = self._head(wt)

            result = self._atb(repo, "sync", "T001", "--dry-run")
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertIn("未做任何改动", result.stdout)
            self.assertIn("src/app.py", result.stdout)
            self.assertEqual(self._head(wt), before)
            self.assertTrue(self._status_clean(wt))

            # a clean case predicts success and also changes nothing
            wt2 = self._start_task(base, repo, "T002")
            self._advance_main(repo)
            before2 = self._head(wt2)
            result = self._atb(repo, "sync", "T002", "--dry-run")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("干净", result.stdout)
            self.assertEqual(self._head(wt2), before2)

    def test_sync_already_up_to_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            wt = self._start_task(base, repo)
            before = self._head(wt)

            result = self._atb(repo, "sync", "T001")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("已与 main 同步", result.stdout)
            self.assertEqual(self._head(wt), before)

    def test_sync_pushed_branch_reminder(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            wt = self._start_task(base, repo)
            bare = base / "remote.git"
            subprocess.run(
                ["git", "init", "--bare", "-q", "-b", "main", str(bare)],
                env=self._env(base), check=True, capture_output=True,
            )
            self._git(repo, "remote", "add", "origin", str(bare))
            branch = "opencode/task-t001"
            self._git(wt, "push", "-u", "origin", branch)
            self._advance_main(repo)

            result = self._atb(repo, "sync", "T001")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("force-with-lease", result.stdout)
            self.assertIn("分支已在远端", result.stdout)

    def test_sync_requires_branch_and_worktree(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            sheet = sheet_mod.Sheet(
                id="T001", title="Todo", agent="opencode", status="todo",
                body="\n## 背景\n\nx\n",
            )
            (repo / ".taskboard" / "tasks" / "T001.md").write_text(
                sheet_mod.dump(sheet), encoding="utf-8"
            )

            result = self._atb(repo, "sync", "T001")
            self.assertEqual(result.returncode, 2, result.stdout)
            self.assertIn("atb start", result.stderr)

    def test_status_suggests_sync_for_first_three(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            for tid in ("T001", "T002", "T003", "T004"):
                self._start_task(base, repo, tid)

            # nothing behind: no hint at all
            fresh = self._atb(repo, "status")
            self.assertEqual(fresh.returncode, 0, fresh.stderr)
            self.assertNotIn("sync 建议", fresh.stdout)

            self._advance_main(repo)
            result = self._atb(repo, "status")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("sync 建议", result.stdout)
            self.assertIn("（共 4 个）", result.stdout)
            for tid in ("T001", "T002", "T003"):
                self.assertIn(tid, result.stdout.split("sync 建议")[1])
            self.assertNotIn("T004", result.stdout.split("sync 建议")[1])


if __name__ == "__main__":
    unittest.main()
