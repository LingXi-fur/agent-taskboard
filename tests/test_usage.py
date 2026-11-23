"""Tests for token usage: adapters, per-task aggregation, review/stats/serve."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_taskboard import sheet as sheet_mod
from agent_taskboard.adapters.claude_code import ClaudeCodeAdapter
from agent_taskboard.adapters.codex import CodexAdapter
from agent_taskboard.adapters.gemini_cli import GeminiCliAdapter

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

SINCE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _now_iso(offset: int = 0) -> str:
    return (
        datetime.now(timezone.utc) - timedelta(seconds=offset)
    ).isoformat().replace("+00:00", "Z")


def _write_claude_session(path: Path, cwd: str, lines: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for obj in lines:
            handle.write(json.dumps(obj) + "\n")


def _claude_message(stamp: str, cwd: str, msg_id: str, request_id: str,
                    usage: dict | None = None) -> dict:
    return {
        "type": "assistant",
        "timestamp": stamp,
        "cwd": cwd,
        "gitBranch": "opencode/demo",
        "sessionId": "s1",
        "requestId": request_id,
        "message": {
            "role": "assistant",
            "id": msg_id,
            "model": "claude-opus-5-5",
            "content": [{"type": "text", "text": "ok"}],
            **({"usage": usage} if usage is not None else {}),
        },
    }


class ClaudeUsageTests(unittest.TestCase):
    def test_streamed_duplicates_are_counted_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stamp = _now_iso()
            path = root / "projects" / "demo" / "s1.jsonl"
            usage = {
                "input_tokens": 100, "output_tokens": 40,
                "cache_read_input_tokens": 500,
                "cache_creation_input_tokens": 60,
            }
            lines = [
                _claude_message(stamp, "/wt", "m1", "r1", usage),
                _claude_message(stamp, "/wt", "m1", "r1", usage),  # streamed
                _claude_message(stamp, "/wt", "m1", "r1", usage),  # streamed
                _claude_message(stamp, "/wt", "m2", "r2",
                                {"input_tokens": 10, "output_tokens": 5}),
                _claude_message(stamp, "/wt", "m3", "r3", None),  # no usage
            ]
            _write_claude_session(path, "/wt", lines)
            sessions = list(ClaudeCodeAdapter(root=root).sessions(SINCE))
            self.assertEqual(len(sessions), 1)
            self.assertEqual(
                sessions[0].resolved_usage(),
                {
                    "input": 110, "output": 45, "cache_read": 500,
                    "cache_write": 60, "total": 715,
                },
            )

    def test_missing_usage_stays_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "projects" / "demo" / "s2.jsonl"
            _write_claude_session(
                path, "/wt", [_claude_message(_now_iso(), "/wt", "m1", "r1")]
            )
            sessions = list(ClaudeCodeAdapter(root=root).sessions(SINCE))
            self.assertIsNone(sessions[0].resolved_usage())


class GeminiUsageTests(unittest.TestCase):
    def test_tokens_are_summed_across_messages(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scratch = root / "tmp" / "hash"
            chats = scratch / "chats"
            chats.mkdir(parents=True)
            (scratch / ".project_root").write_text("/project\n", encoding="utf-8")
            data = {
                "sessionId": "g1",
                "startTime": _now_iso(120),
                "lastUpdated": _now_iso(),
                "messages": [
                    {"type": "user", "content": "hi"},
                    {"type": "gemini", "model": "gemini-2.5-pro",
                     "content": "one",
                     "tokens": {"input": 100, "output": 20, "cached": 300,
                                "total": 420}},
                    {"type": "user", "content": "again"},
                    {"type": "gemini", "model": "gemini-2.5-pro",
                     "content": "two",
                     "tokens": {"input": 50, "output": 10, "cached": 0,
                                "total": 60}},
                ],
            }
            (chats / "session-x.json").write_text(
                json.dumps(data), encoding="utf-8"
            )
            sessions = list(GeminiCliAdapter(root=root).sessions(SINCE))
            self.assertEqual(len(sessions), 1)
            self.assertEqual(
                sessions[0].usage,
                {"input": 150, "output": 30, "cache_read": 300, "total": 480},
            )


class CodexUsageTests(unittest.TestCase):
    def _rollout(self, root: Path, rows: list[dict]) -> None:
        store = root / "sessions" / "2026" / "11"
        store.mkdir(parents=True, exist_ok=True)
        path = store / "rollout-2026-11-23.jsonl"
        with path.open("w", encoding="utf-8") as handle:
            for obj in rows:
                handle.write(json.dumps(obj) + "\n")

    def test_cumulative_thread_usage_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stamp = _now_iso()
            rows = [
                {"type": "session_meta", "timestamp": stamp,
                 "payload": {"session_id": "c1", "cwd": "/wt",
                             "timestamp": stamp}},
                {"type": "token_usage_record", "timestamp": stamp,
                 "payload": {"thread_token_usage": {
                     "input_tokens": 1000, "cached_input_tokens": 500,
                     "cache_write_input_tokens": 0, "output_tokens": 100}}},
                {"type": "token_usage_record", "timestamp": stamp,
                 "payload": {"thread_token_usage": {
                     "input_tokens": 1500, "cached_input_tokens": 800,
                     "cache_write_input_tokens": 0, "output_tokens": 160}}},
            ]
            self._rollout(root, rows)
            sessions = list(CodexAdapter(root=root).sessions(SINCE))
            self.assertEqual(
                sessions[0].usage,
                {"input": 1500, "output": 160, "cache_read": 800,
                 "cache_write": 0, "total": 2460},
            )

    def test_per_turn_usage_dedupes_response_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stamp = _now_iso()
            rows = [
                {"type": "session_meta", "timestamp": stamp,
                 "payload": {"session_id": "c2", "cwd": "/wt",
                             "timestamp": stamp}},
                {"type": "token_usage_record", "timestamp": stamp,
                 "payload": {"response_id": "r1", "usage": {
                     "input_tokens": 100, "output_tokens": 10}}},
                {"type": "token_usage_record", "timestamp": stamp,
                 "payload": {"response_id": "r1", "usage": {
                     "input_tokens": 100, "output_tokens": 10}}},
                {"type": "token_usage_record", "timestamp": stamp,
                 "payload": {"response_id": "r2", "usage": {
                     "input_tokens": 200, "output_tokens": 20}}},
            ]
            self._rollout(root, rows)
            sessions = list(CodexAdapter(root=root).sessions(SINCE))
            self.assertEqual(
                sessions[0].usage,
                {"input": 300, "output": 30, "total": 330},
            )


class UsageAggregationTests(unittest.TestCase):
    def _env(self, base: Path) -> dict[str, str]:
        env = os.environ.copy()
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        env["GIT_CONFIG_SYSTEM"] = os.devnull
        env["GIT_CONFIG_GLOBAL"] = os.devnull
        env["HOME"] = str(base)
        env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
        return env

    def _git(self, cwd: Path, *args: str) -> None:
        subprocess.run(
            ["git", *args], cwd=str(cwd), env=self._env(cwd),
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
        self._git(repo, "commit", "-qm", "base")
        (repo / ".taskboard" / "tasks").mkdir(parents=True)
        (repo / ".taskboard" / "config.toml").write_text(
            'worktree_root = "../wts"\n', encoding="utf-8"
        )
        (repo / ".gitignore").write_text(
            ".taskboard/runs/\n.taskboard/notes/\n", encoding="utf-8"
        )
        return repo

    def _sheet(self, repo: Path, tid: str, *, status: str = "todo") -> None:
        sheet = sheet_mod.Sheet(
            id=tid, title=f"Task {tid}", agent="opencode", status=status,
            allow=["src/**"], test="python3 -c 'pass'",
            body="\n## 背景\n\nx\n\n## 验收\n\n- [ ] item\n",
            created=datetime.now(timezone.utc) - timedelta(days=1),
            branch=f"opencode/{tid.lower()}",
        )
        (repo / ".taskboard" / "tasks" / f"{tid}.md").write_text(
            sheet_mod.dump(sheet), encoding="utf-8"
        )

    def _start_task(self, base: Path, repo: Path, tid: str = "T001") -> Path:
        self._sheet(repo, tid)
        self._git(repo, "add", ".taskboard")
        self._git(repo, "commit", "-qm", "add task")
        result = self._atb(repo, "start", tid)
        self.assertEqual(result.returncode, 0, result.stderr)
        worktree = base / "wts" / tid
        (worktree / "src" / "app.py").write_text("T001\n", encoding="utf-8")
        self._git(worktree, "add", "-A")
        self._git(worktree, "commit", "-qm", "work")
        return worktree

    def _claude_store(self, base: Path, worktree: Path, *, total_input: int) -> None:
        root = base / "claude-home"
        path = root / "projects" / "demo" / "s1.jsonl"
        stamp = _now_iso()
        usage = {"input_tokens": total_input, "output_tokens": 10,
                 "cache_read_input_tokens": 0,
                 "cache_creation_input_tokens": 0}
        # one session inside the task worktree, one elsewhere (unmapped)
        _write_claude_session(
            path, str(worktree),
            [_claude_message(stamp, str(worktree), "m1", "r1", usage)],
        )
        _write_claude_session(
            root / "projects" / "other" / "s2.jsonl", "/somewhere/else",
            [_claude_message(stamp, "/somewhere/else", "m2", "r2",
                             {"input_tokens": 999999, "output_tokens": 1})],
        )
        env = {"ATB_CLAUDE_HOME": str(root)}
        return env

    def test_review_reports_task_token_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            worktree = self._start_task(base, repo)
            # a finished-test record so review is otherwise happy
            runs = repo / ".taskboard" / "runs"
            runs.mkdir(parents=True, exist_ok=True)
            (runs / "T001.jsonl").write_text(
                json.dumps({"timestamp": _now_iso(), "exit_code": 0,
                            "duration": 0.1, "command": "python3 -c 'pass'",
                            "output": ""}) + "\n",
                encoding="utf-8",
            )
            env_targets = self._claude_store(base, worktree, total_input=1234)
            env = self._env(repo)
            env.update(env_targets)
            env["ATB_CODEX_HOME"] = str(base / "empty-codex")
            env["ATB_GEMINI_HOME"] = str(base / "empty-gemini")
            env["ATB_OPENCODE_HOME"] = str(base / "empty-oc")

            result = subprocess.run(
                [sys.executable, "-m", "agent_taskboard", "review", "T001", "--json"],
                cwd=str(repo), env=env, capture_output=True, text=True,
                encoding="utf-8", errors="replace",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            usage = payload["usage"]
            self.assertEqual(usage["sessions"], 1)
            self.assertEqual(usage["with_usage"], 1)
            self.assertEqual(usage["missing"], 0)
            self.assertEqual(usage["input"], 1234)
            self.assertEqual(usage["total"], 1244)

            text = subprocess.run(
                [sys.executable, "-m", "agent_taskboard", "review", "T001"],
                cwd=str(repo), env=env, capture_output=True, text=True,
                encoding="utf-8", errors="replace",
            )
            self.assertIn("## Token 用量", text.stdout)
            self.assertIn("1,244", text.stdout)

    def test_serve_task_payload_includes_usage(self):
        from agent_taskboard import cli as cli_mod
        from agent_taskboard import config as config_mod
        from agent_taskboard import serve as serve_mod

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            worktree = self._start_task(base, repo)
            env_targets = self._claude_store(base, worktree, total_input=50)
            saved = {key: os.environ.get(key) for key in env_targets}
            try:
                os.environ.update(env_targets)
                os.environ["ATB_CODEX_HOME"] = str(base / "empty-codex")
                os.environ["ATB_GEMINI_HOME"] = str(base / "empty-gemini")
                os.environ["ATB_OPENCODE_HOME"] = str(base / "empty-oc")
                cfg = config_mod.load_config(repo)
                payload = serve_mod._payload_task(repo, cfg, "T001")
                self.assertEqual(payload["usage"]["sessions"], 1)
                self.assertEqual(payload["usage"]["total"], 60)
            finally:
                for key in list(env_targets) + [
                    "ATB_CODEX_HOME", "ATB_GEMINI_HOME", "ATB_OPENCODE_HOME"
                ]:
                    value = saved.get(key)
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

    def test_stats_average_tokens_per_done_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            worktree = self._start_task(base, repo)
            # flip the sheet to done (the usage metric applies to done tasks)
            sheet_path = repo / ".taskboard" / "tasks" / "T001.md"
            sheet = sheet_mod.parse(sheet_path.read_text(encoding="utf-8"))
            sheet.status = "done"
            sheet_path.write_text(sheet_mod.dump(sheet), encoding="utf-8")
            env_targets = self._claude_store(base, worktree, total_input=800)
            env = self._env(repo)
            env.update(env_targets)
            env["ATB_CODEX_HOME"] = str(base / "empty-codex")
            env["ATB_GEMINI_HOME"] = str(base / "empty-gemini")
            env["ATB_OPENCODE_HOME"] = str(base / "empty-oc")

            result = subprocess.run(
                [sys.executable, "-m", "agent_taskboard", "stats", "--json"],
                cwd=str(repo), env=env, capture_output=True, text=True,
                encoding="utf-8", errors="replace",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(result.stdout)
            oc = {entry["agent"]: entry for entry in data["agents"]}["opencode"]
            self.assertEqual(oc["avg_tokens_per_done_task"], 810)
            self.assertEqual(oc["token_samples"], 1)

            text = subprocess.run(
                [sys.executable, "-m", "agent_taskboard", "stats", "--lang", "en"],
                cwd=str(repo), env=env, capture_output=True, text=True,
                encoding="utf-8", errors="replace",
            )
            self.assertIn("avg tokens/task", text.stdout)

    def test_stats_without_usage_shows_dash(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            self._sheet(repo, "T001", status="done")
            env = self._env(repo)
            for key in ("ATB_CLAUDE_HOME", "ATB_CODEX_HOME", "ATB_GEMINI_HOME",
                        "ATB_OPENCODE_HOME"):
                env[key] = str(base / f"empty-{key}")
            result = subprocess.run(
                [sys.executable, "-m", "agent_taskboard", "stats", "--json"],
                cwd=str(repo), env=env, capture_output=True, text=True,
                encoding="utf-8", errors="replace",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(result.stdout)
            oc = {entry["agent"]: entry for entry in data["agents"]}["opencode"]
            self.assertIsNone(oc["avg_tokens_per_done_task"])
            self.assertEqual(oc["token_samples"], 0)


if __name__ == "__main__":
    unittest.main()
