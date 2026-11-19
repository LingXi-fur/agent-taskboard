"""Tests for ``atb board --html`` activity columns (Dirty/Idle) and stall highlight."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from agent_taskboard import sheet as sheet_mod

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


class BoardActivityTests(unittest.TestCase):
    def _env(self, home: Path, extra: dict[str, str] | None = None) -> dict[str, str]:
        env = os.environ.copy()
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        env["GIT_CONFIG_SYSTEM"] = os.devnull
        env["GIT_CONFIG_GLOBAL"] = os.devnull
        env["HOME"] = str(home)
        env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
        if extra:
            env.update(extra)
        return env

    def _git(
        self, cwd: Path, *args: str, env_extra: dict[str, str] | None = None
    ) -> None:
        subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            env=self._env(cwd, env_extra),
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            check=True,
        )

    def _atb(self, repo: Path, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "agent_taskboard", *args],
            cwd=str(repo),
            env=self._env(repo),
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
        )

    def _setup(self, base: Path) -> Path:
        repo = base / "proj"
        repo.mkdir()
        self._git(repo, "init", "-q", "-b", "main")
        self._git(repo, "config", "user.name", "t")
        self._git(repo, "config", "user.email", "t@t")
        (repo / "src").mkdir()
        (repo / "src" / "app.py").write_text("base\n", encoding="utf-8")
        self._git(repo, "add", "-A")
        self._git(repo, "commit", "-qm", "init")
        res = self._atb(repo, "init")
        self.assertEqual(res.returncode, 0, res.stderr)
        self._git(repo, "add", ".taskboard")
        self._git(repo, "commit", "-qm", "init taskboard")
        return repo

    def _new_task(self, repo: Path, title: str) -> str:
        res = self._atb(repo, "new", title, "--agent", "gemini", "--allow", "**")
        self.assertEqual(res.returncode, 0, res.stderr)
        return Path(res.stdout.strip()).stem

    def _start_task(self, repo: Path, tid: str) -> Path:
        res = self._atb(repo, "start", tid, "--json")
        self.assertEqual(res.returncode, 0, res.stderr)
        return Path(json.loads(res.stdout)["worktree"])

    def _set_status(self, repo: Path, tid: str, status: str) -> None:
        path = repo / ".taskboard" / "tasks" / f"{tid}.md"
        s = sheet_mod.parse(path.read_text(encoding="utf-8"))
        s.status = status
        path.write_text(sheet_mod.dump(s), encoding="utf-8")

    def _row_for(self, html_text: str, tid: str) -> str:
        match = re.search(rf"<tr[^>]*><td>{re.escape(tid)}</td>.*?</tr>", html_text, re.DOTALL)
        self.assertIsNotNone(match, f"row for {tid} not found in board html")
        return match.group(0)

    def test_board_activity_columns_stall_highlight_and_escaping(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            now = int(time.time())
            old_ts = now - 3 * 3600  # 3 hours ago -> 3h (>= 30m stall)
            old_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(old_ts))

            # T001: doing, idle 3h, dirty=2, title with HTML special chars -> stalled
            t1 = self._new_task(repo, "Stalled <script>&alert")
            wt1 = self._start_task(repo, t1)
            (wt1 / "src" / "a.py").write_text("committed\n", encoding="utf-8")
            self._git(wt1, "add", "-A")
            self._git(
                wt1,
                "commit",
                "-qm",
                "old commit",
                env_extra={
                    "GIT_AUTHOR_DATE": old_iso,
                    "GIT_COMMITTER_DATE": old_iso,
                },
            )
            for name in ("u1.py", "u2.py"):
                f = wt1 / "src" / name
                f.write_text("dirty\n", encoding="utf-8")
                os.utime(f, (old_ts, old_ts))
            # the worktree itself was also created 3h ago
            os.utime(wt1 / ".git", (old_ts, old_ts))

            # T002: doing, fresh activity (0s) -> NOT stalled
            t2 = self._new_task(repo, "Active task")
            wt2 = self._start_task(repo, t2)
            (wt2 / "src" / "fresh.py").write_text("fresh\n", encoding="utf-8")

            # T003: review, idle 3h -> NOT stalled (only doing/rework stall)
            t3 = self._new_task(repo, "Review old")
            wt3 = self._start_task(repo, t3)
            (wt3 / "src" / "r.py").write_text("rev\n", encoding="utf-8")
            self._git(wt3, "add", "-A")
            self._git(
                wt3,
                "commit",
                "-qm",
                "old review",
                env_extra={
                    "GIT_AUTHOR_DATE": old_iso,
                    "GIT_COMMITTER_DATE": old_iso,
                },
            )
            os.utime(wt3 / ".git", (old_ts, old_ts))
            self._set_status(repo, t3, "review")

            # T004: todo (no worktree) -> Dirty/Idle show "—", NOT stalled
            t4 = self._new_task(repo, "Todo task")

            # T005: done (no active worktree check) -> Dirty/Idle show "—", NOT stalled
            t5 = self._new_task(repo, "Done task")
            self._set_status(repo, t5, "done")

            out_html = base / "board.html"
            res = self._atb(repo, "board", "--html", str(out_html), "--stall", "30")
            self.assertEqual(res.returncode, 0, res.stderr)
            html_text = out_html.read_text(encoding="utf-8")

            # Headers include Dirty and Idle (zh by default)
            self.assertIn("<th>未提交</th>", html_text)
            self.assertIn("<th>空闲</th>", html_text)

            # Top summary shows generated timestamp, threshold, and stall count = 1
            self.assertRegex(html_text, r"生成于 \d{4}-\d{2}-\d{2} \d{2}:\d{2}（阈值 30 分钟）")
            self.assertIn("可能卡住：1", html_text)

            # HTML escaping for title
            self.assertNotIn("<script>&alert", html_text)
            self.assertIn("Stalled &lt;script&gt;&amp;alert", html_text)

            # T001 stalled: class="stall-row", text "可能卡住", dirty=2, idle=3h
            row1 = self._row_for(html_text, t1)
            self.assertIn('class="stall-row"', row1)
            self.assertIn("可能卡住", row1)
            self.assertIn("<td>2</td>", row1)
            self.assertIn("3h", row1)

            # T002 active: not stalled, dirty=1
            row2 = self._row_for(html_text, t2)
            self.assertNotIn("stall-row", row2)
            self.assertNotIn("可能卡住", row2)
            self.assertIn("<td>1</td>", row2)

            # T003 review: not stalled even with 3h idle
            row3 = self._row_for(html_text, t3)
            self.assertNotIn("stall-row", row3)
            self.assertNotIn("可能卡住", row3)
            self.assertIn("<td>3h</td>", row3)

            # T004 todo and T005 done: "—" for Dirty and Idle, not stalled
            for tid in (t4, t5):
                row = self._row_for(html_text, tid)
                self.assertNotIn("stall-row", row)
                self.assertNotIn("可能卡住", row)
                self.assertGreaterEqual(row.count("<td>—</td>"), 2)

    def test_board_invalid_stall_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            res = self._atb(repo, "board", "--html", str(base / "b.html"), "--stall", "0")
            self.assertEqual(res.returncode, 2)
            self.assertIn("--stall", res.stderr)


if __name__ == "__main__":
    unittest.main()
