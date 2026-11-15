"""Tests for task-sheet parsing, serialization, ids, slugs and templates."""

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from agent_taskboard import sheet

# Repository root: this file lives at <repo>/tests/test_sheet.py.
ROOT = Path(__file__).resolve().parents[1]


class ParseTests(unittest.TestCase):
    def test_parse_minimal(self):
        text = '+++\nid = "T001"\ntitle = "x"\nstatus = "todo"\n+++\n\nbody here\n'
        parsed = sheet.parse(text)
        self.assertEqual(parsed.id, "T001")
        self.assertEqual(parsed.title, "x")
        self.assertEqual(parsed.status, "todo")
        self.assertEqual(parsed.body, "\nbody here\n")

    def test_missing_id(self):
        with self.assertRaises(sheet.SheetError):
            sheet.parse('+++\ntitle = "x"\nstatus = "todo"\n+++\n')

    def test_missing_status(self):
        with self.assertRaises(sheet.SheetError):
            sheet.parse('+++\nid = "T001"\ntitle = "x"\n+++\n')

    def test_invalid_status(self):
        with self.assertRaises(sheet.SheetError):
            sheet.parse('+++\nid = "T001"\ntitle = "x"\nstatus = "bogus"\n+++\n')

    def test_list_fields_default_empty(self):
        parsed = sheet.parse('+++\nid = "T1"\ntitle = "x"\nstatus = "todo"\n+++\n')
        self.assertEqual(parsed.allow, [])
        self.assertEqual(parsed.deny, [])
        self.assertEqual(parsed.depends, [])

    def test_created_as_string(self):
        parsed = sheet.parse(
            '+++\nid="T1"\ntitle="x"\nstatus="todo"\ncreated="2026-01-01T00:00:00Z"\n+++\n'
        )
        self.assertEqual(parsed.created, "2026-01-01T00:00:00Z")

    def test_created_as_datetime(self):
        parsed = sheet.parse(
            '+++\nid="T1"\ntitle="x"\nstatus="todo"\ncreated=2026-01-01T00:00:00Z\n+++\n'
        )
        self.assertIsInstance(parsed.created, datetime)
        self.assertEqual(parsed.created.tzinfo, timezone.utc)

    def test_unknown_field_preserved(self):
        parsed = sheet.parse(
            '+++\nid="T1"\ntitle="x"\nstatus="todo"\npriority=7\n+++\n'
        )
        self.assertEqual(parsed.extra, {"priority": 7})


class LineEndingAndBomTests(unittest.TestCase):
    """T001 review fixes: CRLF, BOM and unknown-field write-back."""

    def test_parse_crlf(self):
        text = (
            '+++\r\nid = "T1"\r\ntitle = "x"\r\nstatus = "todo"\r\n+++\r\n'
            '\r\nbody line 1\r\nbody line 2\r\n'
        )
        parsed = sheet.parse(text)
        self.assertEqual(parsed.id, "T1")
        self.assertEqual(parsed.title, "x")
        self.assertEqual(parsed.status, "todo")
        self.assertEqual(parsed.body, "\nbody line 1\nbody line 2\n")
        # round-trip stable (dump always emits LF)
        self.assertEqual(sheet.parse(sheet.dump(parsed)), parsed)

    def test_parse_crlf_roundtrip(self):
        text = (
            '+++\r\nid = "T2"\r\ntitle = "t"\r\nstatus = "todo"\r\n'
            'allow = ["a.py"]\r\n+++\r\n\r\n## 正文\r\n'
        )
        self.assertEqual(sheet.parse(sheet.dump(sheet.parse(text))), sheet.parse(text))

    def test_dump_uses_lf_only(self):
        original = sheet.Sheet(id="T3", title="t", status="todo", body="\nbody\n")
        self.assertNotIn("\r", sheet.dump(original))
        self.assertIn("\n", sheet.dump(original))

    def test_parse_bom(self):
        text = '\ufeff+++\nid = "T4"\ntitle = "x"\nstatus = "todo"\n+++\n\nbody\n'
        parsed = sheet.parse(text)
        self.assertEqual(parsed.id, "T4")
        self.assertEqual(parsed.body, "\nbody\n")

    def test_bom_roundtrip(self):
        text = '\ufeff+++\nid = "T5"\ntitle = "x"\nstatus = "todo"\n+++\n\nbody\n'
        self.assertEqual(sheet.parse(sheet.dump(sheet.parse(text))), sheet.parse(text))


class UnknownFieldWriteBackTests(unittest.TestCase):
    """Quoted keys and nested dict values survive parse/dump round trips."""

    def _roundtrip(self, text):
        return sheet.parse(sheet.dump(sheet.parse(text)))

    def test_quoted_key_roundtrip(self):
        text = '+++\nid="T1"\ntitle="x"\nstatus="todo"\n"needs quote" = 1\n+++\n'
        self.assertEqual(self._roundtrip(text), sheet.parse(text))

    def test_quoted_key_output_is_quoted(self):
        original = sheet.Sheet(id="T1", title="x", status="todo", extra={"needs quote": 1})
        dumped = sheet.dump(original)
        self.assertIn('"needs quote" = 1', dumped)

    def test_inline_table_roundtrip(self):
        text = (
            '+++\nid="T1"\ntitle="x"\nstatus="todo"\n'
            'meta = { owner = "alice", priority = 3 }\n+++\n'
        )
        self.assertEqual(self._roundtrip(text), sheet.parse(text))

    def test_table_section_roundtrip(self):
        text = (
            '+++\nid="T1"\ntitle="x"\nstatus="todo"\n'
            '[meta]\nowner = "alice"\npriority = 3\n+++\n'
        )
        self.assertEqual(self._roundtrip(text), sheet.parse(text))

    def test_nested_dict_roundtrip(self):
        text = (
            '+++\nid="T1"\ntitle="x"\nstatus="todo"\n'
            'meta = { owner = { name = "alice", id = 7 } }\n+++\n'
        )
        self.assertEqual(self._roundtrip(text), sheet.parse(text))

    def test_dict_in_list_roundtrip(self):
        text = (
            '+++\nid="T1"\ntitle="x"\nstatus="todo"\n'
            'points = [{ x = 1 }, { x = 2 }]\n+++\n'
        )
        self.assertEqual(self._roundtrip(text), sheet.parse(text))


