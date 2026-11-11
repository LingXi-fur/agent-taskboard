"""Unit tests for the git thin wrapper (``gitops.py``)."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from agent_taskboard import gitops
from agent_taskboard.errors import GitError


def _env(tmp: str) -> dict[str, str]:
    env = os.environ.copy()
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["HOME"] = tmp
    return env


def _git(tmp: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(tmp),
        env=_env(str(tmp)),
        capture_output=True,
        text=True,
        check=check,
    )


def _setup_repo(tmp: Path) -> None:
    _git(tmp, "init", "-q", "-b", "main")
    _git(tmp, "config", "user.name", "t")
    _git(tmp, "config", "user.email", "t@t")
    (tmp / "a.txt").write_text("a\n", encoding="utf-8")
    (tmp / "b.txt").write_text("b\n", encoding="utf-8")
    _git(tmp, "add", "-A")
    _git(tmp, "commit", "-qm", "c1")


class GitOpsTests(unittest.TestCase):
    def test_rev_parse(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            _setup_repo(tmp)
            head = _git(tmp, "rev-parse", "HEAD").stdout.strip()
            self.assertEqual(gitops.rev_parse("HEAD", cwd=tmp), head)
            self.assertEqual(gitops.rev_parse("main", cwd=tmp), head)

    def test_rev_parse_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            _setup_repo(tmp)
            with self.assertRaises(GitError):
                gitops.rev_parse("does-not-exist", cwd=tmp)

    def test_branch_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            _setup_repo(tmp)
            self.assertTrue(gitops.branch_exists("main", cwd=tmp))
            self.assertFalse(gitops.branch_exists("nope", cwd=tmp))

    def test_worktree_add_and_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            _setup_repo(tmp)
            wt = tmp / "wt"
            gitops.worktree_add(wt, "feature", "main", cwd=tmp)
            self.assertTrue((wt / "a.txt").exists())
            entries = gitops.worktree_list(cwd=tmp)
            branches = {e.get("branch") for e in entries}
            self.assertIn("feature", branches)
            paths = {str(Path(e["path"]).resolve()) for e in entries if "path" in e}
            self.assertIn(str(wt.resolve()), paths)

    def test_rev_list_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            _setup_repo(tmp)
            base = gitops.rev_parse("HEAD", cwd=tmp)
            (tmp / "c.txt").write_text("c\n", encoding="utf-8")
            _git(tmp, "add", "-A")
            _git(tmp, "commit", "-qm", "c2")
            ahead, behind = gitops.rev_list_count(base, "HEAD", cwd=tmp)
            self.assertEqual(ahead, 1)
            self.assertEqual(behind, 0)

    def test_merge_base(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            _setup_repo(tmp)
            base = gitops.rev_parse("HEAD", cwd=tmp)
            (tmp / "c.txt").write_text("c\n", encoding="utf-8")
            _git(tmp, "add", "-A")
            _git(tmp, "commit", "-qm", "c2")
            self.assertEqual(gitops.merge_base(base, "HEAD", cwd=tmp), base)

    def test_merge_tree_conflicts_exact_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            _setup_repo(tmp)
            _git(tmp, "checkout", "-q", "-b", "A")
            (tmp / "a.txt").write_text("A\n", encoding="utf-8")
            _git(tmp, "commit", "-qam", "A")
            _git(tmp, "checkout", "-q", "main")
            _git(tmp, "checkout", "-q", "-b", "B")
            (tmp / "a.txt").write_text("B\n", encoding="utf-8")
            _git(tmp, "commit", "-qam", "B")
            _git(tmp, "checkout", "-q", "main")
            # only the real file, never the "Auto-merging"/"CONFLICT" messages
            self.assertEqual(gitops.merge_tree_conflicts("A", "B", cwd=tmp), ["a.txt"])

    def test_merge_tree_conflicts_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            _setup_repo(tmp)
            _git(tmp, "checkout", "-q", "-b", "A")
            (tmp / "a.txt").write_text("A\n", encoding="utf-8")
            _git(tmp, "commit", "-qam", "A")
            _git(tmp, "checkout", "-q", "main")
            _git(tmp, "checkout", "-q", "-b", "B")
            (tmp / "b.txt").write_text("B\n", encoding="utf-8")
            _git(tmp, "commit", "-qam", "B")
            _git(tmp, "checkout", "-q", "main")
            self.assertEqual(gitops.merge_tree_conflicts("A", "B", cwd=tmp), [])

    def test_merge_tree_conflicts_invalid_ref_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            _setup_repo(tmp)
            with self.assertRaises(GitError):
                gitops.merge_tree_conflicts("main", "does-not-exist", cwd=tmp)

    def test_changed_files_rename_returns_both_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            _setup_repo(tmp)
            base = gitops.rev_parse("HEAD", cwd=tmp)
            _git(tmp, "mv", "a.txt", "a2.txt")
            _git(tmp, "commit", "-qm", "rename")
            files = gitops.changed_files(base, "HEAD", cwd=tmp)
            self.assertIn("a.txt", files)
            self.assertIn("a2.txt", files)

    def test_changed_files_modified_added_deleted(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            _setup_repo(tmp)
            base = gitops.rev_parse("HEAD", cwd=tmp)
            (tmp / "b.txt").write_text("b2\n", encoding="utf-8")
            (tmp / "new.txt").write_text("n\n", encoding="utf-8")
            _git(tmp, "rm", "-q", "a.txt")
            _git(tmp, "add", "-A")
            _git(tmp, "commit", "-qm", "changes")
            files = gitops.changed_files(base, "HEAD", cwd=tmp)
            self.assertIn("b.txt", files)
            self.assertIn("new.txt", files)
            self.assertIn("a.txt", files)

    def test_uncommitted_files_includes_untracked_and_rename(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            _setup_repo(tmp)
            (tmp / "b.txt").write_text("changed\n", encoding="utf-8")
            (tmp / "untracked.txt").write_text("u\n", encoding="utf-8")
            _git(tmp, "mv", "a.txt", "a2.txt")  # staged rename
            files = gitops.uncommitted_files(tmp)
            self.assertIn("a.txt", files)   # original path of the staged rename
            self.assertIn("a2.txt", files)  # new path of the staged rename
            self.assertIn("b.txt", files)   # modified
            self.assertIn("untracked.txt", files)  # untracked


if __name__ == "__main__":
    unittest.main()
