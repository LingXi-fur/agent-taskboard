"""Tests for ``atb watch`` (status change, new commit, new sheet, timeout)."""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from agent_taskboard import sheet as sheet_mod
from agent_taskboard.cli import _watch_changes

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

    # ------------------------------------------------------------------ #
    # stall / activity / robustness
    # ------------------------------------------------------------------ #

    def _age_base_commit(self, repo: Path, epoch: float) -> None:
        stamp = f"@{int(epoch)}"
        env = self._env(repo)
        env["GIT_AUTHOR_DATE"] = stamp
        env["GIT_COMMITTER_DATE"] = stamp
        subprocess.run(
            ["git", "commit", "--amend", "--allow-empty", "--no-edit"],
            cwd=str(repo), env=env, capture_output=True, text=True, check=True,
        )

    def _branch_of(self, repo: Path, tid: str) -> str:
        return sheet_mod.parse(
            (repo / ".taskboard" / "tasks" / f"{tid}.md").read_text(encoding="utf-8")
        ).branch

    def test_stall_crosses_threshold(self):
        # Deterministic unit test: idle goes from below to above the threshold.
        prev = {"T001": {"status": "doing", "tip": "a" * 40, "dirty": 0, "idle": 100}}
        cur = {"T001": {"status": "doing", "tip": "a" * 40, "dirty": 0, "idle": 200}}
        reported: set[str] = set()
        changes = _watch_changes(
            prev, cur, ROOT, stall_seconds=120, activity=False,
            reported_stall=reported,
        )
        kinds = [c["kind"] for c in changes]
        self.assertEqual(kinds, ["stall"])
        self.assertEqual(changes[0]["id"], "T001")
        # a task is reported at most once per run
        again = _watch_changes(
            cur, cur, ROOT, stall_seconds=120, activity=False,
            reported_stall=reported,
        )
        self.assertEqual(again, [])

    def test_stall_immediate_and_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            self._age_base_commit(repo, time.time() - 7200)  # base commit 2h old
            self._write_sheet(repo, "T001", "Task")
            self.assertEqual(self._atb(repo, "start", "T001").returncode, 0)
            old = time.time() - 7200  # worktree also created 2h ago
            os.utime(base / "wts" / "T001" / ".git", (old, old))

            started = time.monotonic()
            result = self._atb(
                repo, "watch", "--interval", "0.2", "--timeout", "6", "--stall", "30"
            )
            elapsed = time.monotonic() - started
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("无活动", result.stdout)
            self.assertEqual(result.stdout.count("无活动"), 1)
            self.assertIn("已 2h 无活动（阈值 30 分钟", result.stdout)
            self.assertLess(elapsed, 4, "already-stalled tasks should report immediately")

    def test_stall_crossing_e2e(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            self._age_base_commit(repo, time.time() - 1)
            self._write_sheet(repo, "T001", "Task")
            self.assertEqual(self._atb(repo, "start", "T001").returncode, 0)

            result = self._atb(
                repo, "watch", "--interval", "0.1", "--timeout", "8",
                "--stall", str(4 / 60),
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("无活动", result.stdout)

    def test_activity_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            self._write_sheet(repo, "T001", "Task")
            self.assertEqual(self._atb(repo, "start", "T001").returncode, 0)
            wt = base / "wts" / "T001"

            rc, out, err = self._watch(
                repo, self._args() + ["--activity"],
                lambda: (wt / "src" / "new.py").write_text("x\n", encoding="utf-8"),
            )
            self.assertEqual(rc, 0, out + err)
            self.assertIn("T001 未提交改动 0 → 1", out)

    def test_sheet_error_after_three_rounds(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            self._write_sheet(repo, "T001", "Task")

            def mutate():
                (repo / ".taskboard" / "tasks" / "T002.md").write_text(
                    "+++\nthis is not valid toml\n+++\n\nbody\n", encoding="utf-8"
                )

            rc, out, err = self._watch(repo, self._args(), mutate)
            self.assertEqual(rc, 0, out + err)
            self.assertIn("T002 任务单无法解析", out)

    def test_briefly_unparsable_sheet_is_not_reported_as_new(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            self._write_sheet(repo, "T001", "Task")
            sheet = repo / ".taskboard" / "tasks" / "T001.md"
            good = sheet.read_text(encoding="utf-8")

            def mutate():
                sheet.write_text("+++\nhalf written\n", encoding="utf-8")
                time.sleep(0.5)  # a couple of rounds, fewer than three
                sheet.write_text(good, encoding="utf-8")

            rc, out, err = self._watch(
                repo, ["--interval", "0.2", "--timeout", "3"], mutate
            )
            self.assertEqual(rc, 3, out + err)
            self.assertNotIn("新增任务", out)
            self.assertNotIn("无法解析", out)

    def test_branch_deleted(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            self._write_sheet(repo, "T001", "Task")
            self.assertEqual(self._atb(repo, "start", "T001").returncode, 0)
            branch = self._branch_of(repo, "T001")

            def mutate():
                self._git(repo, "worktree", "remove", str(base / "wts" / "T001"))
                self._git(repo, "branch", "-D", branch)

            rc, out, err = self._watch(repo, self._args(), mutate)
            self.assertEqual(rc, 0, out + err)
            self.assertIn("T001 分支已删除", out)

    def test_branch_rewritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            (repo / "src" / "second.py").write_text("2\n", encoding="utf-8")
            self._git(repo, "add", "-A")
            self._git(repo, "commit", "-qm", "second")
            self._write_sheet(repo, "T001", "Task")
            self.assertEqual(self._atb(repo, "start", "T001").returncode, 0)
            wt = base / "wts" / "T001"

            def mutate():
                self._git(wt, "reset", "--hard", "HEAD~1")
                (wt / "src" / "rewritten.py").write_text("r\n", encoding="utf-8")
                self._git(wt, "add", "-A")
                self._git(wt, "commit", "-qm", "rewritten")

            rc, out, err = self._watch(repo, self._args(), mutate)
            self.assertEqual(rc, 0, out + err)
            self.assertIn("分支被改写", out)
            self.assertNotIn("新提交 0 个", out)

    def test_json_structured_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            self._write_sheet(repo, "T001", "Task")
            self.assertEqual(self._atb(repo, "start", "T001").returncode, 0)

            rc, out, err = self._watch(
                repo, self._args() + ["--json"],
                lambda: self._atb(repo, "rework", "T001", "--note", "x"),
            )
            self.assertEqual(rc, 0, out + err)
            data = json.loads(out)
            self.assertTrue(data["changed"])
            self.assertFalse(data["timeout"])
            for change in data["changes"]:
                self.assertEqual(
                    set(change), {"id", "kind", "from", "to", "text"}
                )
            kinds = {c["kind"] for c in data["changes"]}
            self.assertIn("status", kinds)


if __name__ == "__main__":
    unittest.main()
