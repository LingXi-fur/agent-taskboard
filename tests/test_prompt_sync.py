"""Tests for the sync hint in ``atb prompt`` (branch behind the target)."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agent_taskboard import sheet as sheet_mod

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


class PromptSyncTests(unittest.TestCase):
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
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=True,
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
        (repo / "src" / "app.py").write_text("line\n", encoding="utf-8")
        self._git(repo, "add", "-A")
        self._git(repo, "commit", "-qm", "base")
        (repo / ".taskboard" / "tasks").mkdir(parents=True)
        (repo / ".taskboard" / "config.toml").write_text(
            'worktree_root = "../wts"\n', encoding="utf-8"
        )
        sheet = sheet_mod.Sheet(
            id="T001", title="Task T001", agent="opencode", status="todo",
            allow=["src/**"], test="python3 -c 'pass'",
            body="\n## 背景\n\nx\n\n## 验收\n\n- [ ] item\n",
        )
        (repo / ".taskboard" / "tasks" / "T001.md").write_text(
            sheet_mod.dump(sheet), encoding="utf-8"
        )
        self.assertEqual(self._atb(repo, "start", "T001").returncode, 0)
        return repo

    def _advance_main(self, repo: Path) -> None:
        (repo / "src" / "main-side.py").write_text("main\n", encoding="utf-8")
        self._git(repo, "add", "-A")
        self._git(repo, "commit", "-qm", "main advances")

    def test_prompt_no_hint_when_up_to_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._setup(Path(tmp))

            zh = self._atb(repo, "prompt", "T001")
            self.assertEqual(zh.returncode, 0, zh.stderr)
            self.assertNotIn("atb sync", zh.stdout)

    def test_prompt_zh_and_en_hint_when_behind(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._setup(Path(tmp))
            self._advance_main(repo)

            zh = self._atb(repo, "prompt", "T001")
            self.assertEqual(zh.returncode, 0, zh.stderr)
            self.assertIn("开工前先运行 `atb sync T001`", zh.stdout)
            self.assertIn("落后目标分支 1 个提交", zh.stdout)

            en = self._atb(repo, "prompt", "T001", "--lang", "en")
            self.assertEqual(en.returncode, 0, en.stderr)
            self.assertIn("run `atb sync T001` before starting", en.stdout)

            payload = self._atb(repo, "prompt", "T001", "--json")
            self.assertEqual(payload.returncode, 0, payload.stderr)
            self.assertIn("atb sync T001", payload.stdout)


if __name__ == "__main__":
    unittest.main()
