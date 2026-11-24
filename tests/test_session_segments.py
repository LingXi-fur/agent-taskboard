"""Tests for splitting long sessions across tasks (evidence segmentation)."""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_taskboard import sessions
from agent_taskboard import sheet as sheet_mod
from agent_taskboard.adapters.base import Session, SessionEvent

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
UTC = timezone.utc

T0 = datetime(2026, 11, 10, 8, 0, tzinfo=UTC)


def _session(events, *, cwd="/elsewhere", usage=None):
    return Session(
        adapter="opencode", id="ses-long", cwd=cwd,
        started_at=events[0].timestamp if events else None,
        last_active_at=events[-1].timestamp if events else None,
        usage=usage, events=events,
    )


class SegmentUnitTests(unittest.TestCase):
    def setUp(self):
        self.worktrees = [
            (Path("/wts/T001"), "T001"),
            (Path("/wts/T002"), "T002"),
        ]
        self.branches = {}
        self.known = {"T001", "T002", "T003"}

    def _segments(self, session, windows):
        return sessions.segment_session(
            session, self.worktrees, self.branches, windows, self.known
        )

    def test_worktree_then_command_evidence(self):
        events = [
            SessionEvent(T0, "Read file_path=/wts/T001/src/app.py",
                         {"input": 100, "output": 10, "total": 110}),
            SessionEvent(T0 + timedelta(minutes=5), "", None),
            SessionEvent(T0 + timedelta(minutes=10),
                         "Bash command=atb check T002",
                         {"input": 200, "output": 20, "total": 220}),
        ]
        segments = self._segments(_session(events), {})
        self.assertEqual([s.task_id for s in segments], ["T001", "T002"])
        self.assertEqual([s.basis for s in segments], ["worktree", "command"])
        self.assertEqual(segments[0].usage, {"input": 100, "output": 10, "total": 110})
        self.assertEqual(segments[1].usage, {"input": 200, "output": 20, "total": 220})
        self.assertEqual(segments[0].start, T0)
        self.assertEqual(segments[0].end, T0 + timedelta(minutes=10))
        self.assertEqual(segments[1].end, events[-1].timestamp)

    def test_adjacent_same_task_segments_merge(self):
        events = [
            SessionEvent(T0, "Read file=/wts/T001/a.py"),
            SessionEvent(T0 + timedelta(minutes=1), "Read file=/wts/T001/b.py"),
            SessionEvent(T0 + timedelta(minutes=2), "atb review T001"),
        ]
        segments = self._segments(_session(events), {})
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].task_id, "T001")
        self.assertEqual(segments[0].start, T0)

    def test_time_window_only_when_unique(self):
        windows = {
            "T001": (T0.timestamp(), (T0 + timedelta(hours=1)).timestamp()),
            "T002": (T0.timestamp() + 7200, None),
        }
        inside = [SessionEvent(T0 + timedelta(minutes=10), "just a sentence")]
        segments = self._segments(_session(inside), windows)
        self.assertEqual([(s.task_id, s.basis) for s in segments],
                         [("T001", "time_window")])

    def test_overlapping_windows_are_not_guessed(self):
        windows = {
            "T001": (T0.timestamp(), T0.timestamp() + 3600),
            "T002": (T0.timestamp() + 1800, T0.timestamp() + 7200),
        }
        events = [SessionEvent(T0 + timedelta(minutes=40), "no evidence")]
        self.assertEqual(self._segments(_session(events), windows), [])

    def test_window_ends_switch_segment(self):
        windows = {
            "T001": (T0.timestamp(), T0.timestamp() + 3600),
            "T003": (T0.timestamp() + 7200, None),
        }
        events = [
            SessionEvent(T0 + timedelta(minutes=5), "file=/wts/T001/x.py",
                         {"input": 10, "total": 10}),
            SessionEvent(T0 + timedelta(hours=3), "plain message",
                         {"input": 30, "total": 30}),
        ]
        segments = self._segments(_session(events), windows)
        self.assertEqual([(s.task_id, s.basis) for s in segments],
                         [("T001", "worktree"), ("T003", "time_window")])
        self.assertEqual(segments[1].usage, {"input": 30, "total": 30})

    def test_unmapped_window_with_no_candidate_drops_the_stretch(self):
        windows = {"T001": (T0.timestamp(), T0.timestamp() + 60)}
        events = [
            SessionEvent(T0 + timedelta(minutes=5), "file=/wts/T001/a.py",
                         {"input": 10, "total": 10}),
            SessionEvent(T0 + timedelta(hours=5), "later, no evidence",
                         {"input": 99, "total": 99}),
        ]
        segments = self._segments(_session(events), windows)
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].usage, {"input": 10, "total": 10})

    def test_whole_session_mapping_unchanged(self):
        events = [SessionEvent(T0, "Read file=/wts/T002/a.py")]
        session = Session(
            adapter="opencode", id="ses-whole",
            cwd="/wts/T001/sub", started_at=T0,
            last_active_at=T0 + timedelta(hours=2),
            usage={"input": 500, "output": 5, "total": 505},
            events=events,
        )
        segments = self._segments(session, {})
        self.assertEqual(len(segments), 1)
        segment = segments[0]
        self.assertEqual(segment.task_id, "T001")
        self.assertEqual(segment.basis, "worktree")
        self.assertEqual(segment.start, session.started_at)
        self.assertEqual(segment.end, session.last_active_at)
        self.assertEqual(segment.usage, session.usage)

    def test_unknown_atb_command_id_is_ignored(self):
        events = [SessionEvent(T0, "atb check T999")]
        self.assertEqual(self._segments(_session(events), {}), [])


