"""The handoff prompt asks agents for milestone notes (atb note)."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agent_taskboard import sheet as sheet_mod

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


def _section(text: str, heading: str) -> list[str]:
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.strip() == heading:
            start = index
            break
    if start is None:
        return []
    out = []
    for line in lines[start:]:
        if out and line.startswith("## "):
            break
        out.append(line)
    return out


class PromptNotesTests(unittest.TestCase):
    def _env(self, base: Path) -> dict[str, str]:
        env = os.environ.copy()
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        env["GIT_CONFIG_SYSTEM"] = os.devnull
        env["GIT_CONFIG_GLOBAL"] = os.devnull
        env["HOME"] = str(base)
        env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
        return env

    def _atb(self, repo: Path, *args: str):
        return subprocess.run(
            [sys.executable, "-m", "agent_taskboard", *args],
            cwd=str(repo), env=self._env(repo), capture_output=True,
            text=True, encoding="utf-8", errors="replace",
        )

    def _setup(self, base: Path) -> Path:
        repo = base / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo,
                       env=self._env(base), check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=repo,
                       env=self._env(base), check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo,
                       env=self._env(base), check=True)
        (repo / "src").mkdir()
        (repo / "src" / "app.py").write_text("line\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=repo, env=self._env(base),
                       check=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=repo,
                       env=self._env(base), check=True)
        (repo / ".taskboard" / "tasks").mkdir(parents=True)
        (repo / ".taskboard" / "config.toml").write_text(
            'worktree_root = "../wts"\n', encoding="utf-8"
        )
        sheet = sheet_mod.Sheet(
            id="T007", title="Notes demo", agent="opencode", status="todo",
            allow=["src/**"], test="python3 -c 'pass'",
            body="\n## 背景\n\nx\n\n## 验收\n\n- [ ] item\n",
        )
        (repo / ".taskboard" / "tasks" / "T007.md").write_text(
            sheet_mod.dump(sheet), encoding="utf-8"
        )
        self.assertEqual(self._atb(repo, "start", "T007").returncode, 0)
        return repo

    def test_zh_section_mentions_the_three_moments(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._setup(Path(tmp))
            result = self._atb(repo, "prompt", "T007")
            self.assertEqual(result.returncode, 0, result.stderr)
            out = result.stdout
            self.assertIn("## 进度备注", out)
            for marker in ("开工", "阻塞", "提交评审"):
                self.assertIn(marker, out)
            bullets = [
                line for line in out.splitlines()
                if line.lstrip().startswith("- ") and "atb note T007" in line
            ]
            self.assertEqual(len(bullets), 3, bullets)
            section = _section(out, "## 进度备注")
            non_empty = [line for line in section if line.strip()]
            self.assertLessEqual(len(non_empty), 6, section)
            # the rest of the prompt is still there
            self.assertIn("## 工作位置", out)
            self.assertIn("## 回报格式", out)

    def test_en_section_and_id_substitution(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._setup(Path(tmp))
            result = self._atb(repo, "prompt", "T007", "--lang", "en")
            self.assertEqual(result.returncode, 0, result.stderr)
            out = result.stdout
            self.assertIn("## Progress notes", out)
            for marker in ("when you start", "blocked", "before handing to review"):
                self.assertIn(marker, out)
            bullets = [
                line for line in out.splitlines()
                if line.lstrip().startswith("- ") and "atb note T007" in line
            ]
            self.assertEqual(len(bullets), 3, bullets)
            section = _section(out, "## Progress notes")
            non_empty = [line for line in section if line.strip()]
            self.assertLessEqual(len(non_empty), 6, section)
            self.assertIn("Do NOT push.", out)

    def test_no_note_commands_for_other_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._setup(Path(tmp))
            out = self._atb(repo, "prompt", "T007").stdout
            self.assertNotIn("atb note T001", out)
            self.assertNotIn("atb note T008", out)


if __name__ == "__main__":
    unittest.main()
