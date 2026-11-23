"""Tests for ``atb note``: storage, validation, timeline and review."""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from agent_taskboard import sheet as sheet_mod

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


class NoteTests(unittest.TestCase):
    def _env(self, base: Path) -> dict[str, str]:
        env = os.environ.copy()
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        env["GIT_CONFIG_SYSTEM"] = os.devnull
        env["GIT_CONFIG_GLOBAL"] = os.devnull
        env["HOME"] = str(base)
        env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
        return env

    def _git(self, cwd: Path, *args: str, ts: int | None = None) -> None:
        env = self._env(cwd)
        if ts is not None:
            env["GIT_COMMITTER_DATE"] = f"{ts} +0000"
            env["GIT_AUTHOR_DATE"] = f"{ts} +0000"
        subprocess.run(
            ["git", *args], cwd=str(cwd), env=env,
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
        result = self._atb(repo, "init")
        self.assertEqual(result.returncode, 0, result.stderr)
        self._git(repo, "add", "-A")
        self._git(repo, "commit", "-qm", "init atb")
        return repo

    def _write_sheet(
        self, repo: Path, tid: str = "T001", *, status: str = "todo",
        agent: str = "opencode",
    ) -> None:
        sheet = sheet_mod.Sheet(
            id=tid, title="Task", agent=agent, status=status,
            allow=["src/**"], test="python3 -c 'pass'",
            body="\n## 背景\n\nx\n\n## 验收\n\n- [ ] item\n",
        )
        (repo / ".taskboard" / "tasks" / f"{tid}.md").write_text(
            sheet_mod.dump(sheet), encoding="utf-8"
        )

    def _notes_path(self, repo: Path, tid: str = "T001") -> Path:
        return repo / ".taskboard" / "notes" / f"{tid}.jsonl"

    def _notes(self, repo: Path, tid: str = "T001") -> list[dict]:
        path = self._notes_path(repo, tid)
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    # ------------------------------------------------------------------ #
    # append / list / --json / @FILE
    # ------------------------------------------------------------------ #

    def test_append_and_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            self._write_sheet(repo)

            result = self._atb(repo, "note", "T001", "waiting on review")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("已记录 T001 的备注（opencode）", result.stdout)

            stored = self._notes(repo)
            self.assertEqual(len(stored), 1)
            self.assertEqual(stored[0]["by"], "opencode")
            self.assertEqual(stored[0]["text"], "waiting on review")
            self.assertIn("timestamp", stored[0])

            listed = self._atb(repo, "note", "T001")
            self.assertEqual(listed.returncode, 0, listed.stderr)
            self.assertIn("opencode", listed.stdout)
            self.assertIn("waiting on review", listed.stdout)

    def test_append_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            self._write_sheet(repo)

            result = self._atb(repo, "note", "T001", "hello", "--json")
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(
                set(payload), {"id", "timestamp", "by", "text"}
            )
            self.assertEqual(payload["id"], "T001")
            self.assertEqual(payload["text"], "hello")
            self.assertEqual(payload["by"], "opencode")

            listed = self._atb(repo, "note", "T001", "--json")
            payload = json.loads(listed.stdout)
            self.assertEqual(payload["id"], "T001")
            self.assertEqual(len(payload["notes"]), 1)
            self.assertEqual(payload["notes"][0]["text"], "hello")

    def test_note_from_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            self._write_sheet(repo)
            note_file = base / "note.md"
            note_file.write_text("from a file\nsecond line\n", encoding="utf-8")

            result = self._atb(repo, "note", "T001", f"@{note_file}")
            self.assertEqual(result.returncode, 0, result.stderr)
            stored = self._notes(repo)
            self.assertEqual(stored[0]["text"], "from a file\nsecond line")

    def test_by_and_multiline(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            self._write_sheet(repo)

            result = self._atb(repo, "note", "T001", "line one\nline two", "--by", "human")
            self.assertEqual(result.returncode, 0, result.stderr)
            stored = self._notes(repo)
            self.assertEqual(stored[0]["by"], "human")
            self.assertEqual(stored[0]["text"], "line one\nline two")

    def test_list_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            self._write_sheet(repo)

            result = self._atb(repo, "note", "T001")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("（无备注）", result.stdout)
            en = self._atb(repo, "note", "T001", "--lang", "en")
            self.assertIn("(no notes)", en.stdout)

    # ------------------------------------------------------------------ #
    # validation
    # ------------------------------------------------------------------ #

    def test_rejects_empty_and_whitespace(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            self._write_sheet(repo)

            for text in ("", "   ", "\n\n"):
                result = self._atb(repo, "note", "T001", text)
                self.assertEqual(result.returncode, 2, (text, result.stdout))
                self.assertIn("must not be empty", result.stderr)
            self.assertEqual(self._notes(repo), [])

    def test_rejects_too_long_without_truncating(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            self._write_sheet(repo)

            result = self._atb(repo, "note", "T001", "x" * 4001)
            self.assertEqual(result.returncode, 2, result.stdout)
            self.assertIn("4000", result.stderr)
            self.assertEqual(self._notes(repo), [])

            ok = self._atb(repo, "note", "T001", "x" * 4000)
            self.assertEqual(ok.returncode, 0, ok.stderr)
            self.assertEqual(len(self._notes(repo)), 1)

    def test_missing_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)

            result = self._atb(repo, "note", "T999", "hello")
            self.assertEqual(result.returncode, 2, result.stdout)
            self.assertIn("not found", result.stderr)

    def test_done_task_still_accepts_notes(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            self._write_sheet(repo, status="done")

            result = self._atb(repo, "note", "T001", "post-merge note")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(self._notes(repo)), 1)

    # ------------------------------------------------------------------ #
    # timeline and review
    # ------------------------------------------------------------------ #

    def test_note_sits_between_status_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            self._write_sheet(repo)
            self._git(repo, "add", ".taskboard")
            self._git(repo, "commit", "-qm", "add task")

            # commit the status transitions around the note: the note uses
            # real "now", so put "doing" in the past and "review" in the future
            past_start = int(time.time()) - 1000
            future_review = int(time.time()) + 1000
            self.assertEqual(self._atb(repo, "start", "T001").returncode, 0)
            self._git(repo, "add", ".taskboard")
            self._git(repo, "commit", "-qm", "mark doing", ts=past_start)

            self.assertEqual(self._atb(repo, "note", "T001", "mid-task note").returncode, 0)

            self.assertEqual(self._atb(repo, "review", "T001").returncode, 0)
            self._git(repo, "add", ".taskboard")
            self._git(repo, "commit", "-qm", "mark review", ts=future_review)

            result = self._atb(repo, "log", "T001", "--json")
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            kinds = [event["kind"] for event in payload["events"]]
            self.assertIn("note", kinds)
            note_index = kinds.index("note")
            self.assertGreater(note_index, 0)
            self.assertLess(note_index, len(kinds) - 1)
            self.assertEqual(kinds[0], "status")
            self.assertEqual(kinds[-1], "status")
            note_event = payload["events"][note_index]
            self.assertEqual(note_event["by"], "opencode")
            self.assertEqual(note_event["note"], "mid-task note")

            text = self._atb(repo, "log", "T001")
            self.assertIn("备注（opencode）", text.stdout)
            self.assertIn("mid-task note", text.stdout)

    def test_review_shows_last_three_notes(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            self._write_sheet(repo)
            self.assertEqual(self._atb(repo, "start", "T001").returncode, 0)
            for index in range(1, 5):
                result = self._atb(repo, "note", "T001", f"note {index}")
                self.assertEqual(result.returncode, 0, result.stderr)

            result = self._atb(repo, "review", "T001", "--json")
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(len(payload["notes"]), 3)
            self.assertEqual(
                [note["text"] for note in payload["notes"]],
                ["note 2", "note 3", "note 4"],
            )

            text = self._atb(repo, "review", "T001")
            self.assertEqual(text.returncode, 0, text.stderr)
            self.assertIn("## 最近备注", text.stdout)
            self.assertNotIn("note 1", text.stdout)
            self.assertIn("note 4", text.stdout)

            en = self._atb(repo, "review", "T001", "--lang", "en")
            self.assertIn("## Recent notes", en.stdout)

    # ------------------------------------------------------------------ #
    # .gitignore and doctor
    # ------------------------------------------------------------------ #

    def test_init_writes_gitignore_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            text = (repo / ".gitignore").read_text(encoding="utf-8")
            self.assertIn(".taskboard/runs/", text)
            self.assertIn(".taskboard/notes/", text)

            # idempotent: a second init does not duplicate the lines
            self.assertEqual(self._atb(repo, "init").returncode, 0)
            text = (repo / ".gitignore").read_text(encoding="utf-8")
            self.assertEqual(text.count(".taskboard/notes/"), 1)

    def test_doctor_warns_when_gitignore_missing_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            self._write_sheet(repo)

            ok = self._atb(repo, "doctor", "--json")
            by_name = {item["name"]: item for item in json.loads(ok.stdout)["checks"]}
            self.assertEqual(by_name["gitignore"]["status"], "OK")

            (repo / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
            warned = self._atb(repo, "doctor", "--json")
            self.assertEqual(warned.returncode, 0, warned.stderr)
            payload = json.loads(warned.stdout)
            by_name = {item["name"]: item for item in payload["checks"]}
            self.assertEqual(by_name["gitignore"]["status"], "WARN")
            self.assertIn("notes", by_name["gitignore"]["message"])
            self.assertNotEqual(payload["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
