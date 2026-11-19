"""Unit tests for the built-in session adapters and the loader."""

import importlib.metadata
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from agent_taskboard import adapters
from agent_taskboard.adapters import base
from agent_taskboard.adapters.base import AdapterError
from agent_taskboard.adapters.claude_code import ClaudeCodeAdapter
from agent_taskboard.adapters.codex import CodexAdapter
from agent_taskboard.adapters.gemini_cli import GeminiCliAdapter
from agent_taskboard.adapters.opencode import OpenCodeAdapter

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "adapters"

SINCE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _build_opencode_db(path: Path) -> None:
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
        INSERT INTO session_v2 VALUES (
            'ses_demo1', 'REPO_WT_PLACEHOLDER', 'demo task',
            '{"id":"glm-5.3","providerID":"opencode-go"}',
            1795000000000, 1795000600000,
            1795000600000, 1795000000000, NULL,
            11223, 2537, 99000, 4000
        );
        INSERT INTO session_message VALUES (
            'msg2', 'ses_demo1', 'assistant', 2, 1795000600000, 1795000600000,
            '{"content":[{"type":"text","text":"Skeleton in place."}]}'
        );
        INSERT INTO session_message VALUES (
            'msg1', 'ses_demo1', 'user', 1, 1795000000000, 1795000000000,
            '{"content":[{"type":"text","text":"Start the skeleton"}]}'
        );
        """
    )
    connection.commit()
    connection.close()


class AdapterBaseTests(unittest.TestCase):
    def test_parse_iso_and_epoch(self):
        parsed = base.parse_iso("2026-11-19T09:00:00Z")
        self.assertEqual(parsed, datetime(2026, 11, 19, 9, tzinfo=timezone.utc))
        self.assertIsNone(base.parse_iso("nope"))
        self.assertIsNone(base.parse_iso(""))
        self.assertEqual(
            base.epoch_ms(1795000000000),
            datetime.fromtimestamp(1795000000, tz=timezone.utc),
        )
        self.assertIsNone(base.epoch_ms("x"))

    def test_classify_state_rules(self):
        now = datetime(2026, 11, 19, 12, tzinfo=timezone.utc)
        recent = now - base.timedelta(minutes=5)
        old = now - base.timedelta(hours=3)
        self.assertEqual(
            base.classify_state(
                last_user_after_reply=True, last_active=old, now=now
            ),
            "working",
        )
        self.assertEqual(
            base.classify_state(
                last_user_after_reply=False, last_active=recent, now=now
            ),
            "waiting",
        )
        self.assertEqual(
            base.classify_state(
                last_user_after_reply=False, last_active=old, now=now
            ),
            "idle",
        )
        self.assertEqual(
            base.classify_state(
                busy=True, last_user_after_reply=False, last_active=old, now=now
            ),
            "working",
        )
        self.assertEqual(
            base.classify_state(last_user_after_reply=False, last_active=None),
            "unknown",
        )

    def test_summarize_truncates_to_120(self):
        text = "word " * 100
        self.assertEqual(len(base.summarize(text)), 120)
        self.assertTrue(base.summarize(text).endswith("…"))
        self.assertEqual(base.summarize("  a\n b  "), "a b")


class ClaudeCodeAdapterTests(unittest.TestCase):
    def test_reads_fixture(self):
        adapter = ClaudeCodeAdapter(root=FIXTURES / "claude_code")
        self.assertTrue(adapter.available())
        sessions = list(adapter.sessions(SINCE))
        self.assertEqual(len(sessions), 2)
        first = next(s for s in sessions if s.id == "claude-demo-0001")
        self.assertEqual(first.cwd, "REPO_WT_PLACEHOLDER")
        self.assertEqual(first.branch, "opencode/demo-task")
        self.assertEqual(first.model, "claude-opus-5-5")
        self.assertEqual(
            first.usage,
            {"input": 1200, "output": 340, "cache_read": 8000, "cache_write": 1500},
        )
        self.assertEqual(first.state, "working")  # user turn without a reply
        self.assertEqual(
            first.last_message, "Now add rate limiting on the same endpoint"
        )
        self.assertEqual(
            first.started_at, datetime(2026, 11, 19, 9, tzinfo=timezone.utc)
        )

    def test_env_override(self):
        with mock.patch.dict(
            os.environ, {"ATB_CLAUDE_HOME": str(FIXTURES / "claude_code")}
        ):
            adapter = ClaudeCodeAdapter()
            self.assertEqual(
                adapter.store(), FIXTURES / "claude_code" / "projects"
            )

    def test_missing_store_is_unavailable(self):
        adapter = ClaudeCodeAdapter(root=Path(tempfile.mkdtemp(prefix="nope-")))
        self.assertFalse(adapter.available())
        self.assertEqual(list(adapter.sessions(SINCE)), [])

    def test_directory_named_like_a_file_raises_adapter_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "projects" / "demo"
            store.mkdir(parents=True)
            (store / "not-a-file.jsonl").mkdir()
            adapter = ClaudeCodeAdapter(root=Path(tmp))
            with self.assertRaises(AdapterError):
                list(adapter.sessions(SINCE))

    def test_big_file_is_read_head_and_tail_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "projects" / "big"
            root.mkdir(parents=True)
            path = root / "session.jsonl"
            with path.open("w", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "type": "user",
                            "timestamp": "2026-11-19T09:00:00Z",
                            "cwd": "/big",
                            "sessionId": "big-1",
                            "message": {"role": "user", "content": "start"},
                        }
                    ) + "\n"
                )
                filler = json.dumps(
                    {"type": "user", "timestamp": "2026-11-19T09:01:00Z",
                     "message": {"role": "user", "content": "filler line"}}
                ) + "\n"
                handle.write(filler * 2000)  # well beyond one 64 KB window
                handle.write(
                    json.dumps(
                        {
                            "type": "user",
                            "timestamp": "2026-11-19T10:00:00Z",
                            "cwd": "/big",
                            "gitBranch": "demo/branch",
                            "sessionId": "big-1",
                            "message": {"role": "user", "content": "final"},
                        }
                    ) + "\n"
                )
            adapter = ClaudeCodeAdapter(root=Path(tmp))
            sessions = list(adapter.sessions(SINCE))
            self.assertEqual(len(sessions), 1)
            session = sessions[0]
            self.assertEqual(session.last_message, "final")
            self.assertEqual(session.cwd, "/big")
            self.assertEqual(session.branch, "demo/branch")
            self.assertGreater(path.stat().st_size, base.TAIL_BYTES)


class CodexAdapterTests(unittest.TestCase):
    def test_reads_fixture(self):
        adapter = CodexAdapter(root=FIXTURES / "codex")
        self.assertTrue(adapter.available())
        sessions = list(adapter.sessions(SINCE))
        self.assertEqual(len(sessions), 1)
        session = sessions[0]
        self.assertEqual(session.id, "codex-demo-0001")
        self.assertEqual(session.cwd, "REPO_WT_PLACEHOLDER")
        self.assertEqual(session.model, "gpt-5.2-codex")
        self.assertEqual(session.state, "working")
        self.assertEqual(session.last_message, "One more pass on the naming please")
        self.assertEqual(
            session.usage,
            {"input": 900, "output": 250, "cache_read": 3000, "cache_write": 100},
        )
        self.assertEqual(session.extra["originator"], "codex-cli")

    def test_env_override(self):
        with mock.patch.dict(os.environ, {"ATB_CODEX_HOME": str(FIXTURES / "codex")}):
            adapter = CodexAdapter()
            self.assertEqual(adapter.store(), FIXTURES / "codex" / "sessions")

    @unittest.skipUnless(
        shutil.which("zstd") is not None,
        "the zstd CLI is an optional external binary; the plain-JSONL path "
        "is what CI exercises",
    )
    def test_zstd_compressed_rollout(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "sessions" / "2026"
            store.mkdir(parents=True)
            source = (
                FIXTURES / "codex" / "sessions" / "2026" / "11" / "19"
            ).glob("rollout-*.jsonl")
            shutil.copyfile(
                next(source),
                store / "rollout-2026-11-19.jsonl",
            )
            subprocess.run(
                ["zstd", "-qf", str(store / "rollout-2026-11-19.jsonl")],
                check=True, capture_output=True,
            )
            (store / "rollout-2026-11-19.jsonl").unlink()  # keep only the .zst
            self.assertTrue((store / "rollout-2026-11-19.jsonl.zst").is_file())
            adapter = CodexAdapter(root=Path(tmp))
            sessions = list(adapter.sessions(SINCE))
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0].id, "codex-demo-0001")
            self.assertEqual(sessions[0].last_message, "One more pass on the naming please")


class GeminiCliAdapterTests(unittest.TestCase):
    def test_reads_fixture(self):
        adapter = GeminiCliAdapter(root=FIXTURES / "gemini_cli")
        self.assertTrue(adapter.available())
        sessions = list(adapter.sessions(SINCE))
        self.assertEqual(len(sessions), 1)
        session = sessions[0]
        self.assertEqual(session.id, "gemini-demo-0001")
        self.assertEqual(session.cwd, "/scratch/gemini-place")
        self.assertEqual(session.model, "gemini-2.5-pro")
        self.assertEqual(session.state, "working")
        self.assertEqual(session.last_message, "Draft the fix next please")
        self.assertEqual(
            session.usage, {"input": 700, "output": 120, "cache_read": 2100}
        )

    def test_env_override(self):
        with mock.patch.dict(
            os.environ, {"ATB_GEMINI_HOME": str(FIXTURES / "gemini_cli")}
        ):
            adapter = GeminiCliAdapter()
            self.assertEqual(adapter.store(), FIXTURES / "gemini_cli" / "tmp")

    def test_corrupt_json_raises_adapter_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "tmp" / "hash" / "chats"
            store.mkdir(parents=True)
            (store / "session-x.json").write_text("{not json", encoding="utf-8")
            adapter = GeminiCliAdapter(root=Path(tmp))
            with self.assertRaises(AdapterError):
                list(adapter.sessions(SINCE))


class OpenCodeAdapterTests(unittest.TestCase):
    def test_reads_database_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "opencode" / "opencode.db"
            _build_opencode_db(db)
            adapter = OpenCodeAdapter(root=Path(tmp) / "opencode")
            self.assertTrue(adapter.available())
            sessions = list(adapter.sessions(SINCE))
            self.assertEqual(len(sessions), 1)
            session = sessions[0]
            self.assertEqual(session.id, "ses_demo1")
            self.assertEqual(session.cwd, "REPO_WT_PLACEHOLDER")
            self.assertEqual(session.model, "glm-5.3")
            self.assertEqual(session.state, "working")  # busy > idle
            self.assertEqual(session.last_message, "Skeleton in place.")
            self.assertEqual(
                session.usage,
                {"input": 11223, "output": 2537, "cache_read": 99000, "cache_write": 4000},
            )

    def test_corrupt_database_raises_adapter_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "opencode" / "opencode.db"
            db.parent.mkdir(parents=True)
            db.write_bytes(b"this is not sqlite" * 100)
            adapter = OpenCodeAdapter(root=Path(tmp) / "opencode")
            self.assertTrue(adapter.available())
            with self.assertRaises(AdapterError):
                list(adapter.sessions(SINCE))

    def test_env_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "oc-data"
            _build_opencode_db(target / "opencode.db")
            with mock.patch.dict(os.environ, {"ATB_OPENCODE_HOME": str(target)}):
                adapter = OpenCodeAdapter()
                self.assertEqual(
                    adapter.db_path(), target / "opencode.db"
                )
                self.assertEqual(len(list(adapter.sessions(SINCE))), 1)


class LoaderTests(unittest.TestCase):
    def test_builtins_present(self):
        state = adapters.load_adapters()
        names = [adapter.name for adapter in state.adapters]
        self.assertIn("claude-code", names)
        self.assertIn("codex", names)
        self.assertIn("gemini-cli", names)
        self.assertIn("opencode", names)
        self.assertEqual(state.errors, [])

    def test_entry_point_registration(self):
        class DemoAdapter:
            name = "demo-tool"

            def available(self) -> bool:
                return True

            def sessions(self, since):
                return iter([])

        def broken():
            raise RuntimeError("plugin exploded")

        fake_entries = [
            SimpleNamespace(name="demo-tool", load=lambda: DemoAdapter),
            SimpleNamespace(name="broken", load=broken),
        ]
        with mock.patch.object(
            importlib.metadata,
            "entry_points",
            return_value=fake_entries,
        ):
            state = adapters.load_adapters()
        names = [adapter.name for adapter in state.adapters]
        self.assertIn("demo-tool", names)
        self.assertEqual(len(state.errors), 1)
        self.assertEqual(state.errors[0]["adapter"], "broken")
        self.assertIn("plugin exploded", state.errors[0]["error"])


if __name__ == "__main__":
    unittest.main()
