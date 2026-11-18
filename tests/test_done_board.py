"""End-to-end tests for ``atb done``, ``atb board --html`` and ``review --lang``."""

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agent_taskboard import sheet as sheet_mod

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

BASE_CONTENT = "line1\nline2\nline3\n"


class DoneBoardTests(unittest.TestCase):
    def _env(self, base: Path) -> dict[str, str]:
        env = os.environ.copy()
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        env["GIT_CONFIG_SYSTEM"] = os.devnull
        env["GIT_CONFIG_GLOBAL"] = os.devnull
        env["HOME"] = str(base)
        env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
        return env

    def _git(self, cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args], cwd=str(cwd), env=self._env(cwd),
            capture_output=True, text=True, check=check,
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
        (repo / "src" / "app.py").write_text(BASE_CONTENT, encoding="utf-8")
        self._git(repo, "add", "-A")
        self._git(repo, "commit", "-qm", "base")
        (repo / ".taskboard" / "tasks").mkdir(parents=True)
        (repo / ".taskboard" / "config.toml").write_text(
            'worktree_root = "../wts"\n', encoding="utf-8"
        )
        return repo

    def _write_sheet(
        self, repo: Path, tid: str, title: str, *,
        allow=("src/**",), deny=(), test="", body=None,
    ) -> None:
        if body is None:
            body = "\n## Context\n\n## Tasks\n\n## 验收\n\n- [ ] item one\n- [ ] item two\n"
        sheet = sheet_mod.Sheet(
            id=tid, title=title, agent="opencode", status="todo",
            allow=list(allow), deny=list(deny), test=test, body=body,
        )
        (repo / ".taskboard" / "tasks" / f"{tid}.md").write_text(
            sheet_mod.dump(sheet), encoding="utf-8"
        )

    def _read_sheet(self, repo: Path, tid: str) -> sheet_mod.Sheet:
        return sheet_mod.parse(
            (repo / ".taskboard" / "tasks" / f"{tid}.md").read_text(encoding="utf-8")
        )

    def _write_run(self, repo: Path, tid: str, exit_code: int) -> None:
        runs = repo / ".taskboard" / "runs"
        runs.mkdir(parents=True, exist_ok=True)
        record = {
            "timestamp": "2026-11-14T00:00:00+00:00",
            "exit_code": exit_code,
            "duration": 0.1,
            "command": "python3 -c 'pass'",
            "output": "",
        }
        with (runs / f"{tid}.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _branch_exists(self, repo: Path, name: str) -> bool:
        return self._git(repo, "show-ref", "--verify", "--quiet", f"refs/heads/{name}", check=False).returncode == 0

    def _commit_in_worktree(self, wt: Path, relpath: str, content: str) -> None:
        (wt / relpath).write_text(content, encoding="utf-8")
        self._git(wt, "add", "-A")
        self._git(wt, "commit", "-qm", "agent work")

    def _ready_task(self, base: Path, tmp_repo: Path, *, allow=("src/**",), deny=(), test="python3 -c 'pass'") -> Path:
        """Create T001, start it, commit one allowed change and move it to review."""
        self._write_sheet(tmp_repo, "T001", "Finish me", allow=allow, deny=deny, test=test)
        self.assertEqual(self._atb(tmp_repo, "start", "T001").returncode, 0)
        wt = base / "wts" / "T001"
        self._commit_in_worktree(wt, "src/app.py", "line1\nline2\nT001\n")
        self.assertEqual(self._atb(tmp_repo, "review", "T001").returncode, 0)
        return wt

    # ------------------------------------------------------------------ #

    def test_done_merge_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            self._write_sheet(repo, "T001", "Finish me", test="python3 -c 'pass'")
            self.assertEqual(self._atb(repo, "start", "T001").returncode, 0)
            wt = base / "wts" / "T001"
            self._commit_in_worktree(wt, "src/app.py", "line1\nline2\nT001\n")
            self.assertEqual(self._atb(repo, "check", "T001", "--test").returncode, 0)
            self.assertEqual(self._atb(repo, "review", "T001").returncode, 0)
            self.assertEqual(self._read_sheet(repo, "T001").status, "review")
            branch = self._read_sheet(repo, "T001").branch

            result = self._atb(repo, "done", "T001", "--merge", "--clean")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            parents = self._git(repo, "rev-list", "--parents", "-n", "1", "HEAD").stdout.split()
            self.assertEqual(len(parents), 3, "expected a --no-ff merge commit")
            self.assertFalse(wt.exists(), "worktree should be removed")
            self.assertFalse(self._branch_exists(repo, branch), "branch should be deleted")
            self.assertEqual(self._read_sheet(repo, "T001").status, "done")

    def test_done_rejects_scope_violation(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            (repo / "src" / "core.py").write_text("v1\n", encoding="utf-8")
            self._git(repo, "add", "-A")
            self._git(repo, "commit", "-qm", "add core")
            wt = self._ready_task(base, repo, deny=("src/core.py",))
            (wt / "src" / "core.py").write_text("v2\n", encoding="utf-8")
            self._git(wt, "add", "-A")
            self._git(wt, "commit", "-qm", "touch core")
            self._write_run(repo, "T001", 0)

            result = self._atb(repo, "done", "T001")
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertEqual(self._read_sheet(repo, "T001").status, "review")

    def test_done_rejects_uncommitted(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            wt = self._ready_task(base, repo)
            self._write_run(repo, "T001", 0)
            (wt / "src" / "app.py").write_text("uncommitted\n", encoding="utf-8")

            result = self._atb(repo, "done", "T001")
            self.assertEqual(result.returncode, 1, result.stdout)

    def test_done_rejects_failed_test(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            self._ready_task(base, repo)
            self._write_run(repo, "T001", 3)

            result = self._atb(repo, "done", "T001")
            self.assertEqual(result.returncode, 1, result.stdout)

    def test_done_rejects_missing_test_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            self._ready_task(base, repo)

            result = self._atb(repo, "done", "T001")
            self.assertEqual(result.returncode, 1, result.stdout)

    def test_done_force_still_rejects_violation(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            (repo / "src" / "core.py").write_text("v1\n", encoding="utf-8")
            self._git(repo, "add", "-A")
            self._git(repo, "commit", "-qm", "add core")
            self._write_sheet(repo, "T001", "Finish me", deny=("src/core.py",), test="python3 -c 'pass'")
            self.assertEqual(self._atb(repo, "start", "T001").returncode, 0)
            wt = base / "wts" / "T001"
            (wt / "src" / "core.py").write_text("v2\n", encoding="utf-8")
            self._git(wt, "add", "-A")
            self._git(wt, "commit", "-qm", "touch core")
            self._write_run(repo, "T001", 0)  # status stays 'doing' so --force applies

            result = self._atb(repo, "done", "T001", "--force")
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertEqual(self._read_sheet(repo, "T001").status, "doing")

    def test_done_merge_conflict_aborts(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            self._write_sheet(repo, "T001", "Conflict me", test="python3 -c 'pass'")
            self.assertEqual(self._atb(repo, "start", "T001").returncode, 0)
            wt = base / "wts" / "T001"
            self._commit_in_worktree(wt, "src/app.py", "line1\nT001\nline3\n")

            # target advances with a conflicting edit to the same line
            (repo / "src" / "app.py").write_text("line1\nMAIN\nline3\n", encoding="utf-8")
            self._git(repo, "add", "-A")
            self._git(repo, "commit", "-qm", "main edit")

            self.assertEqual(self._atb(repo, "review", "T001").returncode, 0)
            self._write_run(repo, "T001", 0)
            before = self._git(repo, "rev-parse", "HEAD").stdout.strip()

            result = self._atb(repo, "done", "T001", "--merge")
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertEqual(self._git(repo, "rev-parse", "HEAD").stdout.strip(), before)
            self.assertEqual(self._read_sheet(repo, "T001").status, "review")

    def test_done_merge_requires_target_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            self._ready_task(base, repo)
            self._write_run(repo, "T001", 0)
            self._git(repo, "checkout", "-q", "-b", "other")

            result = self._atb(repo, "done", "T001", "--merge")
            self.assertEqual(result.returncode, 2, result.stdout)

    def test_done_clean_requires_merge(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            self._ready_task(base, repo)
            self._write_run(repo, "T001", 0)

            result = self._atb(repo, "done", "T001", "--clean")
            self.assertEqual(result.returncode, 2, result.stdout)

    def test_board_html_escapes_and_has_no_urls(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            self._write_sheet(repo, "T001", "<script>alert(1)</script>")
            self.assertEqual(self._atb(repo, "start", "T001").returncode, 0)
            branch = self._read_sheet(repo, "T001").branch
            self._write_sheet(repo, "T002", "todo task")
            # make T001's branch vanish so the board must show "missing"
            self._git(repo, "worktree", "remove", str(base / "wts" / "T001"))
            self._git(repo, "branch", "-D", branch)

            out = base / "board.html"
            result = self._atb(repo, "board", "--html", str(out))
            self.assertEqual(result.returncode, 0, result.stderr)
            content = out.read_text(encoding="utf-8")
            self.assertIn("&lt;script&gt;", content)
            self.assertNotIn("<script>", content)
            self.assertNotIn("http://", content)
            self.assertNotIn("https://", content)
            self.assertIn("missing", content)
            self.assertIn("T002", content)

    def test_review_lang_en_has_no_chinese(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            wt = self._ready_task(base, repo)
            self._write_run(repo, "T001", 0)

            result = self._atb(repo, "review", "T001", "--lang", "en")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIsNone(re.search(r"[\u4e00-\u9fff]", result.stdout), result.stdout)
            self.assertIn("## Check results", result.stdout)
            self.assertIn("## Acceptance checklist", result.stdout)
            self.assertIn("## Review notes", result.stdout)

            result = self._atb(repo, "review", "T001", "--lang", "zh")
            self.assertIn("审查", result.stdout)

    def test_push_only_via_gitops_push_and_no_force_delete(self):
        pkg = ROOT / "src" / "agent_taskboard"
        sources = "\n".join(
            path.read_text(encoding="utf-8") for path in pkg.glob("*.py")
        )
        # Pushing is opt-in (T021): the only git push is gitops.push, it never
        # forces, and nothing else in the package spells the subcommand.
        gitops_src = (pkg / "gitops.py").read_text(encoding="utf-8")
        self.assertEqual(sources.count('"push"'), 1)
        self.assertIn('_run(["push", remote, ref], cwd=cwd, check=False)', gitops_src)
        self.assertNotIn("'push'", sources)
        for flag in ("--force", "--force-with-lease", '"-f"', "+refs"):
            self.assertNotIn(flag, gitops_src.split("def push(", 1)[1].split("\ndef ", 1)[0])
        self.assertNotIn('"-D"', sources)
        self.assertNotIn("'-D'", sources)
        self.assertIn('"branch", "-d"', sources)


if __name__ == "__main__":
    unittest.main()
