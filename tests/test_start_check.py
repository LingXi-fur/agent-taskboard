"""End-to-end tests for ``atb start``, ``atb prompt`` and ``atb check``."""

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


class StartPromptCheckTests(unittest.TestCase):
    def _env(self, tmp: Path) -> dict[str, str]:
        env = os.environ.copy()
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        env["GIT_CONFIG_SYSTEM"] = os.devnull
        env["GIT_CONFIG_GLOBAL"] = os.devnull
        env["HOME"] = str(tmp)
        env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
        return env

    def _git(self, tmp: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args],
            cwd=str(tmp),
            env=self._env(tmp),
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            check=check,
        )

    def _atb(self, tmp: Path, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "agent_taskboard", *args],
            cwd=str(tmp),
            env=self._env(tmp),
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
        )

    def _setup_repo(self, tmp: Path, *, test_timeout: int | None = None) -> None:
        self._git(tmp, "init", "-q", "-b", "main")
        self._git(tmp, "config", "user.name", "t")
        self._git(tmp, "config", "user.email", "t@t")
        (tmp / "src").mkdir()
        (tmp / "src" / "a.py").write_text("a\n", encoding="utf-8")
        (tmp / "DESIGN.md").write_text("# design\n", encoding="utf-8")
        self._git(tmp, "add", "-A")
        self._git(tmp, "commit", "-qm", "c1")
        (tmp / ".taskboard").mkdir()
        (tmp / ".taskboard" / "tasks").mkdir()
        timeout = f"\ntest_timeout = {test_timeout}" if test_timeout is not None else ""
        (tmp / ".taskboard" / "config.toml").write_text(
            'worktree_root = "worktrees"' + timeout + "\n", encoding="utf-8"
        )

    def _write_sheet(
        self, tmp: Path, tid: str, title: str, *,
        agent: str = "opencode", allow: tuple = ("src/**",),
        deny: tuple = ("DESIGN.md",), depends: tuple = (), test: str = "",
        status: str = "todo", branch: str = "", body: str | None = None,
    ) -> None:
        if body is None:
            body = "\n## 背景\n\n## 要做的事\n\n## 验收\n- [ ] \n"
        sheet = sheet_mod.Sheet(
            id=tid, title=title, agent=agent, status=status,
            allow=list(allow), deny=list(deny), depends=list(depends),
            test=test, branch=branch, body=body,
        )
        (tmp / ".taskboard" / "tasks" / f"{tid}.md").write_text(
            sheet_mod.dump(sheet), encoding="utf-8"
        )

    def _set_status(self, tmp: Path, tid: str, status: str) -> None:
        path = tmp / ".taskboard" / "tasks" / f"{tid}.md"
        sheet = sheet_mod.parse(path.read_text(encoding="utf-8"))
        sheet.status = status
        path.write_text(sheet_mod.dump(sheet), encoding="utf-8")

    def _set_test(self, tmp: Path, tid: str, test: str) -> None:
        path = tmp / ".taskboard" / "tasks" / f"{tid}.md"
        sheet = sheet_mod.parse(path.read_text(encoding="utf-8"))
        sheet.test = test
        path.write_text(sheet_mod.dump(sheet), encoding="utf-8")

    def _wt_count(self, tmp: Path) -> int:
        out = self._git(tmp, "worktree", "list", "--porcelain").stdout
        return sum(1 for line in out.splitlines() if line.startswith("worktree "))

    def _read_runs(self, tmp: Path, tid: str) -> list[dict]:
        path = tmp / ".taskboard" / "runs" / f"{tid}.jsonl"
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    # ------------------------------------------------------------------ #

    def test_start_check_reports_denied_and_outside(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            self._setup_repo(tmp)
            self._write_sheet(tmp, "T001", "任务一", allow=("src/**",), deny=("DESIGN.md",))

            result = self._atb(tmp, "start", "T001")
            self.assertEqual(result.returncode, 0, result.stderr)
            wt = tmp / "worktrees" / "T001"
            self.assertTrue(wt.exists())

            # modify an allowed file, a denied file, and add an out-of-scope file
            (wt / "src" / "a.py").write_text("changed\n", encoding="utf-8")
            (wt / "DESIGN.md").write_text("# changed\n", encoding="utf-8")
            (wt / "outside.txt").write_text("x\n", encoding="utf-8")
            self._git(wt, "add", "-A")
            self._git(wt, "commit", "-qm", "agent work")

            result = self._atb(tmp, "check", "T001")
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertIn("DESIGN.md", result.stdout)
            self.assertIn("outside.txt", result.stdout)

            result = self._atb(tmp, "check", "T001", "--json")
            data = json.loads(result.stdout)
            self.assertEqual(data["denied"], ["DESIGN.md"])
            self.assertEqual(data["outside"], ["outside.txt"])
            self.assertFalse(data["ok"])

    def test_check_detects_rename_out_of_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            self._setup_repo(tmp)
            self._write_sheet(tmp, "T001", "任务", allow=("src/**",), deny=())
            self.assertEqual(self._atb(tmp, "start", "T001").returncode, 0)
            wt = tmp / "worktrees" / "T001"
            (wt / "other").mkdir()
            self._git(wt, "mv", "src/a.py", "other/a.py")
            self._git(wt, "commit", "-qm", "move out of scope")

            result = self._atb(tmp, "check", "T001", "--json")
            self.assertEqual(result.returncode, 1)
            data = json.loads(result.stdout)
            self.assertIn("other/a.py", data["outside"])
            self.assertNotIn("src/a.py", data["outside"])
            self.assertNotIn("src/a.py", data["denied"])

    def test_dependency_force_restart_rework(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            self._setup_repo(tmp)
            self._write_sheet(tmp, "T001", "被依赖", allow=("src/**",))
            self._write_sheet(tmp, "T002", "依赖者", allow=("src/**",), depends=("T001",))

            # dependency not done -> exit 1
            result = self._atb(tmp, "start", "T002")
            self.assertEqual(result.returncode, 1, result.stdout)

            # --force allows
            result = self._atb(tmp, "start", "T002", "--force")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((tmp / "worktrees" / "T002").exists())

            # while doing, start is rejected (status not todo/rework) -> exit 2
            result = self._atb(tmp, "start", "T002")
            self.assertEqual(result.returncode, 2, result.stdout)

            # independent task for the restart / rework scenarios
            self._write_sheet(tmp, "T003", "独立", allow=("src/**",))
            self.assertEqual(self._atb(tmp, "start", "T003").returncode, 0)

            # reset to todo but branch already exists -> exit 2
            self._set_status(tmp, "T003", "todo")
            result = self._atb(tmp, "start", "T003")
            self.assertEqual(result.returncode, 2, result.stdout)
            self.assertIn("already exists", result.stderr)

            # rework reuses the existing worktree
            self._set_status(tmp, "T003", "rework")
            before = self._wt_count(tmp)
            result = self._atb(tmp, "start", "T003")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self._wt_count(tmp), before)
            sheet = sheet_mod.parse(
                (tmp / ".taskboard" / "tasks" / "T003.md").read_text(encoding="utf-8")
            )
            self.assertEqual(sheet.status, "doing")

    def test_check_test_pass_and_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            self._setup_repo(tmp)
            self._write_sheet(
                tmp, "T001", "t", allow=("src/**",),
                test="python3 -c 'print(1234)'",
            )
            self.assertEqual(self._atb(tmp, "start", "T001").returncode, 0)

            result = self._atb(tmp, "check", "T001", "--test")
            self.assertEqual(result.returncode, 0, result.stdout)
            runs = self._read_runs(tmp, "T001")
            self.assertEqual(runs[-1]["exit_code"], 0)
            self.assertIn("1234", runs[-1]["output"])
            self.assertIn("timestamp", runs[-1])
            self.assertIn("duration", runs[-1])
            self.assertIn("command", runs[-1])
            self.assertIn("print(1234)", runs[-1]["command"])

            self._set_test(tmp, "T001", "python3 -c 'import sys; sys.exit(3)'")
            result = self._atb(tmp, "check", "T001", "--test")
            self.assertEqual(result.returncode, 1)
            runs = self._read_runs(tmp, "T001")
            self.assertEqual(runs[-1]["exit_code"], 3)

    def test_check_test_output_truncated_to_4kb(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            self._setup_repo(tmp)
            self._write_sheet(
                tmp, "T001", "t", allow=("src/**",),
                test="python3 -c 'print(\"A\" * 8000); print(\"TAIL_MARKER\")'",
            )
            self.assertEqual(self._atb(tmp, "start", "T001").returncode, 0)
            result = self._atb(tmp, "check", "T001", "--test")
            self.assertEqual(result.returncode, 0, result.stdout)
            runs = self._read_runs(tmp, "T001")
            out = runs[-1]["output"]
            self.assertLessEqual(len(out.encode("utf-8")), 4096)
            self.assertIn("TAIL_MARKER", out)

    def test_check_test_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            self._setup_repo(tmp, test_timeout=1)
            self._write_sheet(
                tmp, "T001", "t", allow=("src/**",),
                test="python3 -c 'import time; time.sleep(30)'",
            )
            self.assertEqual(self._atb(tmp, "start", "T001").returncode, 0)

            result = self._atb(tmp, "check", "T001", "--test")
            self.assertEqual(result.returncode, 1)
            runs = self._read_runs(tmp, "T001")
            self.assertEqual(runs[-1]["exit_code"], -1)
            self.assertIn("timeout", runs[-1]["output"])

    def test_check_uncommitted_strict(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            self._setup_repo(tmp)
            self._write_sheet(tmp, "T001", "t", allow=("src/**",))
            self.assertEqual(self._atb(tmp, "start", "T001").returncode, 0)
            wt = tmp / "worktrees" / "T001"
            (wt / "src" / "a.py").write_text("uncommitted\n", encoding="utf-8")

            result = self._atb(tmp, "check", "T001")
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn("warning", result.stderr)

            result = self._atb(tmp, "check", "T001", "--strict")
            self.assertEqual(result.returncode, 1, result.stdout)

    def test_prompt_zh_en_and_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            self._setup_repo(tmp)
            self._write_sheet(tmp, "T001", "任务", allow=("src/**",), deny=("DESIGN.md",))
            self.assertEqual(self._atb(tmp, "start", "T001").returncode, 0)
            wt = tmp / "worktrees" / "T001"

            result = self._atb(tmp, "prompt", "T001", "--lang", "zh")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(str(wt), result.stdout)
            self.assertIn("不要 push", result.stdout)
            self.assertIn("src/**", result.stdout)
            self.assertIn("DESIGN.md", result.stdout)

            result = self._atb(tmp, "prompt", "T001", "--lang", "en")
            self.assertIn("Do NOT push", result.stdout)
            self.assertIn("src/**", result.stdout)

            result = self._atb(tmp, "prompt", "T001", "--json")
            data = json.loads(result.stdout)
            self.assertEqual(set(data), {"text", "worktree", "branch"})
            self.assertIn(str(wt), data["worktree"])
            self.assertEqual(data["branch"], "opencode/t001")

    def test_prompt_rework_section_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            self._setup_repo(tmp)
            body = (
                "\n## 背景\nbg\n\n## 要做的事\ndo\n\n"
                "## 返工\nrework note\n\n## 验收\n- [ ] \n"
            )
            self._write_sheet(tmp, "T001", "任务", allow=("src/**",), body=body)
            self.assertEqual(self._atb(tmp, "start", "T001").returncode, 0)

            result = self._atb(tmp, "prompt", "T001", "--lang", "zh")
            text = result.stdout
            self.assertIn("返工", text)
            self.assertIn("rework note", text)
            self.assertLess(text.index("返工"), text.index("工作位置"))


if __name__ == "__main__":
    unittest.main()
