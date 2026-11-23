"""End-to-end tests for ``atb status``, ``conflicts``, ``review`` and ``rework``."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agent_taskboard import sheet as sheet_mod

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

# A 10-line file so "same line" and "far-apart line" edits behave differently.
BASE_CONTENT = "\n".join(f"l{i}" for i in range(1, 11)) + "\n"


class BoardReviewTests(unittest.TestCase):
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

    def _setup_repo(self, tmp: Path) -> None:
        self._git(tmp, "init", "-q", "-b", "main")
        self._git(tmp, "config", "user.name", "t")
        self._git(tmp, "config", "user.email", "t@t")
        (tmp / "f.txt").write_text(BASE_CONTENT, encoding="utf-8")
        self._git(tmp, "add", "-A")
        self._git(tmp, "commit", "-qm", "base")
        (tmp / ".taskboard").mkdir()
        (tmp / ".taskboard" / "tasks").mkdir()
        (tmp / ".taskboard" / "config.toml").write_text(
            'worktree_root = "worktrees"\n', encoding="utf-8"
        )

    def _write_sheet(self, tmp: Path, tid: str, title: str, *, allow=("f.txt",), deny=()):
        body = "\n## 背景\n\n## 要做的事\n\n## 验收\n\n- [ ] item one\n- [ ] item two\n"
        sheet = sheet_mod.Sheet(
            id=tid, title=title, agent="opencode", status="todo",
            allow=list(allow), deny=list(deny), body=body,
        )
        (tmp / ".taskboard" / "tasks" / f"{tid}.md").write_text(
            sheet_mod.dump(sheet), encoding="utf-8"
        )

    def _edit_line(self, path: Path, old: str, new: str) -> None:
        content = path.read_text(encoding="utf-8")
        path.write_text(content.replace(old + "\n", new + "\n"), encoding="utf-8")

    def _write_run(self, tmp: Path, tid: str, exit_code: int) -> None:
        runs_dir = tmp / ".taskboard" / "runs"
        runs_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "timestamp": "2026-11-11T00:00:00+00:00",
            "exit_code": exit_code,
            "duration": 0.1,
            "command": "python3 -c 'pass'",
            "output": "",
        }
        with (runs_dir / f"{tid}.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _read_sheet(self, tmp: Path, tid: str) -> sheet_mod.Sheet:
        return sheet_mod.parse(
            (tmp / ".taskboard" / "tasks" / f"{tid}.md").read_text(encoding="utf-8")
        )

    # ------------------------------------------------------------------ #

    def test_conflicts_real_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            self._setup_repo(tmp)
            self._write_sheet(tmp, "T001", "任务一")
            self._write_sheet(tmp, "T002", "任务二")
            self.assertEqual(self._atb(tmp, "start", "T001").returncode, 0)
            self.assertEqual(self._atb(tmp, "start", "T002").returncode, 0)

            self._edit_line(tmp / "worktrees" / "T001" / "f.txt", "l5", "T001-l5")
            self._git(tmp / "worktrees" / "T001", "add", "-A")
            self._git(tmp / "worktrees" / "T001", "commit", "-qm", "T001 edit")

            self._edit_line(tmp / "worktrees" / "T002" / "f.txt", "l5", "T002-l5")
            self._git(tmp / "worktrees" / "T002", "add", "-A")
            self._git(tmp / "worktrees" / "T002", "commit", "-qm", "T002 edit")

            result = self._atb(tmp, "conflicts")
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertIn("真实冲突", result.stdout)
            self.assertIn("f.txt", result.stdout)
            self.assertNotIn("Auto-merging", result.stdout)
            self.assertNotIn("CONFLICT", result.stdout)

            result = self._atb(tmp, "conflicts", "--json")
            data = json.loads(result.stdout)
            self.assertEqual(len(data["conflicts"]), 1)
            self.assertEqual(data["conflicts"][0]["files"], ["f.txt"])

    def test_conflicts_lists_all_conflicting_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            self._setup_repo(tmp)
            (tmp / "g.txt").write_text("g1\ng2\ng3\n", encoding="utf-8")
            self._git(tmp, "add", "-A")
            self._git(tmp, "commit", "-qm", "add g")
            self._write_sheet(tmp, "T001", "任务一", allow=("f.txt", "g.txt"))
            self._write_sheet(tmp, "T002", "任务二", allow=("f.txt", "g.txt"))
            self.assertEqual(self._atb(tmp, "start", "T001").returncode, 0)
            self.assertEqual(self._atb(tmp, "start", "T002").returncode, 0)

            for tag in ("T001", "T002"):
                wt = tmp / "worktrees" / tag
                self._edit_line(wt / "f.txt", "l5", f"{tag}-l5")
                self._edit_line(wt / "g.txt", "g2", f"{tag}-g2")
                self._git(wt, "add", "-A")
                self._git(wt, "commit", "-qm", f"{tag} edit")

            result = self._atb(tmp, "conflicts", "--json")
            self.assertEqual(result.returncode, 1, result.stdout)
            data = json.loads(result.stdout)
            self.assertEqual(len(data["conflicts"]), 1)
            self.assertEqual(data["conflicts"][0]["files"], ["f.txt", "g.txt"])

    def test_conflicts_invalid_branch_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            self._setup_repo(tmp)
            self._write_sheet(tmp, "T001", "任务一")
            self.assertEqual(self._atb(tmp, "start", "T001").returncode, 0)
            # break the branch so the task state cannot be determined
            self._git(tmp, "worktree", "remove", str(tmp / "worktrees" / "T001"))
            self._git(tmp, "branch", "-D", "opencode/t001")

            result = self._atb(tmp, "conflicts", "--json")
            self.assertEqual(result.returncode, 2, result.stdout)
            data = json.loads(result.stdout)
            self.assertTrue(data["errors"])
            self.assertIn("T001", data["errors"][0]["tasks"])

            result = self._atb(tmp, "conflicts")
            self.assertEqual(result.returncode, 2)
            self.assertIn("无法判断", result.stdout)

    def test_conflicts_mergeable(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            self._setup_repo(tmp)
            self._write_sheet(tmp, "T001", "任务一")
            self._write_sheet(tmp, "T002", "任务二")
            self.assertEqual(self._atb(tmp, "start", "T001").returncode, 0)
            self.assertEqual(self._atb(tmp, "start", "T002").returncode, 0)

            self._edit_line(tmp / "worktrees" / "T001" / "f.txt", "l1", "T001-l1")
            self._git(tmp / "worktrees" / "T001", "add", "-A")
            self._git(tmp / "worktrees" / "T001", "commit", "-qm", "T001 edit")

            self._edit_line(tmp / "worktrees" / "T002" / "f.txt", "l9", "T002-l9")
            self._git(tmp / "worktrees" / "T002", "add", "-A")
            self._git(tmp / "worktrees" / "T002", "commit", "-qm", "T002 edit")

            result = self._atb(tmp, "conflicts", "--json")
            self.assertEqual(result.returncode, 0, result.stdout)
            data = json.loads(result.stdout)
            self.assertEqual(len(data["conflicts"]), 0)
            self.assertEqual(len(data["mergeable"]), 1)
            self.assertIn("f.txt", data["mergeable"][0]["files"])

    def test_conflicts_baseline_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            self._setup_repo(tmp)
            self._write_sheet(tmp, "T001", "任务一")
            self.assertEqual(self._atb(tmp, "start", "T001").returncode, 0)

            # task touches f.txt on its branch
            self._edit_line(tmp / "worktrees" / "T001" / "f.txt", "l5", "T001-l5")
            self._git(tmp / "worktrees" / "T001", "add", "-A")
            self._git(tmp / "worktrees" / "T001", "commit", "-qm", "T001 edit")

            # target advances and also touches f.txt after the task's baseline
            self._edit_line(tmp / "f.txt", "l5", "main-l5")
            self._git(tmp, "add", "-A")
            self._git(tmp, "commit", "-qm", "main edit")

            result = self._atb(tmp, "conflicts", "--json")
            self.assertEqual(result.returncode, 0, result.stdout)
            data = json.loads(result.stdout)
            self.assertEqual(len(data["drift"]), 1)
            self.assertEqual(data["drift"][0]["task"], "T001")
            self.assertIn("f.txt", data["drift"][0]["files"])

    def test_status_missing_and_test_marks(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            self._setup_repo(tmp)
            self._write_sheet(tmp, "T001", "待办任务")          # todo -> dashes
            self._write_sheet(tmp, "T002", "通过任务")          # doing, test ok
            self._write_sheet(tmp, "T003", "失败任务")          # doing, test fail
            self._write_sheet(tmp, "T004", "删除分支任务")      # doing, branch deleted

            self.assertEqual(self._atb(tmp, "start", "T002").returncode, 0)
            self.assertEqual(self._atb(tmp, "start", "T003").returncode, 0)
            self.assertEqual(self._atb(tmp, "start", "T004").returncode, 0)

            self._write_run(tmp, "T002", 0)
            self._write_run(tmp, "T003", 3)

            # delete T004's worktree and branch
            self._git(tmp, "worktree", "remove", str(tmp / "worktrees" / "T004"))
            self._git(tmp, "branch", "-D", "opencode/t004")

            result = self._atb(tmp, "status")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("✓", result.stdout)
            self.assertIn("✗", result.stdout)
            self.assertIn("missing", result.stdout)
            self.assertIn("T001", result.stdout)

    def test_status_json_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            self._setup_repo(tmp)
            self._write_sheet(tmp, "T001", "待办任务")
            self.assertEqual(self._atb(tmp, "start", "T001").returncode, 0)

            result = self._atb(tmp, "status", "--json")
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(result.stdout)
            self.assertEqual(set(data), {"tasks", "summary"})
            self.assertEqual(
                set(data["tasks"][0]),
                {"id", "status", "agent", "title", "branch", "missing",
                 "ahead", "behind", "changed_files", "violations", "test",
                 "dirty", "idle_seconds", "ready", "blockers", "warnings"},
            )
            self.assertIn("doing", data["summary"])

    def test_review_markdown_and_status_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            self._setup_repo(tmp)
            self._write_sheet(tmp, "T001", "审查任务")
            self.assertEqual(self._atb(tmp, "start", "T001").returncode, 0)

            result = self._atb(tmp, "review", "T001")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("## 验收清单", result.stdout)
            self.assertIn("- [ ] item one", result.stdout)
            self.assertIn("## 审查意见", result.stdout)
            self.assertIn("## diffstat", result.stdout)
            # the section heading from the body must not be repeated verbatim
            self.assertNotIn("## 验收", result.stdout.split("\n"))

            sheet = self._read_sheet(tmp, "T001")
            self.assertEqual(sheet.status, "review")

            # running review again keeps the status
            self._atb(tmp, "review", "T001")
            self.assertEqual(self._read_sheet(tmp, "T001").status, "review")

    def test_review_out_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            self._setup_repo(tmp)
            self._write_sheet(tmp, "T001", "审查任务")
            self.assertEqual(self._atb(tmp, "start", "T001").returncode, 0)

            out = tmp / "review.md"
            result = self._atb(tmp, "review", "T001", "--out", str(out))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(out.exists())
            self.assertIn("## 验收清单", out.read_text(encoding="utf-8"))

    def test_review_failure_keeps_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            self._setup_repo(tmp)
            self._write_sheet(tmp, "T001", "审查任务")
            self.assertEqual(self._atb(tmp, "start", "T001").returncode, 0)

            # break the branch so review generation fails after start
            self._git(tmp, "worktree", "remove", str(tmp / "worktrees" / "T001"))
            self._git(tmp, "branch", "-D", "opencode/t001")

            result = self._atb(tmp, "review", "T001")
            self.assertEqual(result.returncode, 2, result.stdout)
            self.assertEqual(self._read_sheet(tmp, "T001").status, "doing")

    def test_rework_twice_and_prompt_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            self._setup_repo(tmp)
            self._write_sheet(tmp, "T001", "返工任务")
            self.assertEqual(self._atb(tmp, "start", "T001").returncode, 0)

            self.assertEqual(self._atb(tmp, "rework", "T001", "--note", "第一次意见").returncode, 0)
            self.assertEqual(self._atb(tmp, "rework", "T001", "--note", "第二次意见").returncode, 0)

            sheet = self._read_sheet(tmp, "T001")
            self.assertEqual(sheet.status, "rework")
            self.assertIn("第一次意见", sheet.body)
            self.assertIn("第二次意见", sheet.body)
            self.assertEqual(sheet.body.count("## 返工"), 1)

            result = self._atb(tmp, "prompt", "T001", "--lang", "zh")
            text = result.stdout
            self.assertIn("第一次意见", text)
            self.assertIn("第二次意见", text)
            self.assertLess(text.index("返工"), text.index("工作位置"))

    def test_rework_note_from_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            self._setup_repo(tmp)
            self._write_sheet(tmp, "T001", "返工任务")
            self.assertEqual(self._atb(tmp, "start", "T001").returncode, 0)

            note_file = tmp / "note.txt"
            note_file.write_text("多行意见第一行\n多行意见第二行\n", encoding="utf-8")
            self.assertEqual(
                self._atb(tmp, "rework", "T001", "--note", "@" + str(note_file)).returncode, 0
            )

            sheet = self._read_sheet(tmp, "T001")
            self.assertIn("多行意见第一行", sheet.body)
            self.assertIn("多行意见第二行", sheet.body)

    def test_rework_rejects_done(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            self._setup_repo(tmp)
            self._write_sheet(tmp, "T001", "已完成")
            path = tmp / ".taskboard" / "tasks" / "T001.md"
            sheet = sheet_mod.parse(path.read_text(encoding="utf-8"))
            sheet.status = "done"
            path.write_text(sheet_mod.dump(sheet), encoding="utf-8")

            result = self._atb(tmp, "rework", "T001", "--note", "x")
            self.assertEqual(result.returncode, 2, result.stdout)
            after = sheet_mod.parse(path.read_text(encoding="utf-8"))
            self.assertEqual(after.status, "done")
            self.assertNotIn("## 返工", after.body)


if __name__ == "__main__":
    unittest.main()
