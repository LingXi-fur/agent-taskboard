"""Tests for ``atb new --body`` / ``atb edit`` and ``--copy-to``."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent_taskboard import cli
from agent_taskboard import sheet as sheet_mod
from agent_taskboard.errors import AtbError

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


class SheetBodyTests(unittest.TestCase):
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
            ["git", *args],
            cwd=str(cwd),
            env=self._env(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )

    def _setup(self, base: Path) -> Path:
        repo = base / "repo"
        repo.mkdir()
        self._git(repo, "init", "-q", "-b", "main")
        self._git(repo, "config", "user.name", "t")
        self._git(repo, "config", "user.email", "t@t")
        tasks = repo / ".taskboard" / "tasks"
        tasks.mkdir(parents=True)
        (repo / ".taskboard" / "config.toml").write_text(
            'worktree_root = "../wts"\n', encoding="utf-8"
        )
        return repo

    def _atb(
        self, repo: Path, *args: str, input: str | None = None
    ) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "agent_taskboard", *args],
            cwd=str(repo),
            env=self._env(repo),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            input=input,
        )

    def _sheet_path(self, repo: Path, tid: str = "T001") -> Path:
        return repo / ".taskboard" / "tasks" / f"{tid}.md"

    def _read(self, repo: Path, tid: str = "T001") -> tuple[str, sheet_mod.Sheet]:
        path = self._sheet_path(repo, tid)
        text = path.read_text(encoding="utf-8")
        return text, sheet_mod.parse(text)

    def _write(self, repo: Path, tid: str = "T001", **fields) -> None:
        values = {
            "id": tid,
            "title": "任务",
            "agent": "opencode",
            "status": "todo",
            "body": "\n## 背景\n\n正文\n",
        }
        values.update(fields)
        self._sheet_path(repo, tid).write_text(
            sheet_mod.dump(sheet_mod.Sheet(**values)), encoding="utf-8"
        )

    def _assert_normalized(self, text: str, body: str) -> None:
        self.assertTrue(text.endswith("\n"))
        self.assertFalse(text.endswith("\n\n"))
        after_fence = text.split("\n+++\n", 1)[1]
        self.assertTrue(after_fence.startswith("\n"))
        self.assertFalse(after_fence.startswith("\n\n"))
        self.assertEqual(sheet_mod.parse(text).body, body)

    # ------------------------------------------------------------------ #
    # new --body
    # ------------------------------------------------------------------ #

    def test_new_body_from_file_is_normalized(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            body_file = base / "body.md"
            body_file.write_text(
                "\n \n## 自定义\n\n内容\n \n\n", encoding="utf-8"
            )

            result = self._atb(
                repo, "new", "带正文", "--agent", "opencode",
                "--body", f"@{body_file}",
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            text, parsed = self._read(repo)
            self._assert_normalized(text, "\n## 自定义\n\n内容\n")
            self.assertEqual(parsed.title, "带正文")
            self.assertEqual(parsed.status, "todo")

    def test_new_body_from_stdin(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._setup(Path(tmp))

            result = self._atb(
                repo, "new", "stdin body", "--agent", "opencode",
                "--body", "-", input="\n## 来自 stdin\n\n正文\n\n",
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            text, parsed = self._read(repo)
            self._assert_normalized(text, "\n## 来自 stdin\n\n正文\n")

    def test_new_without_body_keeps_template(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._setup(Path(tmp))

            result = self._atb(repo, "new", "默认模板", "--agent", "opencode")
            self.assertEqual(result.returncode, 0, result.stderr)

            _, parsed = self._read(repo)
            self.assertEqual(parsed.body, sheet_mod.TEMPLATE_BODY)
            self.assertIn("## 背景", parsed.body)
            self.assertIn("## 验收", parsed.body)

    def test_new_missing_body_file_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)

            result = self._atb(
                repo, "new", "x", "--agent", "opencode",
                "--body", f"@{base / 'missing.md'}",
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("not found", result.stderr)
            self.assertFalse(self._sheet_path(repo).exists())

    # ------------------------------------------------------------------ #
    # edit
    # ------------------------------------------------------------------ #

    def test_edit_body_preserves_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            self._write(
                repo, status="doing", base="abc123", branch="opencode/t001"
            )
            body_file = base / "new-body.md"
            body_file.write_text("\n\n## 替换后\n\n新正文\n\n", encoding="utf-8")

            result = self._atb(
                repo, "edit", "T001", "--body", f"@{body_file}"
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            text, parsed = self._read(repo)
            self._assert_normalized(text, "\n## 替换后\n\n新正文\n")
            self.assertEqual(parsed.id, "T001")
            self.assertEqual(parsed.status, "doing")
            self.assertEqual(parsed.base, "abc123")
            self.assertEqual(parsed.branch, "opencode/t001")

    def test_edit_body_from_stdin(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._setup(Path(tmp))
            self._write(repo)

            result = self._atb(
                repo, "edit", "T001", "--body", "-",
                input="## stdin 替换\n\n正文\n",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            _, parsed = self._read(repo)
            self.assertEqual(parsed.body, "\n## stdin 替换\n\n正文\n")

    def test_edit_add_and_remove_allow_deny(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._setup(Path(tmp))
            self._write(repo, allow=["src/**", "tests/**"], deny=["docs/**"])

            result = self._atb(
                repo, "edit", "T001",
                "--add-allow", "docs/**",
                "--remove-allow", "tests/**",
                "--add-deny", "secrets/**",
                "--remove-deny", "docs/**",
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            _, parsed = self._read(repo)
            self.assertEqual(parsed.allow, ["src/**", "docs/**"])
            self.assertEqual(parsed.deny, ["secrets/**"])

    def test_edit_title_and_agent(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._setup(Path(tmp))
            self._write(repo)

            result = self._atb(
                repo, "edit", "T001", "--title", "新标题", "--agent", "claude"
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            _, parsed = self._read(repo)
            self.assertEqual(parsed.title, "新标题")
            self.assertEqual(parsed.agent, "claude")

    def test_edit_done_and_dropped_are_rejected(self):
        for status in ("done", "dropped"):
            with self.subTest(status=status):
                with tempfile.TemporaryDirectory() as tmp:
                    repo = self._setup(Path(tmp))
                    self._write(repo, status=status)
                    before = self._sheet_path(repo).read_text(encoding="utf-8")

                    result = self._atb(
                        repo, "edit", "T001", "--title", "不该写入"
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("cannot be edited", result.stderr)
                    self.assertEqual(
                        self._sheet_path(repo).read_text(encoding="utf-8"),
                        before,
                    )

    def test_edit_missing_body_file_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            self._write(repo)
            before = self._sheet_path(repo).read_text(encoding="utf-8")

            result = self._atb(
                repo, "edit", "T001", "--body", f"@{base / 'missing.md'}"
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(
                self._sheet_path(repo).read_text(encoding="utf-8"), before
            )

    def test_edit_refuses_to_write_when_dump_does_not_parse(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._setup(Path(tmp))
            self._write(repo)
            before = self._sheet_path(repo).read_text(encoding="utf-8")
            args = cli.build_parser().parse_args(
                ["edit", "T001", "--title", "新标题"]
            )

            with mock.patch.object(
                cli.config, "find_repo_root", return_value=repo
            ), mock.patch.object(cli, "dump", return_value="not a sheet\n"):
                with self.assertRaises(AtbError):
                    cli.cmd_edit(args)

            self.assertEqual(
                self._sheet_path(repo).read_text(encoding="utf-8"), before
            )

    def test_edit_without_changes_is_a_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._setup(Path(tmp))
            self._write(repo)
            before = self._sheet_path(repo).read_text(encoding="utf-8")

            result = self._atb(repo, "edit", "T001")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                self._sheet_path(repo).read_text(encoding="utf-8"), before
            )

    # ------------------------------------------------------------------ #
    # --copy-to
    # ------------------------------------------------------------------ #

    def test_new_copy_to(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            editor = base / "editor"
            editor.mkdir()

            result = self._atb(
                repo, "new", "copied", "--agent", "opencode",
                "--copy-to", str(editor),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            copy = editor / "ATB_TASK_T001.md"
            self.assertTrue(copy.is_file())
            self.assertEqual(
                copy.read_text(encoding="utf-8"),
                self._sheet_path(repo).read_text(encoding="utf-8"),
            )

    def test_edit_copy_to_has_updated_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            self._write(repo)
            editor = base / "editor"
            editor.mkdir()

            result = self._atb(
                repo, "edit", "T001", "--title", "更新后",
                "--copy-to", str(editor),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            copy = editor / "ATB_TASK_T001.md"
            self.assertTrue(copy.is_file())
            text = copy.read_text(encoding="utf-8")
            self.assertEqual(sheet_mod.parse(text).title, "更新后")
            self.assertEqual(
                text, self._sheet_path(repo).read_text(encoding="utf-8")
            )

    def test_copy_to_missing_directory_errors_without_creating_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            missing = base / "no-such-dir"

            result = self._atb(
                repo, "new", "x", "--agent", "opencode",
                "--copy-to", str(missing),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("does not exist", result.stderr)
            self.assertFalse(missing.exists())
            self.assertFalse(self._sheet_path(repo).exists())

    def test_edit_copy_to_missing_directory_keeps_sheet(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            self._write(repo)
            before = self._sheet_path(repo).read_text(encoding="utf-8")
            missing = base / "no-such-dir"

            result = self._atb(
                repo, "edit", "T001", "--title", "x",
                "--copy-to", str(missing),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("does not exist", result.stderr)
            self.assertFalse(missing.exists())
            self.assertEqual(
                self._sheet_path(repo).read_text(encoding="utf-8"), before
            )


if __name__ == "__main__":
    unittest.main()
