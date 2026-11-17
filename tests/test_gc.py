"""Tests for ``atb gc`` (cleaning up leftover worktrees and branches)."""

from __future__ import annotations

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


class GcTests(unittest.TestCase):
    def _env(self, home: Path) -> dict[str, str]:
        env = os.environ.copy()
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        env["GIT_CONFIG_SYSTEM"] = os.devnull
        env["GIT_CONFIG_GLOBAL"] = os.devnull
        env["HOME"] = str(home)
        env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
        return env

    def _git(self, cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            env=self._env(cwd),
            capture_output=True,
            text=True,
            check=check,
        )

    def _atb(self, cwd: Path, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "agent_taskboard", *args],
            cwd=str(cwd),
            env=self._env(cwd),
            capture_output=True,
            text=True,
        )

    def _setup_repo(self, tmp: Path) -> Path:
        repo = tmp / "proj"
        repo.mkdir()
        self._git(repo, "init", "-q", "-b", "main")
        self._git(repo, "config", "user.name", "t")
        self._git(repo, "config", "user.email", "t@t")
        (repo / "README.md").write_text("hello\n", encoding="utf-8")
        self._git(repo, "add", "-A")
        self._git(repo, "commit", "-qm", "init")
        res = self._atb(repo, "init")
        self.assertEqual(res.returncode, 0, res.stderr)
        self._git(repo, "add", ".taskboard")
        self._git(repo, "commit", "-qm", "init taskboard")
        return repo

    def _create_started_task(
        self,
        repo: Path,
        title: str,
        *,
        commit_file: str | None = None,
        merge_into_main: bool = False,
        status: str | None = None,
        dirty_file: str | None = None,
    ) -> tuple[str, Path, str]:
        new_res = self._atb(repo, "new", title, "--agent", "gemini", "--allow", "**", "--json")
        self.assertEqual(new_res.returncode, 0, new_res.stderr)
        tid = json.loads(new_res.stdout)["id"]

        start_res = self._atb(repo, "start", tid, "--json")
        self.assertEqual(start_res.returncode, 0, start_res.stderr)
        start_data = json.loads(start_res.stdout)
        wt = Path(start_data["worktree"])
        branch = start_data["branch"]

        if commit_file is not None:
            (wt / commit_file).write_text(f"content for {tid}\n", encoding="utf-8")
            self._git(wt, "add", commit_file)
            self._git(wt, "commit", "-qm", f"{tid}: add {commit_file}")

        if merge_into_main:
            self._git(repo, "merge", "--no-ff", branch, "-qm", f"merge {tid}")

        if dirty_file is not None:
            (wt / dirty_file).write_text("uncommitted\n", encoding="utf-8")

        if status is not None:
            sheet_path = repo / ".taskboard" / "tasks" / f"{tid}.md"
            s = sheet_mod.parse(sheet_path.read_text(encoding="utf-8"))
            s.status = status
            sheet_path.write_text(sheet_mod.dump(s), encoding="utf-8")

        return tid, wt, branch

    def _branch_exists(self, repo: Path, branch: str) -> bool:
        proc = self._git(repo, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}", check=False)
        return proc.returncode == 0

    def test_gc_dry_run_and_apply_across_statuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._setup_repo(Path(tmp))

            # T001: done & merged & clean -> remove
            t1, wt1, br1 = self._create_started_task(
                repo, "Done clean", commit_file="a.txt", merge_into_main=True, status="done"
            )
            # T002: done & merged & dirty -> skip: dirty
            t2, wt2, br2 = self._create_started_task(
                repo, "Done dirty", commit_file="b.txt", merge_into_main=True, status="done",
                dirty_file="untracked.txt",
            )
            # T003: dropped & unmerged & clean -> skip: unmerged
            t3, wt3, br3 = self._create_started_task(
                repo, "Dropped unmerged", commit_file="c.txt", merge_into_main=False, status="dropped"
            )
            # T004: doing -> must never appear
            t4, wt4, br4 = self._create_started_task(
                repo, "Still doing", commit_file="d.txt", merge_into_main=False, status="doing"
            )

            # Dry-run JSON
            dry = self._atb(repo, "gc", "--json")
            self.assertEqual(dry.returncode, 0, dry.stderr)
            payload = json.loads(dry.stdout)
            self.assertFalse(payload["apply"])
            by_id = {item["id"]: item for item in payload["candidates"]}
            self.assertEqual(set(by_id), {t1, t2, t3})
            self.assertNotIn(t4, by_id)

            for item in payload["candidates"]:
                self.assertEqual(
                    set(item), {"id", "status", "worktree", "branch", "action", "reason"}
                )

            self.assertEqual(by_id[t1]["action"], "remove")
            self.assertEqual(by_id[t2]["action"], "skip: dirty")
            self.assertEqual(by_id[t3]["action"], "skip: unmerged")

            # Dry-run must leave everything intact
            for wt, br in [(wt1, br1), (wt2, br2), (wt3, br3), (wt4, br4)]:
                self.assertTrue(wt.exists())
                self.assertTrue(self._branch_exists(repo, br))

            # Apply without --include-unmerged
            applied = self._atb(repo, "gc", "--apply", "--json")
            self.assertEqual(applied.returncode, 0, applied.stderr)
            self.assertFalse(wt1.exists())
            self.assertFalse(self._branch_exists(repo, br1))
            # Task sheet status remains untouched
            t1_sheet = sheet_mod.parse(
                (repo / ".taskboard" / "tasks" / f"{t1}.md").read_text(encoding="utf-8")
            )
            self.assertEqual(t1_sheet.status, "done")
            # Dirty and unmerged and doing remain intact
            self.assertTrue(wt2.exists())
            self.assertTrue(self._branch_exists(repo, br2))
            self.assertTrue(wt3.exists())
            self.assertTrue(self._branch_exists(repo, br3))
            self.assertTrue(wt4.exists())
            self.assertTrue(self._branch_exists(repo, br4))

    def test_gc_include_unmerged_removes_only_clean_worktree_and_keeps_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._setup_repo(Path(tmp))
            t1, wt1, br1 = self._create_started_task(
                repo, "Dropped unmerged", commit_file="u.txt", merge_into_main=False, status="dropped"
            )

            dry = self._atb(repo, "gc", "--include-unmerged")
            self.assertEqual(dry.returncode, 0, dry.stderr)
            self.assertIn("remove-worktree", dry.stdout)
            self.assertIn("kept", dry.stdout)
            self.assertTrue(wt1.exists())
            self.assertTrue(self._branch_exists(repo, br1))

            res = self._atb(repo, "gc", "--include-unmerged", "--apply")
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertFalse(wt1.exists())
            self.assertTrue(self._branch_exists(repo, br1))

    def test_gc_specific_ids_and_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._setup_repo(Path(tmp))
            t1, wt1, br1 = self._create_started_task(
                repo, "Done one", commit_file="x.txt", merge_into_main=True, status="done"
            )
            t2, wt2, br2 = self._create_started_task(
                repo, "Done two", commit_file="y.txt", merge_into_main=True, status="done"
            )

            # Only clean t1
            res = self._atb(repo, "gc", t1, "--apply")
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertFalse(wt1.exists())
            self.assertTrue(wt2.exists())

            # Inspecting already-cleaned t1 explicitly reports skip: missing
            again = self._atb(repo, "gc", t1, "--json")
            self.assertEqual(again.returncode, 0, again.stderr)
            items = json.loads(again.stdout)["candidates"]
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["action"], "skip: missing")

            # Unknown task ID exits with friendly error code 2
            bad = self._atb(repo, "gc", "T999")
            self.assertEqual(bad.returncode, 2)
            self.assertIn("T999", bad.stderr)

    def test_gc_apply_partial_failure_returns_nonzero_and_continues(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._setup_repo(Path(tmp))
            # T001: valid done task
            t1, wt1, br1 = self._create_started_task(
                repo, "Healthy done", commit_file="ok.txt", merge_into_main=True, status="done"
            )
            # T002: done task whose worktree is corrupted/unregistered (git worktree remove fails)
            t2, wt2, br2 = self._create_started_task(
                repo, "Broken worktree", commit_file="bad.txt", merge_into_main=True, status="done"
            )
            # Corrupt wt2/.git so uncommitted_files fails or worktree_remove fails:
            # Remove wt2 via git worktree remove, then recreate an empty directory at wt2
            # so wt2.exists() is True, uncommitted_files() runs in repo? No, git status
            # inside an empty dir outside git repo fails -> skip: dirty.
            # Instead, lock the worktree so `git worktree remove` refuses!
            self._git(repo, "worktree", "lock", str(wt2))

            res = self._atb(repo, "gc", "--apply", "--json")
            self.assertNotEqual(res.returncode, 0)
            data = json.loads(res.stdout)
            self.assertEqual(len(data["errors"]), 1)
            self.assertEqual(data["errors"][0]["id"], t2)
            # T001 was still cleaned up despite T002 failing
            self.assertFalse(wt1.exists())
            self.assertFalse(self._branch_exists(repo, br1))
            # T002 remains locked and untouched
            self.assertTrue(wt2.exists())
            self.assertTrue(self._branch_exists(repo, br2))
            by_id = {item["id"]: item for item in data["candidates"]}
            self.assertEqual(by_id[t1]["result"], "removed")
            self.assertEqual(by_id[t2]["result"], "failed")
            self.assertEqual(data["summary"], {"removed": 1, "skipped": 0, "failed": 1})

    def test_gc_apply_text_reports_what_was_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._setup_repo(Path(tmp))
            t1, wt1, br1 = self._create_started_task(
                repo, "Done clean", commit_file="a.txt", merge_into_main=True, status="done"
            )
            t2, wt2, br2 = self._create_started_task(
                repo, "Dropped unmerged", commit_file="b.txt", merge_into_main=False, status="dropped"
            )
            res = self._atb(repo, "gc", "--apply")
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn(f"{t1} [done] removed worktree and branch", res.stdout)
            self.assertNotIn("ready to remove", res.stdout)
            self.assertIn(f"{t2} [dropped] -> skip: unmerged", res.stdout)
            self.assertIn("removed 1, skipped 1, failed 0", res.stdout)
            self.assertNotIn("dry-run only", res.stdout)

            res = self._atb(repo, "gc", "--include-unmerged", "--apply")
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn(f"{t2} [dropped] removed worktree; branch kept", res.stdout)
            self.assertTrue(self._branch_exists(repo, br2))


if __name__ == "__main__":
    unittest.main()