class ExportSegmentsTests(unittest.TestCase):
    """End-to-end: a long OpenCode session split across three tasks."""

    def _env(self, base: Path) -> dict[str, str]:
        env = os.environ.copy()
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        env["GIT_CONFIG_SYSTEM"] = os.devnull
        env["GIT_CONFIG_GLOBAL"] = os.devnull
        env["HOME"] = str(base)
        env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
        return env

    def _git(self, cwd: Path, *args: str, ts: int | None = None) -> str:
        env = self._env(cwd)
        if ts is not None:
            env["GIT_COMMITTER_DATE"] = f"{ts} +0000"
            env["GIT_AUTHOR_DATE"] = f"{ts} +0000"
        return subprocess.run(
            ["git", *args], cwd=str(cwd), env=env, capture_output=True,
            text=True, encoding="utf-8", errors="replace", check=True,
        ).stdout

    def _atb(self, cwd: Path, *args: str, env_extra=None):
        env = self._env(cwd)
        if env_extra:
            env.update(env_extra)
        return subprocess.run(
            [sys.executable, "-m", "agent_taskboard", *args],
            cwd=str(cwd), env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )

    def _write_sheet(self, repo: Path, tid: str) -> None:
        sheet = sheet_mod.Sheet(
            id=tid, title=f"Task {tid}", agent="opencode", status="todo",
            allow=["src/**"], test="python3 -c 'pass'",
            body="\n## 背景\n\nx\n\n## 验收\n\n- [ ] item\n",
        )
        (repo / ".taskboard" / "tasks" / f"{tid}.md").write_text(
            sheet_mod.dump(sheet), encoding="utf-8"
        )

    def _set_status(self, repo: Path, tid: str, status: str) -> None:
        path = repo / ".taskboard" / "tasks" / f"{tid}.md"
        sheet = sheet_mod.parse(path.read_text(encoding="utf-8"))
        sheet.status = status
        path.write_text(sheet_mod.dump(sheet), encoding="utf-8")

    def _build_db(self, path: Path, worktrees: dict[str, Path]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path)
        connection.executescript(
            """
            CREATE TABLE session_v2 (
                id TEXT, directory TEXT, title TEXT, model TEXT,
                time_created INTEGER, time_updated INTEGER,
                time_busy INTEGER, time_idle INTEGER, time_archived INTEGER,
                tokens_input INTEGER, tokens_output INTEGER,
                tokens_cache_read INTEGER, tokens_cache_write INTEGER
            );
            CREATE TABLE session_message (
                id TEXT, session_id TEXT, type TEXT, seq INTEGER,
                time_created INTEGER, time_updated INTEGER, data TEXT
            );
            """
        )
        base_ms = int(T0.timestamp() * 1000)
        connection.execute(
            "INSERT INTO session_v2 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("ses-long", "/elsewhere", "long", '{"id":"glm-5.3"}',
             base_ms, base_ms + 3_700_000, base_ms + 3_700_000, base_ms + 3_700_000,
             None, 600, 60, 0, 0),
        )
        messages = [
            (base_ms + 60_000,
             {"time": {"created": base_ms + 60_000},
              "content": [{"type": "tool", "name": "Read",
                           "state": {"input": {"filePath": str(worktrees["T001"] / "src" / "app.py")}}}],
              "tokens": {"input": 100, "output": 10, "total": 110}}),
            (base_ms + 1_860_000,
             {"time": {"created": base_ms + 1_860_000},
              "content": [{"type": "tool", "name": "Bash",
                           "state": {"input": {"command": "atb check T002"}}}],
              "tokens": {"input": 200, "output": 20, "total": 220}}),
            (base_ms + 3_660_000,
             {"time": {"created": base_ms + 3_660_000},
              "content": [{"type": "text", "text": "SECRET_BODY_TEXT working"}],
              "tokens": {"input": 300, "output": 30, "total": 330}}),
        ]
        for index, (created, data) in enumerate(messages):
            connection.execute(
                "INSERT INTO session_message VALUES (?,?,?,?,?,?,?)",
                (f"m{index}", "ses-long", "assistant", index, created, created,
                 json.dumps(data)),
            )
        connection.commit()
        connection.close()

    def test_long_session_split_by_three_evidences(self):
        with tempfile.TemporaryDirectory() as tmp:
            # macOS resolves /var -> /private/var; keep every path in the
            # resolved form so the fixture and the stored worktree paths match.
            base = Path(tmp).resolve()
            repo = base / "repo"
            repo.mkdir()
            self._git(repo, "init", "-q", "-b", "main")
            self._git(repo, "config", "user.name", "t")
            self._git(repo, "config", "user.email", "t@t")
            (repo / "src").mkdir()
            (repo / "src" / "app.py").write_text("line\n", encoding="utf-8")
            self._git(repo, "add", "-A")
            self._git(repo, "commit", "-qm", "base")
            (repo / ".taskboard" / "tasks").mkdir(parents=True)
            (repo / ".taskboard" / "config.toml").write_text(
                'worktree_root = "../wts"\n', encoding="utf-8"
            )
            (repo / ".gitignore").write_text(
                ".taskboard/runs/\n.taskboard/notes/\n", encoding="utf-8"
            )
            for tid in ("T001", "T002", "T003"):
                self._write_sheet(repo, tid)
            self._git(repo, "add", ".taskboard")
            self._git(repo, "commit", "-qm", "add tasks")

            t = int(T0.timestamp())
            worktrees = {}
            # T001: doing at t0, review at t0+1200 (window [t0, t0+1200))
            self.assertEqual(self._atb(repo, "start", "T001").returncode, 0)
            worktrees["T001"] = base / "wts" / "T001"
            self._git(repo, "add", ".taskboard")
            self._git(repo, "commit", "-qm", "T001 doing", ts=t)
            self._set_status(repo, "T001", "review")
            self._git(repo, "add", ".taskboard")
            self._git(repo, "commit", "-qm", "T001 review", ts=t + 1200)
            # T002: doing at t0+1800, review at t0+3000
            self.assertEqual(self._atb(repo, "start", "T002").returncode, 0)
            worktrees["T002"] = base / "wts" / "T002"
            self._git(repo, "add", ".taskboard")
            self._git(repo, "commit", "-qm", "T002 doing", ts=t + 1800)
            self._set_status(repo, "T002", "review")
            self._git(repo, "add", ".taskboard")
            self._git(repo, "commit", "-qm", "T002 review", ts=t + 3000)
            # T003: doing at t0+3600, no review -> open window
            self.assertEqual(self._atb(repo, "start", "T003").returncode, 0)
            worktrees["T003"] = base / "wts" / "T003"
            self._git(repo, "add", ".taskboard")
            self._git(repo, "commit", "-qm", "T003 doing", ts=t + 3600)

            self._build_db(base / "oc-home" / "opencode.db", worktrees)
            env = {
                "ATB_OPENCODE_HOME": str(base / "oc-home"),
                "ATB_CLAUDE_HOME": str(base / "empty-claude"),
                "ATB_CODEX_HOME": str(base / "empty-codex"),
                "ATB_GEMINI_HOME": str(base / "empty-gemini"),
            }
            result = self._atb(repo, "export", "--json", env_extra=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("SECRET_BODY_TEXT", result.stdout)
            payload = json.loads(result.stdout)
            by_id = {task["id"]: task for task in payload["tasks"]}

            t1 = by_id["T001"]["sessions"]
            self.assertEqual(len(t1), 1)
            self.assertEqual((t1[0]["adapter"], t1[0]["id"]), ("opencode", "ses-long"))
            self.assertEqual(t1[0]["basis"], "worktree")
            self.assertEqual(t1[0]["usage"], {"input": 100, "output": 10, "total": 110})

            t2 = by_id["T002"]["sessions"]
            self.assertEqual(t2[0]["basis"], "command")
            self.assertEqual(t2[0]["usage"], {"input": 200, "output": 20, "total": 220})

            t3 = by_id["T003"]["sessions"]
            self.assertEqual(t3[0]["basis"], "time_window")
            self.assertEqual(t3[0]["usage"], {"input": 300, "output": 30, "total": 330})

            # segments don't overlap: each from/to is ordered and inside
            for task in (t1, t2, t3):
                self.assertLessEqual(task[0]["from"], task[0]["to"])
            self.assertLessEqual(t1[0]["to"], t2[0]["from"])
            self.assertLessEqual(t2[0]["to"], t3[0]["from"])
            # the three segments add up to the session's recorded total
            totals = {"input": 0, "output": 0, "total": 0}
            for task in (t1, t2, t3):
                for key in totals:
                    totals[key] += task[0]["usage"][key]
            self.assertEqual(
                totals, {"input": 600, "output": 60, "total": 660}
            )
            # task-level usage is the segment sum
            self.assertEqual(by_id["T003"]["usage"], t3[0]["usage"])
            # schema unchanged
            self.assertEqual(payload["schema"], 1)


if __name__ == "__main__":
    unittest.main()
