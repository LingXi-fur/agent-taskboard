"""Release-readiness tests: version consistency and friendly error messages."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


class _Base(unittest.TestCase):
    def _env(self, cwd: Path) -> dict[str, str]:
        env = os.environ.copy()
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        env["GIT_CONFIG_SYSTEM"] = os.devnull
        env["GIT_CONFIG_GLOBAL"] = os.devnull
        env["HOME"] = str(cwd)
        env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
        return env

    def _run(self, cwd: Path, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "agent_taskboard", *args],
            cwd=str(cwd), env=self._env(cwd), capture_output=True, text=True,
        )

    def _git(self, cwd: Path, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args], cwd=str(cwd), env=self._env(cwd),
            capture_output=True, text=True, check=True,
        )

    def _committed_repo(self, tmp: Path) -> None:
        self._git(tmp, "init", "-q", "-b", "main")
        self._git(tmp, "config", "user.name", "t")
        self._git(tmp, "config", "user.email", "t@t")
        (tmp / "f.txt").write_text("x\n", encoding="utf-8")
        self._git(tmp, "add", "-A")
        self._git(tmp, "commit", "-qm", "base")


class VersionTests(unittest.TestCase):
    def test_pyproject_matches_dunder_version(self):
        import tomllib

        from agent_taskboard import __version__

        data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(__version__, "0.1.0")
        self.assertEqual(data["project"]["version"], __version__)

    def test_get_version_matches_dunder(self):
        from agent_taskboard import __version__
        from agent_taskboard.cli import get_version

        self.assertEqual(get_version(), __version__)

    def test_cli_version(self):
        env = os.environ.copy()
        env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
        result = subprocess.run(
            [sys.executable, "-m", "agent_taskboard", "--version"],
            env=env, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("0.1.0", result.stdout)


class FriendlyErrorTests(_Base):
    def _assert_friendly(self, result: subprocess.CompletedProcess) -> None:
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn("Traceback", result.stdout)
        self.assertIn("atb: error:", result.stderr)

    def test_not_a_git_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            result = self._run(tmp, "status")
            self._assert_friendly(result)
            self.assertIn("git repository", result.stderr.lower())

    def test_no_commits(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            self._git(tmp, "init", "-q", "-b", "main")
            self._git(tmp, "config", "user.name", "t")
            self._git(tmp, "config", "user.email", "t@t")
            self.assertEqual(self._run(tmp, "init").returncode, 0)
            self.assertEqual(self._run(tmp, "new", "Task", "--agent", "opencode").returncode, 0)

            result = self._run(tmp, "start", "T001")
            self._assert_friendly(result)
            self.assertIn("no commits", result.stderr.lower())

    def test_not_initialized(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            self._committed_repo(tmp)
            result = self._run(tmp, "status")
            self._assert_friendly(result)
            self.assertIn("atb init", result.stderr)

    def test_task_not_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            self._committed_repo(tmp)
            self.assertEqual(self._run(tmp, "init").returncode, 0)
            result = self._run(tmp, "show", "T999")
            self._assert_friendly(result)
            self.assertIn("not found", result.stderr.lower())

    def test_corrupt_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            self._committed_repo(tmp)
            self.assertEqual(self._run(tmp, "init").returncode, 0)
            (tmp / ".taskboard" / "tasks" / "T001.md").write_text(
                '+++\nid = "T001"\ntitle = "x"\nstatus = "todo"\n'
                "this is not valid toml\n+++\n\nbody\n",
                encoding="utf-8",
            )
            result = self._run(tmp, "show", "T001")
            self._assert_friendly(result)
            self.assertIn("T001.md", result.stderr)
            self.assertIn("line", result.stderr.lower())


if __name__ == "__main__":
    unittest.main()
