"""Tests for ``atb doctor`` (one-shot read-only health check)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent_taskboard import doctor, sheet as sheet_mod

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


class DoctorTests(unittest.TestCase):
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
            text=True,
            check=True,
        )

    def _atb(self, repo: Path, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "agent_taskboard", *args],
            cwd=str(repo),
            env=self._env(repo),
            capture_output=True,
            text=True,
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
        res = self._atb(repo, "init")
        self.assertEqual(res.returncode, 0, res.stderr)
        self._git(repo, "add", ".taskboard")
        self._git(repo, "commit", "-qm", "init atb")
        return repo

    def _new_and_start(self, repo: Path, title: str) -> tuple[str, Path, str]:
        tid = Path(
            self._atb(repo, "new", title, "--agent", "gemini").stdout.strip()
        ).stem
        start = self._atb(repo, "start", tid, "--json")
        self.assertEqual(start.returncode, 0, start.stderr)
        data = json.loads(start.stdout)
        return tid, Path(data["worktree"]), data["branch"]

    def _by_name(self, payload: dict) -> dict[str, dict]:
        return {c["name"]: c for c in payload["checks"]}

    def test_doctor_all_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._setup(Path(tmp))
            tid, _wt, _br = self._new_and_start(repo, "Healthy task")

            res = self._atb(repo, "doctor", "--json")
            self.assertEqual(res.returncode, 0, res.stderr)
            payload = json.loads(res.stdout)
            self.assertEqual(payload["status"], "OK")
            by_name = self._by_name(payload)
            for key in ("git", "config", "sheets", "active", "leftovers", "orphans"):
                self.assertEqual(by_name[key]["status"], "OK", by_name[key])

            # Human-readable output lists every check
            text = self._atb(repo, "doctor")
            self.assertEqual(text.returncode, 0)
            self.assertIn("OK", text.stdout)
            self.assertNotIn("FAIL", text.stdout)

    def test_doctor_old_git_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._setup(Path(tmp))
            with mock.patch("agent_taskboard.config.git_version", return_value=(2, 30)):
                payload, exit_code = doctor.run_doctor(repo)
            self.assertEqual(exit_code, 1)
            by_name = self._by_name(payload)
            self.assertEqual(by_name["git"]["status"], "FAIL")
            self.assertIn("2.30", by_name["git"]["message"])

    def test_doctor_config_errors_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._setup(Path(tmp))
            cfg_file = repo / ".taskboard" / "config.toml"
            cfg_file.write_text('target = "missing-branch"\n', encoding="utf-8")

            res = self._atb(repo, "doctor", "--json")
            self.assertEqual(res.returncode, 1)
            by_name = self._by_name(json.loads(res.stdout))
            self.assertEqual(by_name["config"]["status"], "FAIL")
            self.assertIn("missing-branch", by_name["config"]["message"])

            # Invalid TOML fails config check cleanly without traceback
            cfg_file.write_text("target = [\n", encoding="utf-8")
            res2 = self._atb(repo, "doctor", "--json")
            self.assertEqual(res2.returncode, 1)
            by_name2 = self._by_name(json.loads(res2.stdout))
            self.assertEqual(by_name2["config"]["status"], "FAIL")

    def test_doctor_unwritable_worktree_root_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._setup(Path(tmp))
            with mock.patch("agent_taskboard.doctor.os.access", return_value=False):
                payload, exit_code = doctor.run_doctor(repo)
            self.assertEqual(exit_code, 1)
            cfg_chk = self._by_name(payload)["config"]
            self.assertEqual(cfg_chk["status"], "FAIL")
            self.assertIn("not writable", cfg_chk["message"])

    def test_doctor_sheets_bad_toml_mismatch_and_missing_depends_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._setup(Path(tmp))
            tasks_dir = repo / ".taskboard" / "tasks"
            (tasks_dir / "T001.md").write_text("+++\nbad toml =\n+++\n", encoding="utf-8")

            s2 = sheet_mod.Sheet(id="T999", title="Wrong ID", agent="gemini", status="todo", depends=["T888"])
            (tasks_dir / "T002.md").write_text(sheet_mod.dump(s2), encoding="utf-8")

            res = self._atb(repo, "doctor", "--json")
            self.assertEqual(res.returncode, 1)
            sheets_chk = self._by_name(json.loads(res.stdout))["sheets"]
            self.assertEqual(sheets_chk["status"], "FAIL")
            self.assertIn("T001.md", sheets_chk["message"])
            self.assertIn("T002.md", sheets_chk["message"])
            self.assertIn("T888", sheets_chk["message"])

    def test_doctor_reports_non_utf8_sheet_instead_of_crashing(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._setup(Path(tmp))
            (repo / ".taskboard" / "tasks" / "T001.md").write_bytes(b"+++\nid = \"\xff\xfe\"\n+++\n")
            res = self._atb(repo, "doctor", "--json")
            self.assertEqual(res.returncode, 1, res.stderr)
            self.assertNotIn("Traceback", res.stderr)
            sheets_chk = self._by_name(json.loads(res.stdout))["sheets"]
            self.assertEqual(sheets_chk["status"], "FAIL")
            self.assertIn("T001.md", sheets_chk["message"])

    def test_doctor_active_missing_worktree_and_wrong_branch_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            t1, wt1, _br1 = self._new_and_start(repo, "Missing wt")
            t2, wt2, _br2 = self._new_and_start(repo, "Wrong branch")

            shutil.rmtree(wt1)
            self._git(wt2, "checkout", "-qb", "other-branch")

            res = self._atb(repo, "doctor", "--json")
            self.assertEqual(res.returncode, 1)
            active_chk = self._by_name(json.loads(res.stdout))["active"]
            self.assertEqual(active_chk["status"], "FAIL")
            self.assertIn(t1, active_chk["message"])
            self.assertIn(t2, active_chk["message"])
            self.assertIn("other-branch", active_chk["message"])

    def test_doctor_gc_leftovers_and_orphan_worktrees_warn_exit_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            # T001 done with leftover worktree + branch -> WARN leftovers
            t1, _wt1, _br1 = self._new_and_start(repo, "Done leftover")
            sheet_path = repo / ".taskboard" / "tasks" / f"{t1}.md"
            s = sheet_mod.parse(sheet_path.read_text(encoding="utf-8"))
            s.status = "done"
            sheet_path.write_text(sheet_mod.dump(s), encoding="utf-8")

            # Add an orphan worktree registered by git under worktree_root
            orphan_wt = base / "proj-tasks" / "ORPHAN"
            self._git(repo, "worktree", "add", "-b", "orphan-br", str(orphan_wt), "HEAD")

            res = self._atb(repo, "doctor", "--json")
            self.assertEqual(res.returncode, 0, res.stderr)
            payload = json.loads(res.stdout)
            self.assertEqual(payload["status"], "WARN")
            by_name = self._by_name(payload)
            self.assertEqual(by_name["leftovers"]["status"], "WARN")
            self.assertIn(t1, by_name["leftovers"]["message"])
            self.assertIn("atb gc", by_name["leftovers"]["hint"])
            self.assertEqual(by_name["orphans"]["status"], "WARN")
            self.assertIn("ORPHAN", by_name["orphans"]["message"])


if __name__ == "__main__":
    unittest.main()
