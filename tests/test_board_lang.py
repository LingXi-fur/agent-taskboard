"""Tests for ``atb board --html --lang zh|en``."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


class BoardLangTests(unittest.TestCase):
    def _env(self, home: Path, extra: dict[str, str] | None = None) -> dict[str, str]:
        env = os.environ.copy()
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        env["GIT_CONFIG_SYSTEM"] = os.devnull
        env["GIT_CONFIG_GLOBAL"] = os.devnull
        env["HOME"] = str(home)
        env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
        if extra:
            env.update(extra)
        return env

    def _git(
        self, cwd: Path, *args: str, env_extra: dict[str, str] | None = None
    ) -> None:
        subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            env=self._env(cwd, env_extra),
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
        (repo / "src" / "app.py").write_text("base\n", encoding="utf-8")
        self._git(repo, "add", "-A")
        self._git(repo, "commit", "-qm", "init")
        self.assertEqual(self._atb(repo, "init").returncode, 0)
        self._git(repo, "add", ".taskboard")
        self._git(repo, "commit", "-qm", "init atb")
        return repo

    def _prepare_tasks(self, repo: Path) -> tuple[str, str]:
        # T001: doing and stalled
        old_ts = int(time.time()) - 4 * 3600
        old_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(old_ts))
        t1 = Path(self._atb(repo, "new", "Feature Alpha", "--agent", "gemini", "--allow", "**").stdout.strip()).stem
        wt1 = Path(json.loads(self._atb(repo, "start", t1, "--json").stdout)["worktree"])
        (wt1 / "src" / "x.py").write_text("x\n", encoding="utf-8")
        self._git(wt1, "add", "-A")
        self._git(
            wt1,
            "commit",
            "-qm",
            "old work",
            env_extra={"GIT_AUTHOR_DATE": old_iso, "GIT_COMMITTER_DATE": old_iso},
        )
        os.utime(wt1 / ".git", (old_ts, old_ts))

        # T002: doing with missing branch/worktree so "missing" / "缺失" renders
        t2 = Path(self._atb(repo, "new", "Beta Missing", "--agent", "opencode", "--allow", "**").stdout.strip()).stem
        start2 = json.loads(self._atb(repo, "start", t2, "--json").stdout)
        self._git(repo, "worktree", "remove", start2["worktree"])
        self._git(repo, "branch", "-D", start2["branch"])
        return t1, t2

    def test_board_zh_default_and_en_no_cjk(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            self._prepare_tasks(repo)

            zh_out = base / "board-zh.html"
            res_zh = self._atb(repo, "board", "--html", str(zh_out), "--stall", "30")
            self.assertEqual(res_zh.returncode, 0, res_zh.stderr)
            zh_html = zh_out.read_text(encoding="utf-8")

            self.assertIn('<html lang="zh-CN">', zh_html)
            for expected in (
                "<th>状态</th>",
                "<th>分支</th>",
                "<th>未提交</th>",
                "<th>空闲</th>",
                "<th>测试</th>",
                "可能卡住",
                "生成于 ",
                "真实冲突",
                "同文件（可自动合并）",
                "基线漂移",
                '<span class="badge doing">进行中</span>',
                '<td class="missing">缺失</td>',
                '<p class="muted">无</p>',
            ):
                self.assertIn(expected, zh_html)
            for forbidden_ui in (
                "<th>Status</th>",
                "<th>Dirty</th>",
                "<th>Idle</th>",
                "Real conflicts",
                "Baseline drift",
                "possibly stalled",
                '<p class="muted">none</p>',
            ):
                self.assertNotIn(forbidden_ui, zh_html)

            # Chinese UI shell (excluding <style>, <tbody> task data, and <ul> conflict items) allows only ID/Agent/atb
            zh_ui = re.sub(r"<style>.*?</style>", "", zh_html, flags=re.S)
            zh_ui = re.sub(r"<tbody>.*?</tbody>", "", zh_ui, flags=re.S)
            zh_ui = re.sub(r"<ul>.*?</ul>", "", zh_ui, flags=re.S)
            zh_ui_text = re.sub(r"<[^>]+>", " ", zh_ui)
            ascii_words = set(re.findall(r"[A-Za-z]+", zh_ui_text))
            self.assertTrue(
                ascii_words <= {"atb", "ID", "Agent"},
                f"unexpected English UI words in zh board: {ascii_words}",
            )

            en_out = base / "board-en.html"
            res_en = self._atb(
                repo, "board", "--html", str(en_out), "--stall", "30", "--lang", "en"
            )
            self.assertEqual(res_en.returncode, 0, res_en.stderr)
            en_html = en_out.read_text(encoding="utf-8")

            self.assertIn('<html lang="en">', en_html)
            self.assertIsNone(re.search(r"[\u4e00-\u9fff]", en_html), en_html)
            for expected in (
                "<th>Status</th>",
                "<th>Dirty</th>",
                "<th>Idle</th>",
                "possibly stalled",
                "Possibly stalled: 1",
                "Generated at ",
                "Real conflicts",
                "Same files (auto-mergeable)",
                "Baseline drift",
                '<span class="badge doing">doing</span>',
                '<td class="missing">missing</td>',
                '<p class="muted">none</p>',
            ):
                self.assertIn(expected, en_html)


if __name__ == "__main__":
    unittest.main()
