"""Tests for ``atb stats``: per-agent throughput, rework and cycle time."""

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

#: 2026-05-29-ish and 2026-06-10-ish, far enough apart for --since cuts.
T0 = 1_780_000_000
RECENT = 1_790_000_000

REWORK_BODY = "\n## 背景\n\nx\n\n## 返工\n\n- [2026-05-29T10:00:00+00:00] fix it\n"
PLAIN_BODY = "\n## 背景\n\nx\n"


class StatsTests(unittest.TestCase):
    def _env(self, base: Path, ts: int | None = None) -> dict[str, str]:
        env = os.environ.copy()
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        env["GIT_CONFIG_SYSTEM"] = os.devnull
        env["GIT_CONFIG_GLOBAL"] = os.devnull
        env["HOME"] = str(base)
        env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
        if ts is not None:
            env["GIT_COMMITTER_DATE"] = f"{ts} +0000"
            env["GIT_AUTHOR_DATE"] = f"{ts} +0000"
        return env

    def _git(self, cwd: Path, *args: str, ts: int | None = None) -> None:
        subprocess.run(
            ["git", *args], cwd=str(cwd), env=self._env(cwd, ts),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=True,
        )

    def _atb(self, repo: Path, *args: str):
        return subprocess.run(
            [sys.executable, "-m", "agent_taskboard", *args],
            cwd=str(repo), env=self._env(repo), capture_output=True,
            text=True, encoding="utf-8", errors="replace",
        )

    def _setup(self, base: Path) -> Path:
        repo = base / "repo"
        repo.mkdir()
        self._git(repo, "init", "-q", "-b", "main")
        self._git(repo, "config", "user.name", "t")
        self._git(repo, "config", "user.email", "t@t")
        (repo / "src").mkdir()
        (repo / "src" / "app.py").write_text("line\n", encoding="utf-8")
        self._git(repo, "add", "-A")
        self._git(repo, "commit", "-qm", "base", ts=T0 - 100_000)
        (repo / ".taskboard" / "tasks").mkdir(parents=True)
        (repo / ".taskboard" / "config.toml").write_text(
            'worktree_root = "../wts"\n', encoding="utf-8"
        )
        return repo

    def _stage(
        self, repo: Path, tid: str, agent: str, status: str, ts: int,
        *, body: str = PLAIN_BODY,
    ) -> None:
        """Write the sheet with ``status`` and commit it at time ``ts``."""
        sheet = sheet_mod.Sheet(
            id=tid, title=f"Task {tid}", agent=agent, status=status,
            allow=["src/**"], body=body,
        )
        (repo / ".taskboard" / "tasks" / f"{tid}.md").write_text(
            sheet_mod.dump(sheet), encoding="utf-8"
        )
        self._git(repo, "add", ".taskboard")
        self._git(repo, "commit", "-qm", f"{tid} -> {status}", ts=ts)

    def _write_sheet_only(self, repo: Path, tid: str, agent: str, status: str) -> None:
        """A sheet that exists on disk but was never committed (no history)."""
        sheet = sheet_mod.Sheet(
            id=tid, title=f"Task {tid}", agent=agent, status=status,
            allow=["src/**"], body=PLAIN_BODY,
        )
        (repo / ".taskboard" / "tasks" / f"{tid}.md").write_text(
            sheet_mod.dump(sheet), encoding="utf-8"
        )

    def _write_run(self, repo: Path, tid: str, exit_code: int, ts: int) -> None:
        runs = repo / ".taskboard" / "runs"
        runs.mkdir(parents=True, exist_ok=True)
        record = {
            "timestamp": f"{ts}-placeholder",
            "exit_code": exit_code,
            "duration": 0.1,
            "command": "python3 -c 'pass'",
            "output": "",
        }
        record["timestamp"] = "2026-05-29T12:00:00+00:00" if ts < RECENT else "2026-06-10T12:00:00+00:00"
        with (runs / f"{tid}.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _prepared(self, base: Path) -> Path:
        repo = self._setup(base)
        # opencode: done w/ rework+passing test, dropped, active w/ rework,
        # done w/ failing test, done never committed, recent done.
        self._stage(repo, "T001", "opencode", "todo", T0, body=REWORK_BODY)
        self._stage(repo, "T001", "opencode", "doing", T0 + 600, body=REWORK_BODY)
        self._stage(repo, "T001", "opencode", "review", T0 + 3600, body=REWORK_BODY)
        self._stage(repo, "T001", "opencode", "done", T0 + 7200, body=REWORK_BODY)
        self._write_run(repo, "T001", 0, T0 + 7200)

        self._stage(repo, "T002", "opencode", "todo", T0)
        self._stage(repo, "T002", "opencode", "doing", T0 + 60)
        self._stage(repo, "T002", "opencode", "dropped", T0 + 120)

        self._stage(repo, "T003", "opencode", "todo", T0, body=REWORK_BODY)
        self._stage(repo, "T003", "opencode", "doing", T0 + 60, body=REWORK_BODY)

        self._stage(repo, "T004", "opencode", "todo", T0)
        self._stage(repo, "T004", "opencode", "doing", T0 + 300)
        self._stage(repo, "T004", "opencode", "review", T0 + 600)
        self._stage(repo, "T004", "opencode", "done", T0 + 1200)
        self._write_run(repo, "T004", 1, T0 + 1200)

        self._write_sheet_only(repo, "T007", "opencode", "done")

        self._stage(repo, "T008", "opencode", "todo", RECENT)
        self._stage(repo, "T008", "opencode", "doing", RECENT + 60)
        self._stage(repo, "T008", "opencode", "review", RECENT + 120)
        self._stage(repo, "T008", "opencode", "done", RECENT + 300)
        self._write_run(repo, "T008", 0, RECENT + 300)

        # gemini: one clean done task without test records; one never committed.
        self._stage(repo, "T005", "gemini", "todo", T0)
        self._stage(repo, "T005", "gemini", "doing", T0 + 100)
        self._stage(repo, "T005", "gemini", "review", T0 + 200)
        self._stage(repo, "T005", "gemini", "done", T0 + 400)
        self._write_sheet_only(repo, "T006", "gemini", "todo")
        return repo

    def _agent_row(self, text: str, name: str) -> str:
        for line in text.splitlines():
            if line.startswith(name):
                return line
        self.fail(f"no row for agent {name!r} in:\n{text}")

    # ------------------------------------------------------------------ #

    def test_stats_table_zh(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._prepared(Path(tmp))

            result = self._atb(repo, "stats")
            self.assertEqual(result.returncode, 0, result.stderr)
            out = result.stdout
            self.assertIn("按 Agent 统计", out)

            row = self._agent_row(out, "opencode")
            cells = row.split()
            self.assertEqual(cells[0], "opencode")
            self.assertEqual(cells[1:4], ["4", "1", "1"])  # done, dropped, active
            self.assertIn("33%", row)  # rework rate and test failure rate
            self.assertIn("15m", row)  # cycle median (900s)
            self.assertIn("1.8h", row)  # cycle P90 (6600s)

            gemini = self._agent_row(out, "gemini")
            self.assertIn("—", gemini)  # no test records at all

            self.assertIn("2 个任务缺少状态历史", out)

    def test_stats_table_en(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._prepared(Path(tmp))

            result = self._atb(repo, "stats", "--lang", "en")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Per-agent statistics", result.stdout)
            self.assertIn("Note: 2 task(s)", result.stdout)
            self.assertIn("agent", result.stdout)

    def test_stats_json_structure_and_numbers(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._prepared(Path(tmp))

            result = self._atb(repo, "stats", "--json")
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(result.stdout)
            self.assertEqual(
                set(data),
                {"since", "agent_filter", "agents", "missing_history_total", "budgets"}
            )
            self.assertIsNone(data["since"])
            self.assertIsNone(data["agent_filter"])
            self.assertEqual(data["missing_history_total"], 2)

            by_agent = {entry["agent"]: entry for entry in data["agents"]}
            self.assertEqual(set(by_agent), {"opencode", "gemini"})

            oc = by_agent["opencode"]
            self.assertEqual(
                set(oc),
                {
                    "agent", "tasks", "done", "dropped", "active", "rework_rate",
                    "avg_rework_count", "cycle_median_seconds", "cycle_p90_seconds",
                    "cycle_samples", "review_wait_median_seconds",
                    "review_wait_samples", "test_failure_rate", "test_samples",
                    "avg_tokens_per_done_task", "token_samples",
                    "missing_history",
                },
            )
            self.assertEqual(oc["tasks"], 6)
            self.assertEqual(oc["done"], 4)
            self.assertEqual(oc["dropped"], 1)
            self.assertEqual(oc["active"], 1)
            self.assertEqual(oc["rework_rate"], 0.3333)
            self.assertEqual(oc["avg_rework_count"], 0.333)
            # cycles: T004=900, T001=6600, T008=240 → median 900, p90 6600
            self.assertEqual(oc["cycle_samples"], 3)
            self.assertEqual(oc["cycle_median_seconds"], 900)
            self.assertEqual(oc["cycle_p90_seconds"], 6600)
            # review waits: 600, 3600, 180 → median 600
            self.assertEqual(oc["review_wait_median_seconds"], 600)
            self.assertEqual(oc["test_failure_rate"], 0.3333)
            self.assertEqual(oc["test_samples"], 3)
            self.assertEqual(oc["missing_history"], 1)

            gemini = by_agent["gemini"]
            self.assertEqual(gemini["done"], 1)
            self.assertEqual(gemini["cycle_median_seconds"], 300)
            self.assertEqual(gemini["review_wait_median_seconds"], 200)
            self.assertIsNone(gemini["test_failure_rate"])
            self.assertEqual(gemini["missing_history"], 1)

    def test_stats_agent_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._prepared(Path(tmp))

            result = self._atb(repo, "stats", "--agent", "gemini")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("gemini", result.stdout)
            self.assertNotIn("opencode", result.stdout)

    def test_stats_since_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._prepared(Path(tmp))

            result = self._atb(repo, "stats", "--since", "2026-06-05", "--json")
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(result.stdout)
            self.assertEqual(data["missing_history_total"], 0)
            self.assertEqual(len(data["agents"]), 1)
            oc = data["agents"][0]
            self.assertEqual(oc["agent"], "opencode")
            self.assertEqual(oc["tasks"], 1)  # only the recent T008
            self.assertEqual(oc["done"], 1)
            self.assertEqual(oc["missing_history"], 0)

    def test_stats_reworked_and_dropped_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._prepared(Path(tmp))

            data = json.loads(self._atb(repo, "stats", "--json").stdout)
            oc = {entry["agent"]: entry for entry in data["agents"]}["opencode"]
            # T001 (done) and T003 (active) both carry one rework note.
            self.assertGreater(oc["rework_rate"], 0)
            self.assertEqual(oc["avg_rework_count"], 0.333)
            self.assertEqual(oc["dropped"], 1)

    def test_stats_empty_repo_does_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._setup(Path(tmp))  # no tasks at all

            result = self._atb(repo, "stats")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("（没有任务）", result.stdout)
            self.assertIn("无缺少历史的任务", result.stdout)

            result = self._atb(repo, "stats", "--lang", "en")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("(no tasks)", result.stdout)

    def test_stats_invalid_since(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._prepared(Path(tmp))

            result = self._atb(repo, "stats", "--since", "not-a-date")
            self.assertEqual(result.returncode, 2, result.stdout)
            self.assertIn("YYYY-MM-DD", result.stderr)

    def test_stats_requires_init(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = base / "repo"
            repo.mkdir()
            self._git(repo, "init", "-q", "-b", "main")

            result = self._atb(repo, "stats")
            self.assertEqual(result.returncode, 2, result.stdout)
            self.assertIn("atb init", result.stderr)


if __name__ == "__main__":
    unittest.main()
