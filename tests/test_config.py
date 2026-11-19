"""Tests for config loading, repo-root discovery and worktree_root resolution."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from agent_taskboard import config


def _git_env(tmp: str) -> dict[str, str]:
    env = os.environ.copy()
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["HOME"] = tmp
    return env


class RepoRootTests(unittest.TestCase):
    def test_find_repo_root_from_nested_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(
                ["git", "init", "-q", tmp], check=True, env=_git_env(tmp)
            )
            nested = Path(tmp) / "a" / "b"
            nested.mkdir(parents=True)
            self.assertEqual(config.find_repo_root(nested), Path(tmp).resolve())

    def test_find_repo_root_errors_outside_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(config.ConfigError):
                config.find_repo_root(tmp)


class LoadConfigTests(unittest.TestCase):
    def test_defaults_when_no_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / ".taskboard").mkdir()
            cfg = config.load_config(repo)
            self.assertEqual(cfg.target, "main")
            self.assertEqual(cfg.id_prefix, "T")
            self.assertEqual(cfg.worktree_root, "../{repo}-tasks")
            self.assertEqual(cfg.test, "python3 -m unittest discover -s tests")

    def test_partial_overrides_and_unknown_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            tb = repo / ".taskboard"
            tb.mkdir()
            (tb / "config.toml").write_text(
                'target = "dev"\ncustom_key = 42\n', encoding="utf-8"
            )
            cfg = config.load_config(repo)
            self.assertEqual(cfg.target, "dev")
            self.assertEqual(cfg.extra, {"custom_key": 42})
            self.assertEqual(cfg.test, "python3 -m unittest discover -s tests")

    def test_wrong_type_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            tb = repo / ".taskboard"
            tb.mkdir()
            (tb / "config.toml").write_text("target = 42\n", encoding="utf-8")
            with self.assertRaises(config.ConfigError):
                config.load_config(repo)

    def test_worktree_root_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "myrepo"
            repo.mkdir()
            (repo / ".taskboard").mkdir()
            cfg = config.load_config(repo)
            expected = (Path(tmp) / "myrepo-tasks").resolve()
            self.assertEqual(config.resolve_worktree_root(cfg), expected)

    def test_worktree_root_absolute_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "myrepo"
            repo.mkdir()
            tb = repo / ".taskboard"
            tb.mkdir()
            (tb / "config.toml").write_text(
                'worktree_root = "/absolute/{repo}"\n', encoding="utf-8"
            )
            cfg = config.load_config(repo)
            # A root-relative path (``/absolute/...``) has no drive on Windows,
            # so resolving it keeps the repository's drive rather than the
            # process's current drive.  Build the expectation the same way.
            expected = (Path(repo.anchor) / "absolute" / "myrepo").resolve()
            self.assertEqual(config.resolve_worktree_root(cfg), expected)


class GitVersionTests(unittest.TestCase):
    def test_parse_git_version(self):
        self.assertEqual(config._parse_git_version("git version 2.38.1"), (2, 38))
        self.assertEqual(config._parse_git_version("git version 2.30.0"), (2, 30))
        self.assertIsNone(config._parse_git_version("no version here"))

    def test_git_version_runs(self):
        version = config.git_version()
        self.assertIsNotNone(version)
        self.assertGreaterEqual(version[0], 2)


if __name__ == "__main__":
    unittest.main()
