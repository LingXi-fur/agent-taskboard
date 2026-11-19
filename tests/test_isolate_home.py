"""Tests for ``atb check --test --isolate-home``."""

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

# A test command that deliberately writes into $HOME/.cache.
WRITE_HOME = (
    "python3 -c \"import os, pathlib; "
    "p = pathlib.Path(os.environ['HOME']) / '.cache' / 'x'; "
    "p.parent.mkdir(parents=True, exist_ok=True); p.write_text('x')\""
)


class IsolateHomeTests(unittest.TestCase):
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
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
        )

    def _atb(self, repo: Path, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "agent_taskboard", *args],
            cwd=str(repo), env=self._env(repo), capture_output=True, text=True, encoding="utf-8", errors="replace",
        )

    def _setup(self, base: Path, *, isolate_home: bool = False) -> Path:
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
        config = 'worktree_root = "../wts"\n'
        if isolate_home:
            config += "isolate_home = true\n"
        (repo / ".taskboard" / "config.toml").write_text(config, encoding="utf-8")
        return repo

    def _start(self, repo: Path, tid: str, test: str) -> None:
        sheet = sheet_mod.Sheet(
            id=tid, title="T", agent="opencode", status="todo",
            allow=["src/**"], test=test, body="\n## 验收\n\n- [ ] item\n",
        )
        (repo / ".taskboard" / "tasks" / f"{tid}.md").write_text(
            sheet_mod.dump(sheet), encoding="utf-8"
        )
        r = self._atb(repo, "start", tid)
        self.assertEqual(r.returncode, 0, r.stderr)

    def _last_run(self, repo: Path, tid: str) -> dict:
        path = repo / ".taskboard" / "runs" / f"{tid}.jsonl"
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        return json.loads(lines[-1])

    def test_isolate_home_detects_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            self._start(repo, "T001", WRITE_HOME)

            r = self._atb(repo, "check", "T001", "--test", "--isolate-home")
            self.assertEqual(r.returncode, 1, r.stdout)
            self.assertIn("测试写入了 HOME", r.stdout)
            self.assertIn(".cache", r.stdout)
            run = self._last_run(repo, "T001")
            self.assertTrue(run.get("home_writes"))
            self.assertIn(".cache/x", run.get("home_files", []))

    def test_isolate_home_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            self._start(repo, "T001", "python3 -c 'pass'")

            r = self._atb(repo, "check", "T001", "--test", "--isolate-home")
            self.assertEqual(r.returncode, 0, r.stdout)
            self.assertNotIn("测试写入了 HOME", r.stdout)
            run = self._last_run(repo, "T001")
            self.assertFalse(run.get("home_writes"))

    def test_isolate_home_from_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base, isolate_home=True)
            self._start(repo, "T001", WRITE_HOME)

            # no --isolate-home flag: the config default applies
            r = self._atb(repo, "check", "T001", "--test")
            self.assertEqual(r.returncode, 1, r.stdout)
            self.assertTrue(self._last_run(repo, "T001").get("home_writes"))

    def test_isolate_home_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            self._start(repo, "T001", WRITE_HOME)

            r = self._atb(repo, "check", "T001", "--test", "--isolate-home", "--json")
            self.assertEqual(r.returncode, 1, r.stdout)
            data = json.loads(r.stdout)
            self.assertTrue(data["home_writes"])
            self.assertTrue(data["test"]["home_writes"])
            self.assertIn(".cache/x", data["test"]["home_files"])


if __name__ == "__main__":
    unittest.main()
