"""Tests for ``atb log`` (per-task chronological history)."""

from __future__ import annotations

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


class LogTests(unittest.TestCase):
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
        (repo / "README.md").write_text("hi\n", encoding="utf-8")
        self._git(repo, "add", "-A")
        self._git(repo, "commit", "-qm", "init")
        self.assertEqual(self._atb(repo, "init").returncode, 0)
        self._git(repo, "add", ".taskboard")
        self._git(repo, "commit", "-qm", "init atb")
        return repo

    def _commit_sheet(
        self, repo: Path, tid: str, msg: str, ts_iso: str
    ) -> None:
        self._git(repo, "add", f".taskboard/tasks/{tid}.md")
        self._git(
            repo,
            "commit",
            "-qm",
            msg,
            env_extra={"GIT_AUTHOR_DATE": ts_iso, "GIT_COMMITTER_DATE": ts_iso},
        )

    def test_log_full_lifecycle_corrupt_history_and_deleted_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)

            # 1. Create T001 (todo) and commit
            new_res = self._atb(repo, "new", "Build widget", "--agent", "gemini", "--json")
            self.assertEqual(new_res.returncode, 0, new_res.stderr)
            tid = json.loads(new_res.stdout)["id"]
            sheet_file = repo / ".taskboard" / "tasks" / f"{tid}.md"
            self._commit_sheet(repo, tid, "add T001", "2026-11-16T08:00:00Z")

            # 2. Start T001 -> doing, commit
            start_res = self._atb(repo, "start", tid, "--json")
            self.assertEqual(start_res.returncode, 0, start_res.stderr)
            start_data = json.loads(start_res.stdout)
            wt = Path(start_data["worktree"])
            branch = start_data["branch"]
            self._commit_sheet(repo, tid, "start T001", "2026-11-16T08:05:00Z")

            # Make a work commit in worktree
            (wt / "work.txt").write_text("done\n", encoding="utf-8")
            self._git(wt, "add", "work.txt")
            self._git(wt, "commit", "-qm", "work done")

            # 3. Corrupted historical version in git -> should skip and count
            orig_text = sheet_file.read_text(encoding="utf-8")
            sheet_file.write_text("+++\nbroken = [\n+++\n", encoding="utf-8")
            self._commit_sheet(repo, tid, "accidentally corrupt sheet", "2026-11-16T08:10:00Z")
            sheet_file.write_text(orig_text, encoding="utf-8")
            self._commit_sheet(repo, tid, "restore sheet", "2026-11-16T08:12:00Z")

            # 4. Status -> review, then rework note -> rework, then done
            s = sheet_mod.parse(sheet_file.read_text(encoding="utf-8"))
            s.status = "review"
            sheet_file.write_text(sheet_mod.dump(s), encoding="utf-8")
            self._commit_sheet(repo, tid, "T001 review", "2026-11-16T08:20:00Z")

            rew = self._atb(repo, "rework", tid, "--note", "Fix edge case\nSecond line details")
            self.assertEqual(rew.returncode, 0, rew.stderr)
            self._commit_sheet(repo, tid, "T001 rework", "2026-11-16T08:30:00Z")

            s = sheet_mod.parse(sheet_file.read_text(encoding="utf-8"))
            s.status = "done"
            sheet_file.write_text(sheet_mod.dump(s), encoding="utf-8")
            self._commit_sheet(repo, tid, "T001 done", "2026-11-16T09:00:00Z")

            # 5. Merge branch into main (--no-ff) and then delete worktree & branch
            self._git(
                repo,
                "merge",
                "--no-ff",
                branch,
                "-m",
                f"Merge {branch} ({tid}: Build widget)",
                env_extra={
                    "GIT_AUTHOR_DATE": "2026-11-16T09:05:00Z",
                    "GIT_COMMITTER_DATE": "2026-11-16T09:05:00Z",
                },
            )
            self._git(repo, "worktree", "remove", str(wt))
            self._git(repo, "branch", "-d", branch)

            # 6. Write two test run records into .taskboard/runs/T001.jsonl
            runs_dir = repo / ".taskboard" / "runs"
            runs_dir.mkdir(parents=True, exist_ok=True)
            lines_8 = "\n".join(f"line-{i}" for i in range(1, 9))
            run_records = [
                {
                    "timestamp": "2026-11-16T08:25:00+00:00",
                    "exit_code": 1,
                    "duration": 1.25,
                    "output": lines_8,
                },
                {
                    "timestamp": "2026-11-16T08:50:00+00:00",
                    "exit_code": 0,
                    "duration": 2.5,
                    "output": "all ok\nOK\n",
                },
            ]
            (runs_dir / f"{tid}.jsonl").write_text(
                "\n".join(json.dumps(r) for r in run_records) + "\n",
                encoding="utf-8",
            )

            # Default output: hides test output
            res = self._atb(repo, "log", tid)
            self.assertEqual(res.returncode, 0, res.stderr)
            out = res.stdout
            self.assertIn("todo → doing", out)
            self.assertIn("doing → review", out)
            self.assertIn("review → rework", out)
            self.assertIn("rework → done", out)
            self.assertIn("测试 exit 1（1.2s）", out)
            self.assertIn("测试 exit 0（2.5s）", out)
            self.assertIn("已合并（", out)
            self.assertIn("返工：Fix edge case", out)
            self.assertIn("返工记录：1 条", out)
            self.assertIn("跳过无法解析的历史版本：1 个", out)
            self.assertNotIn("line-4", out)
            self.assertNotIn("line-8", out)

            # Verbose output: shows only last 5 lines of output (line-4..line-8, not line-3)
            res_v = self._atb(repo, "log", tid, "--verbose")
            self.assertEqual(res_v.returncode, 0, res_v.stderr)
            self.assertNotIn("line-3", res_v.stdout)
            for idx in range(4, 9):
                self.assertIn(f"line-{idx}", res_v.stdout)
            self.assertIn("all ok", res_v.stdout)

            # JSON output
            res_j = self._atb(repo, "log", tid, "--json", "--verbose")
            self.assertEqual(res_j.returncode, 0, res_j.stderr)
            payload = json.loads(res_j.stdout)
            self.assertEqual(payload["id"], tid)
            self.assertEqual(payload["rework_count"], 1)
            self.assertEqual(payload["parse_errors"], 1)
            kinds = [e["kind"] for e in payload["events"]]
            self.assertIn("status", kinds)
            self.assertIn("test", kinds)
            self.assertIn("rework", kinds)
            self.assertIn("merge", kinds)

    def test_log_includes_uncommitted_status_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._setup(Path(tmp))
            new_res = self._atb(repo, "new", "Uncommitted transition", "--agent", "gemini", "--json")
            tid = json.loads(new_res.stdout)["id"]
            self._commit_sheet(repo, tid, "add sheet", "2026-11-16T08:00:00Z")
            self.assertEqual(self._atb(repo, "start", tid).returncode, 0)

            res = self._atb(repo, "log", tid)
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn("uncommitted  todo → doing", res.stdout)

    def test_log_missing_task_exits_friendly(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._setup(Path(tmp))
            res = self._atb(repo, "log", "T999")
            self.assertEqual(res.returncode, 2)
            self.assertIn("T999", res.stderr)


    def test_log_survives_undecodable_runs_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._setup(Path(tmp))
            res = self._atb(repo, "new", "Task", "--agent", "gemini")
            self.assertEqual(res.returncode, 0, res.stderr)
            runs = repo / ".taskboard" / "runs"
            runs.mkdir(parents=True, exist_ok=True)
            good = json.dumps({"timestamp": "2026-11-16T08:00:00+00:00", "exit_code": 0,
                               "duration": 1.5, "command": "t", "output": "ok"})
            (runs / "T001.jsonl").write_bytes(b"\xff\xfe garbage\n" + good.encode() + b"\n")
            res = self._atb(repo, "log", "T001", "--json")
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertNotIn("Traceback", res.stderr)
            self.assertIn("1.5", res.stdout)

if __name__ == "__main__":
    unittest.main()
