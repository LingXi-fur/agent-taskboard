"""Tests for READY column and ``ready`` / ``blockers`` in ``status`` and ``board``."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agent_taskboard import sheet as sheet_mod

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


class BoardReadyTests(unittest.TestCase):
    def _env(self, home: Path) -> dict[str, str]:
        env = os.environ.copy()
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        env["GIT_CONFIG_SYSTEM"] = os.devnull
        env["GIT_CONFIG_GLOBAL"] = os.devnull
        env["HOME"] = str(home)
        env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
        return env

    def _git(self, cwd: Path, *args: str) -> str:
        return subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            env=self._env(cwd),
            capture_output=True,
            text=True,
            check=True,
        ).stdout

    def _atb(self, repo: Path, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "agent_taskboard", *args],
            cwd=str(repo),
            env=self._env(repo),
            capture_output=True,
            text=True,
        )

    def _setup(self, base: Path) -> Path:
        repo = base / "proj"
        repo.mkdir()
        self._git(repo, "init", "-q", "-b", "main")
        self._git(repo, "config", "user.name", "t")
        self._git(repo, "config", "user.email", "t@t")
        (repo / "src").mkdir()
        (repo / "src" / "a.py").write_text("base-a\n", encoding="utf-8")
        (repo / "src" / "b.py").write_text("base-b\n", encoding="utf-8")
        (repo / "src" / "c.py").write_text("base-c\n", encoding="utf-8")
        (repo / "src" / "d.py").write_text("base-d\n", encoding="utf-8")
        self._git(repo, "add", "-A")
        self._git(repo, "commit", "-qm", "init")
        self.assertEqual(self._atb(repo, "init").returncode, 0)
        self._git(repo, "add", ".taskboard")
        self._git(repo, "commit", "-qm", "init atb")
        return repo

    def _new_and_start(self, repo: Path, title: str, *, allow="src/**") -> tuple[str, Path]:
        tid = json.loads(
            self._atb(repo, "new", title, "--agent", "gemini", "--allow", allow, "--json").stdout
        )["id"]
        wt = Path(json.loads(self._atb(repo, "start", tid, "--json").stdout)["worktree"])
        return tid, wt

    def _write_test_run(self, repo: Path, tid: str, exit_code: int) -> None:
        runs = repo / ".taskboard" / "runs"
        runs.mkdir(parents=True, exist_ok=True)
        (runs / f"{tid}.jsonl").write_text(
            json.dumps({
                "timestamp": "2026-11-18T10:00:00+00:00",
                "exit_code": exit_code,
                "duration": 1.0,
                "output": "ok\n",
            }) + "\n",
            encoding="utf-8",
        )

    def _mark_review(self, repo: Path, tid: str) -> None:
        path = repo / ".taskboard" / "tasks" / f"{tid}.md"
        s = sheet_mod.parse(path.read_text(encoding="utf-8"))
        s.status = "review"
        path.write_text(sheet_mod.dump(s), encoding="utf-8")

    def test_status_and_board_ready_across_four_scenarios_and_hover_lang(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)

            # T001: clean, test passed, mergeable -> ready=True (✓)
            t1, wt1 = self._new_and_start(repo, "Ready task")
            (wt1 / "src" / "a.py").write_text("clean-a\n", encoding="utf-8")
            self._git(wt1, "add", "-A")
            self._git(wt1, "commit", "-qm", "work 1")
            self._write_test_run(repo, t1, 0)
            self._mark_review(repo, t1)

            # T002: failed test -> ✗ test_not_passed
            t2, wt2 = self._new_and_start(repo, "Failed test task")
            (wt2 / "src" / "b.py").write_text("work-b\n", encoding="utf-8")
            self._git(wt2, "add", "-A")
            self._git(wt2, "commit", "-qm", "work 2")
            self._write_test_run(repo, t2, 1)
            self._mark_review(repo, t2)

            # T003: uncommitted changes (and test also failed so hover lists multiple!)
            t3, wt3 = self._new_and_start(repo, "Dirty task")
            (wt3 / "src" / "c.py").write_text("work-c\n", encoding="utf-8")
            self._git(wt3, "add", "-A")
            self._git(wt3, "commit", "-qm", "work 3")
            (wt3 / "src" / "wip.py").write_text("wip\n", encoding="utf-8")
            self._write_test_run(repo, t3, 0)
            self._mark_review(repo, t3)

            # T004: conflicts with main (target) -> ✗ merge_conflict
            t4, wt4 = self._new_and_start(repo, "Target conflict task")
            (wt4 / "src" / "d.py").write_text("branch-d\n", encoding="utf-8")
            self._git(wt4, "add", "-A")
            self._git(wt4, "commit", "-qm", "work 4")
            self._write_test_run(repo, t4, 0)
            self._mark_review(repo, t4)

            # Advance main on d.py so T004 conflicts with target
            (repo / "src" / "d.py").write_text("main-d\n", encoding="utf-8")
            self._git(repo, "add", "src/d.py")
            self._git(repo, "commit", "-qm", "main diverges on d.py")

            # T005: doing with commit -> NOT evaluated (shows "—", ready=None); T006: todo -> ready=None ("—")
            t5, wt5 = self._new_and_start(repo, "Doing with commit")
            (wt5 / "src" / "extra.py").write_text("e\n", encoding="utf-8")
            self._git(wt5, "add", "-A")
            self._git(wt5, "commit", "-qm", "work 5")
            self._write_test_run(repo, t5, 0)

            t6 = json.loads(
                self._atb(repo, "new", "Todo task", "--agent", "gemini", "--json").stdout
            )["id"]

            # 1) Check status --json
            st_json = self._atb(repo, "status", "--json")
            self.assertEqual(st_json.returncode, 0, st_json.stderr)
            payload = json.loads(st_json.stdout)
            self.assertEqual(set(payload), {"tasks", "summary"})
            by_id = {r["id"]: r for r in payload["tasks"]}

            self.assertIs(by_id[t1]["ready"], True)
            self.assertEqual(by_id[t1]["blockers"], [])

            self.assertIs(by_id[t2]["ready"], False)
            self.assertEqual([b["code"] for b in by_id[t2]["blockers"]], ["test_not_passed"])

            self.assertIs(by_id[t3]["ready"], False)
            self.assertEqual([b["code"] for b in by_id[t3]["blockers"]], ["uncommitted"])

            self.assertIs(by_id[t4]["ready"], False)
            self.assertEqual([b["code"] for b in by_id[t4]["blockers"]], ["merge_conflict"])

            self.assertIsNone(by_id[t5]["ready"])
            self.assertEqual(by_id[t5]["blockers"], [])
            self.assertIsNone(by_id[t6]["ready"])
            self.assertEqual(by_id[t6]["blockers"], [])

            # 2) Check status human table includes READY column and symbols
            st_txt = self._atb(repo, "status")
            self.assertEqual(st_txt.returncode, 0, st_txt.stderr)
            self.assertIn("READY", st_txt.stdout)
            lines = {line.split()[0]: line for line in st_txt.stdout.splitlines() if line.startswith("T")}
            self.assertIn("✓", lines[t1])
            self.assertIn("✗ test_not_passed", lines[t2])
            self.assertIn("✗ uncommitted", lines[t3])
            self.assertIn("✗ merge_conflict", lines[t4])
            self.assertTrue(lines[t5].rstrip().endswith("—"))
            self.assertTrue(lines[t6].rstrip().endswith("—"))

            # 3) Make T002 ALSO dirty so hover shows multiple blockers in zh and en
            (wt2 / "src" / "extra2.py").write_text("dirty2\n", encoding="utf-8")

            zh_out = base / "board-zh.html"
            self.assertEqual(
                self._atb(repo, "board", "--html", str(zh_out), "--lang", "zh").returncode, 0
            )
            zh_html = zh_out.read_text(encoding="utf-8")
            self.assertIn("<th>可合并</th>", zh_html)
            self.assertIn('<td class="ready-ok">✓</td>', zh_html)
            self.assertIn("✗ uncommitted", zh_html)
            self.assertIn("✗ merge_conflict", zh_html)
            # Hover title on T002 lists both uncommitted and test_not_passed in Chinese
            t2_row_zh = re.search(rf"<tr[^>]*><td>{t2}</td>.*?</tr>", zh_html, re.S).group(0)
            self.assertIn("title=", t2_row_zh)
            self.assertIn("存在未提交改动", t2_row_zh)
            self.assertIn("最近一次测试未通过或无记录", t2_row_zh)

            en_out = base / "board-en.html"
            self.assertEqual(
                self._atb(repo, "board", "--html", str(en_out), "--lang", "en").returncode, 0
            )
            en_html = en_out.read_text(encoding="utf-8")
            self.assertIn("<th>Ready</th>", en_html)
            self.assertIsNone(re.search(r"[\u4e00-\u9fff]", en_html), en_html)
            t2_row_en = re.search(rf"<tr[^>]*><td>{t2}</td>.*?</tr>", en_html, re.S).group(0)
            self.assertIn("uncommitted changes:", t2_row_en)
            self.assertIn("last test run did not pass or has no record", t2_row_en)

    def test_cross_task_conflict_consistent_across_status_board_and_review_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)

            t1, wt1 = self._new_and_start(repo, "Conflict A")
            (wt1 / "src" / "a.py").write_text("edit-by-t1\n", encoding="utf-8")
            self._git(wt1, "add", "-A")
            self._git(wt1, "commit", "-qm", "t1 edit")
            self._write_test_run(repo, t1, 0)
            self._mark_review(repo, t1)

            t2, wt2 = self._new_and_start(repo, "Conflict B")
            (wt2 / "src" / "a.py").write_text("edit-by-t2\n", encoding="utf-8")
            self._git(wt2, "add", "-A")
            self._git(wt2, "commit", "-qm", "t2 edit")
            self._write_test_run(repo, t2, 0)
            self._mark_review(repo, t2)

            # 1) status --json and status table
            st_json = json.loads(self._atb(repo, "status", "--json").stdout)
            by_id = {r["id"]: r for r in st_json["tasks"]}
            st_txt = self._atb(repo, "status").stdout
            lines = {line.split()[0]: line for line in st_txt.splitlines() if line.startswith("T")}

            # 2) board --html
            board_out = base / "board.html"
            self.assertEqual(
                self._atb(repo, "board", "--html", str(board_out), "--lang", "en").returncode, 0
            )
            board_html = board_out.read_text(encoding="utf-8")

            # 3) review --json for each task
            for tid in (t1, t2):
                rev = json.loads(self._atb(repo, "review", tid, "--json", "--lang", "en").stdout)
                self.assertIs(by_id[tid]["ready"], False)
                self.assertIs(rev["ready"], False)
                self.assertEqual([b["code"] for b in by_id[tid]["blockers"]], ["merge_conflict"])
                self.assertEqual([b["code"] for b in rev["blockers"]], ["merge_conflict"])
                self.assertIn("✗ merge_conflict", lines[tid])
                row_html = re.search(rf"<tr[^>]*><td>{tid}</td>.*?</tr>", board_html, re.S).group(0)
                self.assertIn("✗ merge_conflict", row_html)

    def test_review_missing_branch_reports_missing_branch_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            t1, wt1 = self._new_and_start(repo, "Missing branch task")
            self._write_test_run(repo, t1, 0)
            self._mark_review(repo, t1)
            branch = sheet_mod.parse(
                (repo / ".taskboard" / "tasks" / f"{t1}.md").read_text(encoding="utf-8")
            ).branch
            self._git(repo, "worktree", "remove", str(wt1))
            self._git(repo, "branch", "-D", branch)

            st_json = json.loads(self._atb(repo, "status", "--json").stdout)
            row = st_json["tasks"][0]
            self.assertIs(row["ready"], False)
            self.assertEqual([b["code"] for b in row["blockers"]], ["missing_branch"])

            st_txt = self._atb(repo, "status").stdout
            self.assertIn("✗ missing_branch", st_txt)

            board_out = base / "board.html"
            self.assertEqual(self._atb(repo, "board", "--html", str(board_out)).returncode, 0)
            self.assertIn("✗ missing_branch", board_out.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
