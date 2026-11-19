"""atb must not take optional git locks in an agent's worktree.

``git status`` refreshes stat info in the index and writes it back under
``index.lock``.  When ``atb status``/``watch`` polls a worktree while the agent
runs ``git commit`` there, the agent's commit can fail with exit 128
("Unable to create index.lock").  atb therefore runs git with
``GIT_OPTIONAL_LOCKS=0``.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from agent_taskboard import sheet as sheet_mod

SRC = Path(__file__).resolve().parents[1] / "src"


class OptionalLockTests(unittest.TestCase):
    def _env(self, base: Path) -> dict[str, str]:
        env = os.environ.copy()
        env.pop("GIT_OPTIONAL_LOCKS", None)
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        env["GIT_CONFIG_GLOBAL"] = os.devnull
        env["HOME"] = str(base)
        env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
        return env

    def _git(self, cwd: Path, base: Path, *args: str) -> None:
        subprocess.run(["git", *args], cwd=str(cwd), env=self._env(base),
                       capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)

    def test_status_does_not_rewrite_worktree_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = base / "repo"
            repo.mkdir()
            self._git(repo, base, "init", "-q", "-b", "main")
            self._git(repo, base, "config", "user.name", "t")
            self._git(repo, base, "config", "user.email", "t@t")
            (repo / "src").mkdir()
            (repo / "src" / "app.py").write_text("1\n", encoding="utf-8")
            self._git(repo, base, "add", "-A")
            self._git(repo, base, "commit", "-qm", "base")
            (repo / ".taskboard" / "tasks").mkdir(parents=True)
            (repo / ".taskboard" / "config.toml").write_text(
                'worktree_root = "../wts"\n', encoding="utf-8")
            sheet = sheet_mod.Sheet(id="T001", title="Task", agent="opencode",
                                    status="todo", allow=["src/**"], body="\n")
            (repo / ".taskboard" / "tasks" / "T001.md").write_text(
                sheet_mod.dump(sheet), encoding="utf-8")
            env = self._env(base)
            atb = [sys.executable, "-m", "agent_taskboard"]
            start = subprocess.run(atb + ["start", "T001"], cwd=str(repo), env=env,
                                   capture_output=True, text=True, encoding="utf-8", errors="replace")
            self.assertEqual(start.returncode, 0, start.stderr)

            wt = base / "wts" / "T001"
            index = repo / ".git" / "worktrees" / "T001" / "index"
            # Same content, new mtime: the index stat info is now stale, so a
            # plain ``git status`` would refresh and rewrite the index.
            future = time.time() + 5
            os.utime(wt / "src" / "app.py", (future, future))
            old = time.time() - 60
            os.utime(index, (old, old))
            before = index.stat().st_mtime_ns

            for cmd in (["status"], ["status", "--json"]):
                proc = subprocess.run(atb + cmd, cwd=str(repo), env=env,
                                      capture_output=True, text=True, encoding="utf-8", errors="replace")
                self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(index.stat().st_mtime_ns, before,
                             "atb rewrote the worktree index (took index.lock)")


if __name__ == "__main__":
    unittest.main()
