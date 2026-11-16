"""Tests for ``atb dispatch`` (per-project forwarding checklist)."""

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


class DispatchTests(unittest.TestCase):
    def _env(self, cwd: Path) -> dict[str, str]:
        env = os.environ.copy()
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        env["GIT_CONFIG_SYSTEM"] = os.devnull
        env["GIT_CONFIG_GLOBAL"] = os.devnull
        env["HOME"] = str(cwd)
        env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
        return env

    def _git(self, cwd: Path, *args: str) -> None:
        subprocess.run(
            ["git", *args], cwd=str(cwd), env=self._env(cwd),
            capture_output=True, text=True, check=True,
        )

    def _atb(self, cwd: Path, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "agent_taskboard", *args],
            cwd=str(cwd), env=self._env(cwd), capture_output=True, text=True,
        )

    def _setup(self, base: Path) -> Path:
        repo = base / "myproj"
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
        return repo

    def _write(self, repo: Path, tid: str, title: str, *, agent="opencode",
               status="todo", window="") -> None:
        sheet = sheet_mod.Sheet(
            id=tid, title=title, agent=agent, status=status, window=window
        )
        (repo / ".taskboard" / "tasks" / f"{tid}.md").write_text(
            sheet_mod.dump(sheet), encoding="utf-8"
        )

    def test_dispatch_zh_groups_and_wording(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._setup(Path(tmp))
            self._write(repo, "T001", "Add login", window="OpenCode · taskboard 窗口")
            self._write(repo, "T002", "Tweak docs", agent="claude", status="doing")
            self._write(repo, "T003", "Fix bug", agent="opencode", status="rework")
            self._write(repo, "T004", "Finished", agent="gemini", status="done")

            result = self._atb(repo, "dispatch")
            self.assertEqual(result.returncode, 0, result.stderr)
            out = result.stdout
            self.assertTrue(out.startswith("## 项目：myproj"), out)
            self.assertIn("### OpenCode · taskboard 窗口", out)
            self.assertIn("### claude", out)
            self.assertIn("### opencode", out)
            self.assertIn("| ID | 标题 | Agent | 状态 | 发这句话 |", out)
            self.assertIn("T001", out)
            self.assertIn("T002", out)
            self.assertIn("T003", out)
            self.assertNotIn("T004", out)  # done tasks are excluded
            self.assertIn("prompt T001", out)
            self.assertIn("start T001", out)  # todo: start before prompt
            self.assertIn("按交接文本执行", out)
            self.assertIn("被打回了", out)
            self.assertIn(str(repo), out)

    def test_dispatch_todo_vs_doing_wording(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._setup(Path(tmp))
            self._write(repo, "T001", "Todo task", status="todo")
            self._write(repo, "T002", "Doing task", status="doing")

            out = self._atb(repo, "dispatch").stdout
            self.assertIn("start T001", out)
            self.assertIn("prompt T001", out)
            self.assertIn("prompt T002", out)
            self.assertNotIn("start T002", out)  # doing: prompt only

            out = self._atb(repo, "dispatch", "--lang", "en").stdout
            self.assertIn("start T001", out)
            self.assertIn("prompt T001", out)
            self.assertNotIn("start T002", out)

    def test_dispatch_en(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._setup(Path(tmp))
            self._write(repo, "T001", "Add login")
            self._write(repo, "T002", "Fix bug", status="rework")

            result = self._atb(repo, "dispatch", "--lang", "en")
            self.assertEqual(result.returncode, 0, result.stderr)
            out = result.stdout
            self.assertTrue(out.startswith("## Project: myproj"), out)
            self.assertIn("| ID | Title | Agent | Status | Message |", out)
            self.assertIn("was sent back", out)
            self.assertIn("Reply in English", out)
            self.assertNotIn("项目：", out)

    def test_dispatch_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._setup(Path(tmp))
            self._write(repo, "T001", "Add login", window="W")
            self._write(repo, "T002", "Other", agent="claude", status="doing")

            result = self._atb(repo, "dispatch", "--json")
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(result.stdout)
            self.assertEqual(set(data), {"project", "groups"})
            self.assertEqual(data["project"], "myproj")
            for group in data["groups"]:
                self.assertEqual(set(group), {"name", "tasks"})
                for task in group["tasks"]:
                    self.assertEqual(
                        set(task),
                        {"id", "title", "agent", "status", "window", "message"},
                    )

    def test_dispatch_no_active_tasks(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._setup(Path(tmp))
            self._write(repo, "T001", "Finished", status="done")
            result = self._atb(repo, "dispatch")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("（没有活跃任务）", result.stdout)


if __name__ == "__main__":
    unittest.main()
