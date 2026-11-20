"""End-to-end tests for ``atb sessions`` and the doctor adapter checks."""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agent_taskboard import sheet as sheet_mod

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
FIXTURES = ROOT / "tests" / "fixtures" / "adapters"

PLACEHOLDER = "REPO_WT_PLACEHOLDER"


def _copy_fixture(source: Path, target: Path, worktree: str) -> None:
    """Copy a fixture tree, rewriting the worktree placeholder.

    The path is written with forward slashes: on Windows a backslash path
    embedded in a JSONL line would make the line invalid JSON, and pathlib
    compares forward- and backslash forms as equal anyway.
    """
    posix_worktree = worktree.replace(os.sep, "/")
    for path in source.rglob("*"):
        if path.is_dir():
            continue
        destination = target / path.relative_to(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        text = path.read_text(encoding="utf-8")
        destination.write_text(
            text.replace(PLACEHOLDER, posix_worktree), encoding="utf-8"
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
            'ses_demo1', '{directory}', 'demo task',
            '{{"id":"glm-5.3","providerID":"opencode-go"}}',
            1795000000000, 1795000600000,
            1795000600000, 1795000000000, NULL,
            11223, 2537, 99000, 4000
        );
        INSERT INTO session_message VALUES (
            'msg2', 'ses_demo1', 'assistant', 2, 1795000600000, 1795000600000,
            '{{"content":[{{"type":"text","text":"Skeleton in place."}}]}}'
        );
        """
    )
    connection.commit()
    connection.close()


class SessionsTests(unittest.TestCase):
    def _env(self, base: Path = None) -> dict[str, str]:
        base = base or self._base
        env = os.environ.copy()
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        env["GIT_CONFIG_SYSTEM"] = os.devnull
        env["GIT_CONFIG_GLOBAL"] = os.devnull
        env["HOME"] = str(base)
        env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
        # point every adapter at a controlled copy of the fixtures
        env["ATB_CLAUDE_HOME"] = str(base / "claude-home")
        env["ATB_CODEX_HOME"] = str(base / "codex-home")
        env["ATB_GEMINI_HOME"] = str(base / "gemini-home")
        env["ATB_OPENCODE_HOME"] = str(base / "oc-home")
        return env

    def _git(self, cwd: Path, *args: str) -> None:
        subprocess.run(
            ["git", *args], cwd=str(cwd), env=self._env(),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=True,
        )

    def _atb(self, repo: Path, *args: str):
        return subprocess.run(
            [sys.executable, "-m", "agent_taskboard", *args],
            cwd=str(repo), env=self._env(), capture_output=True,
            text=True, encoding="utf-8", errors="replace",
        )

    def _setup(self, base: Path, *, corrupt_gemini: bool = False) -> Path:
        self._base = base
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
        for tid, title in (("T001", "Demo T001"), ("T002", "Other task")):
            sheet = sheet_mod.Sheet(
                id=tid, title=title, agent="opencode", status="todo",
                allow=["src/**"], test="python3 -c 'pass'",
                body="\n## 背景\n\nx\n\n## 验收\n\n- [ ] item\n",
            )
            (repo / ".taskboard" / "tasks" / f"{tid}.md").write_text(
                sheet_mod.dump(sheet), encoding="utf-8"
            )
            self.assertEqual(self._atb(repo, "start", tid).returncode, 0)
        worktree = base / "wts" / "T001"

        _copy_fixture(
            FIXTURES / "claude_code" / "projects",
            base / "claude-home" / "projects",
            str(worktree),
        )
        _copy_fixture(
            FIXTURES / "codex" / "sessions",
            base / "codex-home" / "sessions",
            str(worktree),
        )
        if corrupt_gemini:
            store = base / "gemini-home" / "tmp" / "hash" / "chats"
            store.mkdir(parents=True)
            (store / "session-broken.json").write_text("{not json", encoding="utf-8")
        else:
            _copy_fixture(
                FIXTURES / "gemini_cli" / "tmp",
                base / "gemini-home" / "tmp",
                str(worktree),
            )
        _build_db(base / "oc-home" / "opencode.db", str(worktree).replace(os.sep, "/"))
        return repo

    # ------------------------------------------------------------------ #

    def test_sessions_json_mapping_and_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)

            result = self._atb(repo, "sessions", "--since", "2026-01-01", "--json")
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)

            by_adapter = {
                entry["name"]: entry for entry in payload["adapters"]
            }
            self.assertEqual(by_adapter["claude-code"]["count"], 2)
            self.assertEqual(by_adapter["codex"]["count"], 1)
            self.assertEqual(by_adapter["gemini-cli"]["count"], 1)
            self.assertEqual(by_adapter["opencode"]["count"], 1)
            for name, entry in by_adapter.items():
                self.assertTrue(entry["available"], name)
                self.assertIsNone(entry["error"], entry)

            rows = payload["sessions"]
            self.assertEqual(len(rows), 5)
            by_id = {row["id"]: row for row in rows}
            # cwd mapping: claude 0001, codex and opencode run in T001's worktree
            self.assertEqual(by_id["claude-demo-0001"]["task"], "T001")
            self.assertEqual(by_id["codex-demo-0001"]["task"], "T001")
            self.assertEqual(by_id["ses_demo1"]["task"], "T001")
            # branch mapping: claude 0002's gitBranch is T002's branch
            self.assertEqual(by_id["claude-demo-0002"]["task"], "T002")
            # gemini's cwd is nowhere near a worktree: a free session
            self.assertIsNone(by_id["gemini-demo-0001"]["task"])
            self.assertEqual(payload["unmapped"], 1)

            mapped = by_id["claude-demo-0001"]
            self.assertEqual(mapped["state"], "working")
            self.assertEqual(mapped["model"], "claude-opus-5-5")
            self.assertEqual(mapped["branch"], "opencode/demo-task")
            self.assertEqual(
                mapped["last_message"],
                "Now add rate limiting on the same endpoint",
            )
            self.assertEqual(
                mapped["usage"],
                {"input": 1200, "output": 340, "cache_read": 8000, "cache_write": 1500},
            )

    def test_sessions_redact_hides_summaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)

            result = self._atb(
                repo, "sessions", "--since", "2026-01-01", "--json", "--redact"
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertTrue(payload["redacted"])
            for row in payload["sessions"]:
                self.assertIsNone(row["last_message"])

            text = self._atb(repo, "sessions", "--since", "2026-01-01", "--redact")
            self.assertEqual(text.returncode, 0, text.stderr)
            self.assertNotIn("rate limiting", text.stdout)
            self.assertNotIn("摘要", text.stdout)

    def test_sessions_text_zh_and_en(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)

            zh = self._atb(repo, "sessions", "--since", "2026-01-01")
            self.assertEqual(zh.returncode, 0, zh.stderr)
            self.assertIn("Agent 会话", zh.stdout)
            self.assertIn("自由会话", zh.stdout)
            self.assertIn("工作中", zh.stdout)
            self.assertIn("rate limiting", zh.stdout)

            en = self._atb(repo, "sessions", "--since", "2026-01-01", "--lang", "en")
            self.assertEqual(en.returncode, 0, en.stderr)
            self.assertIn("Agent sessions", en.stdout)
            self.assertIn("free session", en.stdout)
            self.assertIn("working", en.stdout)
            self.assertIn("unmapped: 1", en.stdout)

    def test_sessions_adapter_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)

            result = self._atb(
                repo, "sessions", "--since", "2026-01-01",
                "--adapter", "codex", "--json",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(len(payload["adapters"]), 1)
            self.assertEqual(payload["adapters"][0]["name"], "codex")
            self.assertEqual(len(payload["sessions"]), 1)
            self.assertEqual(payload["sessions"][0]["adapter"], "codex")

    def test_sessions_since_window_excludes_old_activity(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)

            # every fixture records activity on 2026-11-19: a later cut hides all
            result = self._atb(repo, "sessions", "--since", "2026-11-20", "--json")
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["sessions"], [])
            for entry in payload["adapters"]:
                self.assertEqual(entry["count"], 0, entry)

    def test_broken_adapter_does_not_break_the_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base, corrupt_gemini=True)

            result = self._atb(repo, "sessions", "--since", "2026-01-01", "--json")
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            by_adapter = {
                entry["name"]: entry for entry in payload["adapters"]
            }
            self.assertIn("not valid JSON", by_adapter["gemini-cli"]["error"])
            # every other adapter still delivered its sessions
            self.assertEqual(by_adapter["claude-code"]["count"], 2)
            self.assertEqual(by_adapter["codex"]["count"], 1)
            self.assertEqual(by_adapter["opencode"]["count"], 1)
            self.assertEqual(len(payload["sessions"]), 4)

            text = self._atb(repo, "sessions", "--since", "2026-01-01")
            self.assertEqual(text.returncode, 0, text.stderr)
            self.assertIn("gemini-cli 报错", text.stdout)

    def test_missing_stores_are_reported_not_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            # wipe two stores: the adapters report inactive, nothing crashes
            shutil.rmtree(base / "claude-home")
            shutil.rmtree(base / "gemini-home")

            result = self._atb(repo, "sessions", "--since", "2026-01-01", "--json")
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            by_adapter = {
                entry["name"]: entry for entry in payload["adapters"]
            }
            self.assertFalse(by_adapter["claude-code"]["available"])
            self.assertFalse(by_adapter["gemini-cli"]["available"])
            self.assertEqual(len(payload["sessions"]), 2)

            text = self._atb(repo, "sessions", "--since", "2026-01-01")
            self.assertIn("未检测到存储", text.stdout)

    def test_invalid_since_is_a_usage_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)

            result = self._atb(repo, "sessions", "--since", "not-a-spec")
            self.assertEqual(result.returncode, 2, result.stdout)
            self.assertIn("invalid --since", result.stderr)

    def test_doctor_lists_adapter_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)

            result = self._atb(repo, "doctor", "--json")
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            by_name = {item["name"]: item for item in payload["checks"]}
            for name in ("adapter:claude-code", "adapter:codex",
                         "adapter:gemini-cli", "adapter:opencode"):
                self.assertIn(name, by_name)
                self.assertEqual(by_name[name]["status"], "OK", by_name[name])
            self.assertEqual(payload["status"], "OK")

            text = self._atb(repo, "doctor")
            self.assertEqual(text.returncode, 0, text.stderr)
            self.assertIn("adapter:claude-code", text.stdout)
            self.assertNotIn("FAIL", text.stdout)

    def test_doctor_marks_missing_store_info_and_error_warn(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base, corrupt_gemini=True)
            shutil.rmtree(base / "codex-home")

            result = self._atb(repo, "doctor", "--json")
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            by_name = {item["name"]: item for item in payload["checks"]}
            self.assertEqual(by_name["adapter:codex"]["status"], "INFO")
            self.assertEqual(by_name["adapter:gemini-cli"]["status"], "WARN")
            # a broken observer never turns the doctor red
            self.assertNotEqual(payload["status"], "FAIL")
            self.assertEqual(payload["exit_code"], 0)


if __name__ == "__main__":
    unittest.main()
