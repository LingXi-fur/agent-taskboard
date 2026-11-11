"""Table-driven tests for ``scope.match`` and ``scope.classify``."""

import unittest

from agent_taskboard import scope


class MatchTests(unittest.TestCase):
    """Cover ``*``, ``**`` (start/middle/end), ``?``, dotfiles, spaces, CJK."""

    CASES = [
        # exact and simple globs
        ("src/agent_taskboard/cli.py", "src/agent_taskboard/cli.py", True),
        ("src/agent_taskboard/cli.py", "src/agent_taskboard/*.py", True),
        ("README.md", "README.*", True),
        ("README.md", "*.md", True),
        # ``*`` must not cross a directory separator
        ("src/agent_taskboard/cli.py", "src/*.py", False),
        ("docs/README.md", "*.md", False),
        ("tests/sub/test_x.py", "tests/test_*.py", False),
        # ``**`` at the end
        ("src/agent_taskboard/cli.py", "src/**", True),
        ("src/agent_taskboard/cli.py", "src/agent_taskboard/**", True),
        ("a", "a/**", True),          # ** matches zero extra levels
        # ``**`` at the start
        ("src/agent_taskboard/cli.py", "**/cli.py", True),
        ("a/b/c.py", "**/c.py", True),
        ("src/x.py", "**/src/*.py", True),
        # ``**`` in the middle
        ("src/agent_taskboard/cli.py", "src/**/cli.py", True),
        ("a/b/c.py", "a/**/c.py", True),
        ("a/b/c.py", "a/**/*.py", True),
        ("x/y/z.py", "x/**/z.py", True),
        ("tests/sub/test_x.py", "tests/**/test_*.py", True),
        # ``**`` matching everything / nothing
        ("a/b/c.py", "**", True),
        ("a/b/c.py", "other/**", False),
        # ``?`` matches exactly one character
        ("ab.py", "a?.py", True),
        ("a.py", "a?.py", False),
        ("x/a/b/z.py", "x/*/z.py", False),  # * matches exactly one level
        # dotfiles
        (".gitignore", ".gitignore", True),
        (".gitignore", "*", True),
        ("src/.hidden.py", "src/*", True),
        (".env", "**/.env", True),
        # spaces and Chinese characters in paths
        ("路径 空格/文件 名.py", "路径 空格/*.py", True),
        ("路径 空格/文件 名.py", "路径 空格/**", True),
        ("中文/目录/文件.py", "中文/**", True),
        ("中文/目录/文件.py", "**/文件.py", True),
    ]

    def test_match_table(self):
        self.assertGreaterEqual(len(self.CASES), 25)
        for path, pattern, expected in self.CASES:
            with self.subTest(path=path, pattern=pattern):
                self.assertEqual(scope.match(path, pattern), expected)


class ClassifyTests(unittest.TestCase):
    def test_empty_allow_means_ok(self):
        self.assertEqual(
            scope.classify(["a.py", "b.py"], [], []),
            {"a.py": "ok", "b.py": "ok"},
        )

    def test_allow_restricts(self):
        self.assertEqual(
            scope.classify(["src/x.py", "README.md"], ["src/**"], []),
            {"src/x.py": "ok", "README.md": "outside"},
        )

    def test_deny_wins(self):
        self.assertEqual(
            scope.classify(["DESIGN.md", "src/x.py"], ["src/**"], ["DESIGN.md"]),
            {"DESIGN.md": "denied", "src/x.py": "ok"},
        )

    def test_deny_and_allow_both_hit(self):
        self.assertEqual(
            scope.classify(["README.md"], ["README.md"], ["*.md"]),
            {"README.md": "denied"},
        )

    def test_deny_with_empty_allow(self):
        self.assertEqual(
            scope.classify(["README.md"], [], ["*.md"]),
            {"README.md": "denied"},
        )

    def test_no_match_no_allow(self):
        self.assertEqual(
            scope.classify(["x.py"], ["src/**"], []),
            {"x.py": "outside"},
        )


if __name__ == "__main__":
    unittest.main()
