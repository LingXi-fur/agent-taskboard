"""Tests for ``done --push`` and unpushed-commit visibility.

Every remote is a local bare repository, so nothing touches the network.
"""

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


class PushTests(unittest.TestCase):
    def _env(self, base: Path) -> dict[str, str]:
        env = os.environ.copy()
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        env["GIT_CONFIG_SYSTEM"] = os.devnull
        env["GIT_CONFIG_GLOBAL"] = os.devnull
        env["HOME"] = str(base)
        env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
        return env

    def _git(self, cwd: Path, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=str(cwd), env=self._env(cwd),
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
        ).stdout

    def _atb(self, repo: Path, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "agent_taskboard", *args],
            cwd=str(repo), env=self._env(repo), capture_output=True, text=True, encoding="utf-8", errors="replace",
        )

    def _setup(
        self, base: Path, *, remote: bool = True, set_upstream: bool = True,
        push_after_done: bool = False,
    ) -> Path:
        repo = base / "repo"
        repo.mkdir()
        self._git(repo, "init", "-q", "-b", "main")
        self._git(repo, "config", "user.name", "t")
        self._git(repo, "config", "user.email", "t@t")
        (repo / "src").mkdir()
        (repo / "src" / "app.py").write_text("line\n", encoding="utf-8")
        self._git(repo, "add", "-A")
        self._git(repo, "commit", "-qm", "base")

        if remote:
            bare = base / "remote.git"
            subprocess.run(
                ["git", "init", "--bare", "-q", "-b", "main", str(bare)],
                env=self._env(base), check=True, capture_output=True,
            )
            self._git(repo, "remote", "add", "origin", str(bare))
            if set_upstream:
                self._git(repo, "push", "-u", "origin", "main")

        (repo / ".taskboard" / "tasks").mkdir(parents=True)
        config = 'worktree_root = "../wts"\n'
        if push_after_done:
            config += "push_after_done = true\n"
        (repo / ".taskboard" / "config.toml").write_text(config, encoding="utf-8")
        return repo

    def _prepared_task(self, base: Path, repo: Path, tid: str = "T001") -> Path:
        sheet = sheet_mod.Sheet(
            id=tid, title="Task", agent="opencode", status="todo",
            allow=["src/**"], test="python3 -c 'pass'",
            body="\n## 验收\n\n- [ ] item\n",
        )
        (repo / ".taskboard" / "tasks" / f"{tid}.md").write_text(
            sheet_mod.dump(sheet), encoding="utf-8"
        )
        self.assertEqual(self._atb(repo, "start", tid).returncode, 0)
        wt = base / "wts" / tid
        (wt / "src" / "app.py").write_text("changed\n", encoding="utf-8")
        self._git(wt, "add", "-A")
        self._git(wt, "commit", "-qm", "work")
        self.assertEqual(self._atb(repo, "review", tid).returncode, 0)
        self.assertEqual(self._atb(repo, "check", tid, "--test").returncode, 0)
        return wt

    def _bare_main(self, base: Path):
        bare = base / "remote.git"
        proc = subprocess.run(
            ["git", "--git-dir", str(bare), "rev-parse", "main"],
            env=self._env(base), capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        return proc.stdout.strip() if proc.returncode == 0 else None

    def _status_of(self, repo: Path, tid: str) -> str:
        return sheet_mod.parse(
            (repo / ".taskboard" / "tasks" / f"{tid}.md").read_text(encoding="utf-8")
        ).status

    # ------------------------------------------------------------------ #

    def test_push_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            self._prepared_task(base, repo)

            result = self._atb(repo, "done", "T001", "--merge", "--push")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(self._bare_main(base), self._git(repo, "rev-parse", "main").strip())
            self.assertEqual(self._status_of(repo, "T001"), "done")

    def test_no_upstream_falls_back_to_origin(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base, set_upstream=False)  # remote exists, no upstream
            self._prepared_task(base, repo)

            result = self._atb(repo, "done", "T001", "--merge", "--push")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(self._bare_main(base), self._git(repo, "rev-parse", "main").strip())

    def test_missing_remote_keeps_merge(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base, remote=False)  # no remote at all
            self._prepared_task(base, repo)
            before = self._git(repo, "rev-parse", "main").strip()

            result = self._atb(repo, "done", "T001", "--merge", "--push")
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("does not exist", result.stderr)
            # the merge itself is kept and the task is re-runnable
            self.assertNotEqual(self._git(repo, "rev-parse", "main").strip(), before)
            self.assertEqual(self._status_of(repo, "T001"), "review")

    def test_push_rejected_keeps_merge_and_hints_rebase(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            # make the bare remote ahead so a plain push is rejected
            other = base / "other"
            subprocess.run(
                ["git", "clone", "-q", str(base / "remote.git"), str(other)],
                env=self._env(base), check=True, capture_output=True,
            )
            self._git(other, "config", "user.name", "t")
            self._git(other, "config", "user.email", "t@t")
            (other / "ahead.txt").write_text("ahead\n", encoding="utf-8")
            self._git(other, "add", "-A")
            self._git(other, "commit", "-qm", "ahead")
            self._git(other, "push", "origin", "main")
            remote_before = self._bare_main(base)

            self._prepared_task(base, repo)
            before = self._git(repo, "rev-parse", "main").strip()

            result = self._atb(repo, "done", "T001", "--merge", "--push")
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("pull --rebase", result.stderr)
            # merge kept, remote untouched (no force)
            self.assertNotEqual(self._git(repo, "rev-parse", "main").strip(), before)
            self.assertEqual(self._bare_main(base), remote_before)

    def test_push_requires_merge(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            self._prepared_task(base, repo)

            result = self._atb(repo, "done", "T001", "--push")
            self.assertEqual(result.returncode, 2, result.stdout)

    def test_config_push_after_done(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base, push_after_done=True)
            self._prepared_task(base, repo)

            result = self._atb(repo, "done", "T001", "--merge")  # no --push
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(self._bare_main(base), self._git(repo, "rev-parse", "main").strip())

    def test_no_push_flag_overrides_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base, push_after_done=True)
            self._prepared_task(base, repo)
            remote_before = self._bare_main(base)

            result = self._atb(repo, "done", "T001", "--merge", "--no-push")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(self._bare_main(base), remote_before)

    def test_no_push_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)  # push_after_done defaults to false
            self._prepared_task(base, repo)
            remote_before = self._bare_main(base)

            result = self._atb(repo, "done", "T001", "--merge")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(self._bare_main(base), remote_before)

    def test_status_shows_unpushed(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            self._prepared_task(base, repo)
            # merge locally but do not push
            self.assertEqual(self._atb(repo, "done", "T001", "--merge").returncode, 0)

            text = self._atb(repo, "status")
            self.assertEqual(text.returncode, 0, text.stderr)
            self.assertIn("commits not pushed", text.stdout)

            data = json.loads(self._atb(repo, "status", "--json").stdout)
            upstream = data["summary"]["upstream"]
            self.assertEqual(upstream["target"], "main")
            self.assertGreaterEqual(upstream["ahead"], 1)

    def test_status_no_upstream(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base, remote=False)
            self._prepared_task(base, repo)

            text = self._atb(repo, "status")
            self.assertIn("no upstream", text.stdout)
            data = json.loads(self._atb(repo, "status", "--json").stdout)
            self.assertIsNone(data["summary"]["upstream"]["upstream"])

    def test_board_shows_upstream(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            self._prepared_task(base, repo)
            self.assertEqual(self._atb(repo, "done", "T001", "--merge").returncode, 0)

            zh_out = base / "board-zh.html"
            self.assertEqual(
                self._atb(repo, "board", "--html", str(zh_out)).returncode, 0
            )
            zh_html = zh_out.read_text(encoding="utf-8")
            self.assertIn('class="board-upstream"', zh_html)
            self.assertIn("上游", zh_html)

            en_out = base / "board-en.html"
            self.assertEqual(
                self._atb(
                    repo, "board", "--html", str(en_out), "--lang", "en"
                ).returncode,
                0,
            )
            en_html = en_out.read_text(encoding="utf-8")
            self.assertIn('class="board-upstream"', en_html)
            self.assertIn("commits not pushed", en_html)

    def test_doctor_warns_on_unpushed(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            self._prepared_task(base, repo)
            self.assertEqual(self._atb(repo, "done", "T001", "--merge").returncode, 0)

            result = self._atb(repo, "doctor", "--json")
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            check = {c["name"]: c for c in payload["checks"]}["upstream"]
            self.assertEqual(check["status"], "WARN")
            self.assertIn("not pushed", check["message"])
            self.assertIn("push", check["hint"])

    def test_doctor_no_upstream_is_not_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base, remote=False)
            self._prepared_task(base, repo)

            result = self._atb(repo, "doctor", "--json")
            payload = json.loads(result.stdout)
            check = {c["name"]: c for c in payload["checks"]}["upstream"]
            self.assertNotEqual(check["status"], "FAIL")
            self.assertIn("no upstream", check["message"])


if __name__ == "__main__":
    unittest.main()
