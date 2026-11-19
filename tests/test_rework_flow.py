"""End-to-end rework -> review -> done flow plus the rework prompt hint."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agent_taskboard import sheet as sheet_mod

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
BASE_CONTENT = "line1\nline2\nline3\n"


class ReworkFlowTests(unittest.TestCase):
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
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=check,
        )

    def _atb(self, repo: Path, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "agent_taskboard", *args],
            cwd=str(repo), env=self._env(repo), capture_output=True, text=True, encoding="utf-8", errors="replace",
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

    def _write_sheet(self, repo: Path, tid: str, title: str, *,
                     allow=("src/**",), status="todo", test="") -> None:
        body = "\n## 背景\n\n## 验收\n\n- [ ] item\n"
        sheet = sheet_mod.Sheet(
            id=tid, title=title, agent="opencode", status=status,
            allow=list(allow), test=test, body=body,
        )
        (repo / ".taskboard" / "tasks" / f"{tid}.md").write_text(
            sheet_mod.dump(sheet), encoding="utf-8"
        )

    def _read_sheet(self, repo: Path, tid: str) -> sheet_mod.Sheet:
        return sheet_mod.parse(
            (repo / ".taskboard" / "tasks" / f"{tid}.md").read_text(encoding="utf-8")
        )

    def _commit(self, wt: Path, relpath: str, content: str) -> None:
        (wt / relpath).write_text(content, encoding="utf-8")
        self._git(wt, "add", "-A")
        self._git(wt, "commit", "-qm", "append work")

    def test_rework_review_done_without_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            self._write_sheet(repo, "T001", "Task", test="python3 -c 'pass'")

            self.assertEqual(self._atb(repo, "start", "T001").returncode, 0)
            self.assertEqual(self._read_sheet(repo, "T001").status, "doing")

            self.assertEqual(
                self._atb(repo, "rework", "T001", "--note", "fix it").returncode, 0
            )
            self.assertEqual(self._read_sheet(repo, "T001").status, "rework")

            # append a commit on the original branch after rework
            self._commit(base / "wts" / "T001", "src/app.py", "line1\nline2\nT001\n")

            zh = self._atb(repo, "prompt", "T001", "--lang", "zh").stdout
            self.assertIn("atb check T001 --test", zh)
            en = self._atb(repo, "prompt", "T001", "--lang", "en").stdout
            self.assertIn("atb check T001 --test", en)

            # done refuses rework and points at review
            r = self._atb(repo, "done", "T001")
            self.assertEqual(r.returncode, 2, r.stdout)
            self.assertIn("atb review T001", r.stderr)

            # review straight from rework, with no start in between
            r = self._atb(repo, "review", "T001")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self._read_sheet(repo, "T001").status, "review")

            self.assertEqual(self._atb(repo, "check", "T001", "--test").returncode, 0)

            r = self._atb(repo, "done", "T001", "--merge", "--clean")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(self._read_sheet(repo, "T001").status, "done")

    def test_done_on_doing_points_at_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._setup(Path(tmp))
            self._write_sheet(repo, "T001", "Task")
            self.assertEqual(self._atb(repo, "start", "T001").returncode, 0)

            r = self._atb(repo, "done", "T001")
            self.assertEqual(r.returncode, 2, r.stdout)
            self.assertIn("atb review T001", r.stderr)

    def test_review_rejects_todo_and_done(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._setup(Path(tmp))
            self._write_sheet(repo, "T001", "Todo", status="todo")
            r = self._atb(repo, "review", "T001")
            self.assertEqual(r.returncode, 2, r.stdout)
            self.assertNotIn("Traceback", r.stderr)

            self._write_sheet(repo, "T002", "Done", status="done")
            r = self._atb(repo, "review", "T002")
            self.assertEqual(r.returncode, 2, r.stdout)


if __name__ == "__main__":
    unittest.main()
