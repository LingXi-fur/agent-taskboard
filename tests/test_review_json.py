"""Tests for ``atb review ID --json`` structured review output."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agent_taskboard import sheet as sheet_mod

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


class ReviewJsonTests(unittest.TestCase):
    def _env(self, home: Path) -> dict[str, str]:
        env = os.environ.copy()
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        env["GIT_CONFIG_SYSTEM"] = os.devnull
        env["GIT_CONFIG_GLOBAL"] = os.devnull
        env["HOME"] = str(home)
        env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
        return env

    def _git(self, cwd: Path, *args: str) -> None:
        subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            env=self._env(cwd),
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
        (repo / "src" / "app.py").write_text("line1\n", encoding="utf-8")
        (repo / "src" / "secret.py").write_text("secret\n", encoding="utf-8")
        self._git(repo, "add", "-A")
        self._git(repo, "commit", "-qm", "init")
        self.assertEqual(self._atb(repo, "init").returncode, 0)
        self._git(repo, "add", ".taskboard")
        self._git(repo, "commit", "-qm", "init atb")
        return repo

    def _create_task(
        self, repo: Path, title: str, *, allow=("src/app.py",), deny=("src/secret.py",)
    ) -> tuple[str, Path]:
        cmd = ["new", title, "--agent", "gemini"]
        for pattern in allow:
            cmd.extend(["--allow", pattern])
        for pattern in deny:
            cmd.extend(["--deny", pattern])
        cmd.append("--json")
        res = self._atb(repo, *cmd)
        self.assertEqual(res.returncode, 0, res.stderr)
        tid = json.loads(res.stdout)["id"]
        sheet_path = repo / ".taskboard" / "tasks" / f"{tid}.md"
        s = sheet_mod.parse(sheet_path.read_text(encoding="utf-8"))
        s.body = "\n## 验收\n\n- [x] first check\n- [ ] second check\n"
        sheet_path.write_text(sheet_mod.dump(s), encoding="utf-8")
        self._git(repo, "add", str(sheet_path))
        self._git(repo, "commit", "-qm", f"add {tid}")

        start_res = self._atb(repo, "start", tid, "--json")
        self.assertEqual(start_res.returncode, 0, start_res.stderr)
        wt = Path(json.loads(start_res.stdout)["worktree"])
        return tid, wt

    def _write_test_run(self, repo: Path, tid: str, exit_code: int) -> None:
        runs = repo / ".taskboard" / "runs"
        runs.mkdir(parents=True, exist_ok=True)
        (runs / f"{tid}.jsonl").write_text(
            json.dumps({
                "timestamp": "2026-11-18T08:00:00+00:00",
                "exit_code": exit_code,
                "duration": 1.5,
                "output": "done\n",
            }) + "\n",
            encoding="utf-8",
        )

    def test_ready_true_all_fields_sort_keys_and_out_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            tid, wt = self._create_task(repo, "Ready task")
            (wt / "src" / "app.py").write_text("line1\nline2\n", encoding="utf-8")
            self._git(wt, "add", "src/app.py")
            self._git(wt, "commit", "-qm", "update app")
            self._write_test_run(repo, tid, 0)

            out_file = base / "review.json"
            res = self._atb(repo, "review", tid, "--json", "--out", str(out_file))
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn("review written to", res.stdout)
            raw_json = out_file.read_text(encoding="utf-8")
            data = json.loads(raw_json)

            # Verify sort_keys=True serialization
            self.assertEqual(
                raw_json.strip(),
                json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2),
            )

            expected_top = {
                "id", "title", "agent", "status", "branch", "base",
                "scope", "test", "activity", "doctor_fails", "diffstat",
                "conflicts", "mergeable", "drift", "acceptance", "ready", "blockers",
            }
            self.assertEqual(set(data), expected_top)
            self.assertEqual(data["id"], tid)
            self.assertEqual(data["status"], "review")
            self.assertEqual(data["scope"], {"denied": [], "outside": [], "uncommitted": []})
            self.assertEqual(data["test"]["result"], "ok")
            self.assertEqual(data["test"]["last_run"]["exit_code"], 0)
            self.assertEqual(data["activity"]["dirty"], 0)
            self.assertIsInstance(data["activity"]["idle_seconds"], int)
            self.assertEqual(data["activity"]["rework_count"], 0)
            self.assertEqual(data["doctor_fails"], [])
            self.assertEqual(
                data["diffstat"]["totals"],
                {"files": 1, "insertions": 1, "deletions": 0},
            )
            self.assertEqual(data["diffstat"]["files"][0]["path"], "src/app.py")
            self.assertEqual(
                data["acceptance"],
                [
                    {"text": "first check", "checked": True},
                    {"text": "second check", "checked": False},
                ],
            )
            self.assertTrue(data["ready"])
            self.assertEqual(data["blockers"], [])

            # Status on disk was flipped from doing -> review
            on_disk = sheet_mod.parse(
                (repo / ".taskboard" / "tasks" / f"{tid}.md").read_text(encoding="utf-8")
            )
            self.assertEqual(on_disk.status, "review")

    def test_all_blocker_codes_and_bilingual_messages(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            # T001: scope violation (denied + outside), uncommitted, test fail, conflict with target, doctor fail
            t1, wt1 = self._create_task(repo, "Blocked task")
            (wt1 / "src" / "app.py").write_text("task edit\n", encoding="utf-8")
            (wt1 / "src" / "secret.py").write_text("viol\n", encoding="utf-8")
            (wt1 / "extra.txt").write_text("outside\n", encoding="utf-8")
            self._git(wt1, "add", "-A")
            self._git(wt1, "commit", "-qm", "violate and edit app")
            (wt1 / "wip.txt").write_text("uncommitted\n", encoding="utf-8")
            self._write_test_run(repo, t1, 1)

            # Conflict against main (target) on src/app.py
            (repo / "src" / "app.py").write_text("main edit\n", encoding="utf-8")
            self._git(repo, "add", "src/app.py")
            self._git(repo, "commit", "-qm", "main diverges")

            # Also create T002 touching src/app.py cleanly to exercise cross-task conflict too
            t2, wt2 = self._create_task(repo, "Other conflicting task")
            (wt2 / "src" / "app.py").write_text("t2 edit\n", encoding="utf-8")
            self._git(wt2, "add", "src/app.py")
            self._git(wt2, "commit", "-qm", "t2 edit")

            # Check zh blockers before deleting worktree
            zh_res = self._atb(repo, "review", t1, "--json", "--lang", "zh")
            self.assertEqual(zh_res.returncode, 0, zh_res.stderr)
            zh_data = json.loads(zh_res.stdout)
            self.assertFalse(zh_data["ready"])
            zh_codes = {b["code"]: b["message"] for b in zh_data["blockers"]}
            self.assertEqual(
                set(zh_codes),
                {"scope_violation", "uncommitted", "test_not_passed", "merge_conflict"},
            )
            self.assertIn("src/secret.py", zh_data["scope"]["denied"])
            self.assertIn("extra.txt", zh_data["scope"]["outside"])
            self.assertIn("wip.txt", zh_data["scope"]["uncommitted"])

            # Delete wt1 while keeping branch -> triggers doctor_fail too
            shutil.rmtree(wt1)
            en_res = self._atb(repo, "review", t1, "--json", "--lang", "en")
            self.assertEqual(en_res.returncode, 0, en_res.stderr)
            en_data = json.loads(en_res.stdout)
            self.assertFalse(en_data["ready"])
            en_codes = {b["code"]: b["message"] for b in en_data["blockers"]}
            self.assertEqual(
                set(en_codes),
                {"scope_violation", "test_not_passed", "merge_conflict", "doctor_fail"},
            )
            self.assertEqual(set(zh_data), set(en_data))
            for code in ("scope_violation", "test_not_passed", "merge_conflict", "doctor_fail"):
                self.assertIsNone(
                    re.search(r"[\u4e00-\u9fff]", en_codes[code]),
                    f"Chinese leaked in en blocker {code}: {en_codes[code]}",
                )
            for code in ("scope_violation", "test_not_passed", "merge_conflict"):
                self.assertNotEqual(zh_codes[code], en_codes[code])


if __name__ == "__main__":
    unittest.main()