class RoundTripTests(unittest.TestCase):
    def _roundtrip(self, text):
        return sheet.parse(sheet.dump(sheet.parse(text)))

    def test_real_t001_sheet(self):
        """Parse the repo's own T001.md and assert id/allow/deny + round-trip."""
        text = (ROOT / ".taskboard" / "tasks" / "T001.md").read_text(encoding="utf-8")
        parsed = sheet.parse(text)
        self.assertEqual(parsed.id, "T001")
        self.assertEqual(
            parsed.allow, ["src/agent_taskboard/**", "tests/**", "README.md"]
        )
        self.assertEqual(
            parsed.deny,
            ["DESIGN.md", "pyproject.toml", "LICENSE", ".taskboard/**"],
        )
        self.assertEqual(parsed.depends, [])
        self.assertEqual(parsed.status, "done")
        self.assertEqual(
            sheet.parse(sheet.dump(parsed)),
            parsed,
        )

    def test_roundtrip_special_strings(self):
        titles = [
            "plain title",
            '含"引号"',
            "反斜杠\\路径",
            "换行\n第二行",
            "制表\t符",
            "Unicode: 中文, émoji 😀, 日本語",
            'mix "quote" and \\backslash\nand\nnewlines',
        ]
        for title in titles:
            with self.subTest(title=title):
                original = sheet.Sheet(
                    id="T7",
                    title=title,
                    status="todo",
                    allow=["a b/c.py", "中文/文件.py"],
                    deny=["*.bak"],
                    body="第一行\n\n第二行\n含中文和 emoji 😀",
                )
                restored = sheet.parse(sheet.dump(original))
                self.assertEqual(restored, original)
                self.assertEqual(restored.title, title)

    def test_roundtrip_multiline_body(self):
        body = "\n## 背景\n\n多行\n正文\n\n## 验收\n\n- [ ] x\n"
        original = sheet.Sheet(id="T8", title="t", status="doing", body=body)
        self.assertEqual(sheet.parse(sheet.dump(original)), original)

    def test_dump_fixed_field_order(self):
        original = sheet.Sheet(id="T9", title="t", status="todo")
        dumped = sheet.dump(original)
        self.assertTrue(dumped.startswith('+++\nid = "T9"\ntitle = "t"\n'))
        self.assertIn('\nstatus = "todo"\n', dumped)


class SlugifyTests(unittest.TestCase):
    def test_lowercase_and_spaces(self):
        self.assertEqual(sheet.slugify("Doctor covers OpenCode"), "doctor-covers-opencode")

    def test_chinese_falls_back_to_id(self):
        self.assertEqual(sheet.slugify("骨架", "T003"), "t003")

    def test_max_length(self):
        self.assertEqual(sheet.slugify("a" * 100), "a" * 40)

    def test_collapse_and_strip_dashes(self):
        self.assertEqual(sheet.slugify("  Hello--World  "), "hello-world")

    def test_empty_title_uses_fallback(self):
        self.assertEqual(sheet.slugify("！？", "T002"), "t002")


class NextIdTests(unittest.TestCase):
    def test_first_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(sheet.next_id(tmp, "T"), "T001")

    def test_skips_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("T001.md", "T002.md", "T004.md"):
                (Path(tmp) / name).write_text("x", encoding="utf-8")
            self.assertEqual(sheet.next_id(tmp, "T"), "T003")


class TemplateTests(unittest.TestCase):
    def test_template_has_three_sections_and_two_checkboxes(self):
        body = sheet.TEMPLATE_BODY
        self.assertIn("## 背景", body)
        self.assertIn("## 要做的事", body)
        self.assertIn("## 验收", body)
        self.assertEqual(body.count("- [ ]"), 2)


class WindowFieldTests(unittest.TestCase):
    def test_window_defaults_empty_and_roundtrips(self):
        parsed = sheet.parse(
            '+++\nid="T1"\ntitle="x"\nstatus="todo"\n'
            'window="OpenCode · taskboard 窗口"\n+++\n'
        )
        self.assertEqual(parsed.window, "OpenCode · taskboard 窗口")
        self.assertEqual(sheet.parse(sheet.dump(parsed)), parsed)

    def test_old_sheet_without_window_still_parses(self):
        parsed = sheet.parse('+++\nid="T1"\ntitle="x"\nstatus="todo"\n+++\n')
        self.assertEqual(parsed.window, "")
        # an unset optional window must not be added on dump
        self.assertNotIn("window =", sheet.dump(parsed))


if __name__ == "__main__":
    unittest.main()
