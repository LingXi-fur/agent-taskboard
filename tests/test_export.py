"""Tests for ``atb export --json`` (schema 1)."""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_taskboard import sheet as sheet_mod

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
FIXTURES = ROOT / "tests" / "fixtures" / "adapters"
PLACEHOLDER = "REPO_WT_PLACEHOLDER"


def _copy_fixture(source: Path, target: Path, worktree: str) -> None:
    posix = worktree.replace(os.sep, "/")
    for path in source.rglob("*"):
        if path.is_dir():
            continue
        destination = target / path.relative_to(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            path.read_text(encoding="utf-8").replace(PLACEHOLDER, posix),
            encoding="utf-8",
        )


def _build_db(path: Path, directory: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.executescript(
        f"""
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
        INSERT INTO session_v2 VALUES (
            'ses_demo1', '{directory}', 'demo', '{{"id":"glm-5.3"}}',
            1795000000000, 1795000600000,
            1795000600000, 1795000000000, NULL,
            11223, 2537, 99000, 4000
        );
        INSERT INTO session_message VALUES (
            'm1', 'ses_demo1', 'assistant', 1, 1795000600000, 1795000600000,
            '{{"content":[{{"type":"text","text":"hi"}}]}}'
        );
        """
    )
    connection.commit()
    connection.close()


class ExportTests(unittest.TestCase):
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
        proc = subprocess.run(
            ["git", *args], cwd=str(cwd), env=env, capture_output=True,
            text=True, encoding="utf-8", errors="replace", check=True,
        )
        return proc.stdout

    def _atb(self, cwd: Path, *args: str, env_extra=None):
        env = self._env(cwd)
        if env_extra:
            env.update(env_extra)
        return subprocess.run(
            [sys.executable, "-m", "agent_taskboard", *args],
            cwd=str(cwd), env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
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
        self._git(repo, "commit", "-qm", "base")
        (repo / ".taskboard" / "tasks").mkdir(parents=True)
        (repo / ".taskboard" / "config.toml").write_text(
            'worktree_root = "../wts"\n', encoding="utf-8"
        )
        (repo / ".gitignore").write_text(
            ".taskboard/runs/\n.taskboard/notes/\n", encoding="utf-8"
        )
        return repo

    def _sheet(self, repo: Path, tid: str, *, created: datetime,
               status: str = "todo") -> None:
        sheet = sheet_mod.Sheet(
            id=tid, title=f"Task {tid}", agent="opencode", status=status,
            allow=["src/**"], test="python3 -c 'pass'",
            created=created,
            body="\n## 背景\n\nx\n\n## 验收\n\n- [ ] item\n",
        )
        (repo / ".taskboard" / "tasks" / f"{tid}.md").write_text(
            sheet_mod.dump(sheet), encoding="utf-8"
        )

    def _start_and_done(self, base: Path, repo: Path) -> tuple[Path, str]:
        """T001: start, commit work, run tests, mark done and merge."""
        created = datetime(2026, 11, 20, 8, 0, tzinfo=timezone.utc)
        self._sheet(repo, "T001", created=created)
        self._git(repo, "add", ".taskboard")
        self._git(repo, "commit", "-qm", "add T001")
        self.assertEqual(self._atb(repo, "start", "T001").returncode, 0)
        worktree = base / "wts" / "T001"
        (worktree / "src" / "app.py").write_text("T001\n", encoding="utf-8")
        self._git(worktree, "add", "-A")
        self._git(worktree, "commit", "-qm", "work")
        runs = repo / ".taskboard" / "runs"
        runs.mkdir(parents=True, exist_ok=True)
        stamp = (created + timedelta(hours=3)).isoformat().replace("+00:00", "Z")
        (runs / "T001.jsonl").write_text(
            json.dumps({"timestamp": stamp, "exit_code": 0, "duration": 0.1,
                        "command": "python3 -c 'pass'", "output": ""}) + "\n",
            encoding="utf-8",
        )
        sheet_path = repo / ".taskboard" / "tasks" / "T001.md"
        sheet = sheet_mod.parse(sheet_path.read_text(encoding="utf-8"))
        sheet.status = "done"
        sheet_path.write_text(sheet_mod.dump(sheet), encoding="utf-8")
        self._git(repo, "add", ".taskboard")
        self._git(repo, "commit", "-qm", "mark done")
        branch = sheet.branch
        self._git(repo, "merge", "--no-ff", branch,
                  "-m", f"Merge {branch} (T001: Task T001)")
        sha = self._git(repo, "rev-parse", "HEAD").strip()
        return worktree, sha

    def _fixture_env(self, base: Path, worktree: Path) -> dict[str, str]:
        _copy_fixture(FIXTURES / "claude_code", base / "claude_code-copy",
                      str(worktree))
        _copy_fixture(FIXTURES / "codex", base / "codex-copy", str(worktree))
        _copy_fixture(FIXTURES / "gemini_cli", base / "gemini_cli-copy",
                      str(worktree))
        _build_db(base / "oc-home" / "opencode.db", str(worktree))
        return {
            "ATB_CLAUDE_HOME": str(base / "claude_code-copy"),
            "ATB_CODEX_HOME": str(base / "codex-copy"),
            "ATB_GEMINI_HOME": str(base / "gemini_cli-copy"),
            "ATB_OPENCODE_HOME": str(base / "oc-home"),
        }

    # ------------------------------------------------------------------ #

    def test_schema_fields_and_native_session_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            worktree, merge_sha = self._start_and_done(base, repo)
            self._sheet(repo, "T002", created=datetime(2026, 11, 21, tzinfo=timezone.utc))
            self._git(repo, "add", ".taskboard")
            self._git(repo, "commit", "-qm", "add T002")
            env = self._fixture_env(base, worktree)

            result = self._atb(repo, "export", "--json", env_extra=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            payload = json.loads(result.stdout)  # no stray prints

            self.assertEqual(payload["schema"], 1)
            self.assertEqual(payload["target"], "main")
            datetime.fromisoformat(payload["generated"].replace("Z", "+00:00"))
            self.assertEqual(
                Path(payload["repo"]).resolve(), Path(repo).resolve()
            )
            self.assertEqual([t["id"] for t in payload["tasks"]], ["T001", "T002"])

            t1 = payload["tasks"][0]
            self.assertEqual(
                set(t1),
                {"id", "title", "agent", "status", "branch", "created",
                 "done_at", "merged", "merge_commit", "rework_count",
                 "sessions", "usage"},
            )
            self.assertEqual(t1["status"], "done")
            self.assertTrue(t1["merged"])
            self.assertEqual(t1["merge_commit"], merge_sha)
            self.assertEqual(t1["rework_count"], 0)
            self.assertTrue(t1["created"].endswith("Z"))
            self.assertTrue(t1["done_at"].endswith("Z"))

            ids = {(s["adapter"], s["id"]) for s in t1["sessions"]}
            self.assertIn(("claude-code", "claude-demo-0001"), ids)
            self.assertIn(("codex", "codex-demo-0001"), ids)
            self.assertIn(("opencode", "ses_demo1"), ids)
            self.assertEqual(
                {s["adapter"] for s in t1["sessions"]},
                {"claude-code", "codex", "opencode"},  # gemini cwd unmapped
            )
            for session in t1["sessions"]:
                self.assertEqual(
                    set(session),
                    {"adapter", "id", "started_at", "last_active_at",
                     "from", "to", "basis", "usage"},
                )
                # whole-session mapping: the segment spans the whole session
                self.assertEqual(session["from"], session["started_at"])
                self.assertEqual(session["to"], session["last_active_at"])
                self.assertEqual(session["basis"], "worktree")

            # usage is the sum of the mapped sessions, tokens only
            self.assertEqual(t1["usage"]["input"], 1200 + 900 + 11223)
            self.assertEqual(t1["usage"]["total"], 11040 + 4250 + 116760)

            t2 = payload["tasks"][1]
            self.assertIsNone(t2["usage"])       # no sessions -> null
            self.assertEqual(t2["sessions"], [])
            self.assertFalse(t2["merged"])
            self.assertIsNone(t2["merge_commit"])

    def test_since_filters_by_last_activity(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            worktree, _sha = self._start_and_done(base, repo)
            env = self._fixture_env(base, worktree)

            future = self._atb(
                repo, "export", "--json", "--since", "2099-01-01", env_extra=env
            )
            self.assertEqual(future.returncode, 0, future.stderr)
            self.assertEqual(json.loads(future.stdout)["tasks"], [])

            past = self._atb(
                repo, "export", "--json", "--since", "2000-01-01", env_extra=env
            )
            self.assertEqual(
                [t["id"] for t in json.loads(past.stdout)["tasks"]], ["T001"]
            )

    def test_repo_is_main_worktree_when_run_from_a_task_worktree(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            worktree, _sha = self._start_and_done(base, repo)
            env = self._fixture_env(base, worktree)

            result = self._atb(worktree, "export", "--json", env_extra=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(
                Path(payload["repo"]).resolve(), Path(repo).resolve()
            )

    def test_max_days_limits_the_session_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            worktree, _sha = self._start_and_done(base, repo)
            env = self._fixture_env(base, worktree)

            result = self._atb(
                repo, "export", "--json", "--max-days", "1", env_extra=env
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            # fixture sessions are dated 2026-11-19: outside a 1-day window
            payload = json.loads(result.stdout)
            self.assertEqual(
                [t["sessions"] for t in payload["tasks"] if t["id"] == "T001"],
                [[]],
            )

    def test_json_is_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            result = self._atb(repo, "export")
            self.assertEqual(result.returncode, 2)
            self.assertIn("usage: atb export --json", result.stderr)
            self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
