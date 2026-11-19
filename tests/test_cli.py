"""End-to-end CLI tests in a real (temporary) git repository."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


class CliTests(unittest.TestCase):
    def _env(self, tmp: str) -> dict[str, str]:
        env = os.environ.copy()
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        env["GIT_CONFIG_SYSTEM"] = os.devnull
        env["GIT_CONFIG_GLOBAL"] = os.devnull
        env["HOME"] = tmp
        env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
        return env

    def _git(self, tmp: Path, *argv: str) -> None:
        subprocess.run(
            ["git", *argv],
            cwd=str(tmp),
            env=self._env(str(tmp)),
            check=True,
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
        )

    def _atb(self, tmp: Path, *argv: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "agent_taskboard", *argv],
            cwd=str(tmp),
            env=self._env(str(tmp)),
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
        )

    def test_init_new_list_show_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            self._git(tmp, "init", "-q")

            # init
            result = self._atb(tmp, "init")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((tmp / ".taskboard" / "config.toml").exists())
            self.assertTrue((tmp / ".taskboard" / "tasks").is_dir())

            # init is idempotent and does not overwrite
            result = self._atb(tmp, "init")
            self.assertEqual(result.returncode, 0, result.stderr)

            # new with a missing dependency -> exit code 2
            result = self._atb(
                tmp, "new", "第一个任务", "--agent", "opencode",
                "--allow", "src/**", "--deny", "DESIGN.md", "--depends", "T000",
            )
            self.assertEqual(result.returncode, 2, result.stdout)

            # new (valid)
            result = self._atb(
                tmp, "new", "第一个任务", "--agent", "opencode",
                "--allow", "src/**", "--deny", "DESIGN.md",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(result.stdout.strip().endswith("T001.md"))

            # the generated sheet round-trips and carries the flags
            path = Path(result.stdout.strip())
            self.assertTrue(path.exists())
            from agent_taskboard.sheet import dump, parse
            sheet = parse(path.read_text(encoding="utf-8"))
            self.assertEqual(sheet.id, "T001")
            self.assertEqual(sheet.allow, ["src/**"])
            self.assertEqual(sheet.deny, ["DESIGN.md"])
            self.assertEqual(sheet.status, "todo")
            self.assertEqual(parse(dump(sheet)), sheet)

            # list
            result = self._atb(tmp, "list")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("T001", result.stdout)
            self.assertIn("第一个任务", result.stdout)

            # list --json
            result = self._atb(tmp, "list", "--json")
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(result.stdout)
            self.assertEqual(data[0]["id"], "T001")
            self.assertEqual(data[0]["title"], "第一个任务")

            # show
            result = self._atb(tmp, "show", "T001")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("第一个任务", result.stdout)
            self.assertIn("## 验收", result.stdout)

            # show --json
            result = self._atb(tmp, "show", "T001", "--json")
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(result.stdout)
            self.assertEqual(data["id"], "T001")
            self.assertEqual(data["allow"], ["src/**"])
            self.assertIn("body", data)

    def test_new_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            self._git(tmp, "init", "-q")
            self._atb(tmp, "init")
            result = self._atb(
                tmp, "new", "标题A", "--agent", "claude", "--json"
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(result.stdout)
            self.assertEqual(data["id"], "T001")
            self.assertEqual(data["title"], "标题A")
            self.assertTrue(data["path"].endswith("T001.md"))

    def test_show_missing_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            self._git(tmp, "init", "-q")
            self._atb(tmp, "init")
            result = self._atb(tmp, "show", "T999")
            self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
