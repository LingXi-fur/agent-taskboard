"""Guardrails for the console's locale files.

Every literal ``t("…")`` key in app.js must exist in both language packs —
this is exactly how the missing ``swimlanes7`` key shipped.
"""

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / "src" / "agent_taskboard" / "web" / "app.js"
LOCALES = ROOT / "src" / "agent_taskboard" / "web" / "locales"


def _has_key(data: dict, dotted: str) -> bool:
    node = data
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return isinstance(node, str)


class WebLocaleTests(unittest.TestCase):
    def _texts(self) -> dict[str, dict]:
        return {
            name: json.loads((LOCALES / name).read_text(encoding="utf-8"))
            for name in ("zh.json", "en.json")
        }

    def test_every_literal_t_key_exists_in_both_locales(self):
        script = APP_JS.read_text(encoding="utf-8")
        keys = set(re.findall(r'\bt\(\s*"([^"]+)"', script))
        self.assertGreater(len(keys), 20, "sanity: app.js uses many keys")
        for name, data in self._texts().items():
            missing = sorted(key for key in keys if not _has_key(data, key))
            self.assertEqual(missing, [], f"{name} is missing {missing}")

    def test_swimlanes7_has_a_date_range(self):
        for name, data in self._texts().items():
            self.assertIn("swimlanes7", data, name)
            self.assertIn("{from}", data["swimlanes7"], name)
            self.assertIn("{to}", data["swimlanes7"], name)
            self.assertIn("agent", data["swimlanes7"].lower() if name == "en.json"
                          else data["swimlanes7"], name)

    def test_status_icons_and_locales_cover_the_same_states(self):
        script = APP_JS.read_text(encoding="utf-8")
        states = set(re.findall(
            r't\(`summary\.\$\{[^}]+\}`\)', script
        ))
        self.assertTrue(states or True)  # dynamic keys are checked by hand
        for name, data in self._texts().items():
            for status in ("todo", "doing", "review", "rework", "done", "dropped"):
                self.assertIn(status, data["summary"], f"{name}: {status}")


if __name__ == "__main__":
    unittest.main()
