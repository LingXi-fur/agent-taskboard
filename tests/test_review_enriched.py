"""Tests for enriched ``atb review`` (activity, history, uncommitted alert, doctor hints)."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from agent_taskboard import sheet as sheet_mod

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


class EnrichedReviewTests(unittest.TestCase):
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
        self.assertEqual(self._atb(repo, "init").returncode, 0)
        self._git(repo, "add", ".taskboard")
        self._git(repo, "commit", "-qm", "init atb")
        return repo

    def test_enriched_review_uncommitted_rework_test_timeline_and_lang(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            res_new = self._atb(
                repo, "new", "Enriched task", "--agent", "gemini", "--allow", "src/**", "--json"
            )
            self.assertEqual(res_new.returncode, 0, res_new.stderr)
            tid = json.loads(res_new.stdout)["id"]
            sheet_file = repo / ".taskboard" / "tasks" / f"{tid}.md"
            self._git(repo, "add", str(sheet_file))
            self._git(
                repo,
                "commit",
                "-qm",
                "add sheet",
                env_extra={"GIT_AUTHOR_DATE": "2026-11-16T08:00:00Z", "GIT_COMMITTER_DATE": "2026-11-16T08:00:00Z"},
            )

            start_res = self._atb(repo, "start", tid, "--json")
            self.assertEqual(start_res.returncode, 0, start_res.stderr)
            wt = Path(json.loads(start_res.stdout)["worktree"])

            # Create 12 status transitions in git so we verify the cap of latest 10
            for idx in range(12):
                s = sheet_mod.parse(sheet_file.read_text(encoding="utf-8"))
                s.status = "rework" if idx % 2 == 0 else "doing"
                sheet_file.write_text(sheet_mod.dump(s), encoding="utf-8")
                self._git(repo, "add", str(sheet_file))
                ts = f"2026-11-16T09:{idx:02d}:00Z"
                self._git(
                    repo,
                    "commit",
                    "-qm",
                    f"flip {idx}",
                    env_extra={"GIT_AUTHOR_DATE": ts, "GIT_COMMITTER_DATE": ts},
                )

            # Two rework notes via CLI
            self.assertEqual(self._atb(repo, "rework", tid, "--note", "First rework").returncode, 0)
            self.assertEqual(self._atb(repo, "rework", tid, "--note", "Second rework").returncode, 0)

            # Committed work in worktree
            (wt / "src" / "app.py").write_text("committed change\n", encoding="utf-8")
            self._git(wt, "add", "src/app.py")
            self._git(wt, "commit", "-qm", "work")

            # Add 2 uncommitted files in worktree
            (wt / "src" / "wip1.py").write_text("wip1\n", encoding="utf-8")
            (wt / "src" / "wip2.py").write_text("wip2\n", encoding="utf-8")

            # Write a test run record
            runs_dir = repo / ".taskboard" / "runs"
            runs_dir.mkdir(parents=True, exist_ok=True)
            (runs_dir / f"{tid}.jsonl").write_text(
                json.dumps({
                    "timestamp": "2026-11-16T10:00:00+00:00",
                    "exit_code": 0,
                    "duration": 12.34,
                    "output": "OK\n",
                }) + "\n",
                encoding="utf-8",
            )

            # Chinese review
            zh_res = self._atb(repo, "review", tid, "--lang", "zh")
            self.assertEqual(zh_res.returncode, 0, zh_res.stderr)
            zh_out = zh_res.stdout
            self.assertIn("worktree 里还有 2 个未提交文件，审查的是已提交部分", zh_out)
            self.assertIn("## 活动与历史", zh_out)
            self.assertIn("- DIRTY（未提交数）: 2", zh_out)
            self.assertIn("- IDLE（无活动时长）:", zh_out)
            self.assertIn("- 返工次数: 2", zh_out)
            self.assertIn("exit 0（12.3s）", zh_out)
            self.assertIn("- doctor 异常: 无", zh_out)

            # Check timeline entries capped at <= 10
            timeline_lines = [
                line for line in zh_out.splitlines() if "  " in line and " → " in line
            ]
            self.assertLessEqual(len(timeline_lines), 10)
            self.assertEqual(len(timeline_lines), 10)

            # English review: zero Chinese characters
            en_res = self._atb(repo, "review", tid, "--lang", "en")
            self.assertEqual(en_res.returncode, 0, en_res.stderr)
            en_out = en_res.stdout
            self.assertIsNone(re.search(r"[\u4e00-\u9fff]", en_out), en_out)
            self.assertIn("2 uncommitted file(s) remain in the worktree", en_out)
            self.assertIn("## Activity and history", en_out)
            self.assertIn("- DIRTY (uncommitted count): 2", en_out)
            self.assertIn("- Rework count: 2", en_out)
            self.assertIn("exit 0 (12.3s)", en_out)
            self.assertIn("- Doctor issues: none", en_out)

    def test_enriched_review_no_uncommitted_banner_and_includes_task_doctor_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            # An unrelated done leftover task (produces WARN in doctor, must NOT appear in T002 review)
            t1 = Path(self._atb(repo, "new", "Leftover task", "--agent", "gemini").stdout.strip()).stem
            self.assertEqual(self._atb(repo, "start", t1).returncode, 0)
            s1_path = repo / ".taskboard" / "tasks" / f"{t1}.md"
            s1 = sheet_mod.parse(s1_path.read_text(encoding="utf-8"))
            s1.status = "done"
            s1_path.write_text(sheet_mod.dump(s1), encoding="utf-8")

            # T002: doing with clean committed work, then worktree deleted -> doctor FAIL for T002
            t2 = Path(self._atb(repo, "new", "Broken wt task", "--agent", "gemini", "--allow", "**").stdout.strip()).stem
            start2 = json.loads(self._atb(repo, "start", t2, "--json").stdout)
            wt2 = Path(start2["worktree"])
            (wt2 / "src" / "app.py").write_text("clean work\n", encoding="utf-8")
            self._git(wt2, "add", "-A")
            self._git(wt2, "commit", "-qm", "commit work")
            shutil.rmtree(wt2)

            res = self._atb(repo, "review", t2)
            self.assertEqual(res.returncode, 0, res.stderr)
            out = res.stdout
            self.assertNotIn("审查的是已提交部分", out)
            self.assertIn("- DIRTY（未提交数）: —", out)
            self.assertIn("- doctor 异常:", out)
            self.assertIn(f"[active] {t2}", out)
            self.assertNotIn(t1, out)


if __name__ == "__main__":
    unittest.main()
