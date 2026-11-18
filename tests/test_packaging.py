"""PyPI packaging metadata and MANIFEST checks (no network or slow build)."""

from __future__ import annotations

import tomllib
import unittest
from pathlib import Path

from agent_taskboard import __version__

ROOT = Path(__file__).resolve().parents[1]


class PackagingMetadataTests(unittest.TestCase):
    def test_pyproject_metadata_fields_complete_and_valid(self):
        data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        project = data["project"]

        self.assertEqual(project["name"], "agent-taskboard")
        self.assertEqual(project["version"], __version__)
        self.assertTrue(project["description"].strip())
        self.assertEqual(project["readme"], "README.md")
        self.assertTrue((ROOT / project["readme"]).is_file())
        self.assertEqual(project["requires-python"], ">=3.11")
        self.assertEqual(project["dependencies"], [])
        self.assertIn("license", project)
        self.assertTrue(project.get("authors"))
        self.assertTrue(project.get("keywords"))
        self.assertTrue(project.get("classifiers"))
        self.assertIn("License :: OSI Approved :: MIT License", project["classifiers"])

        scripts = project.get("scripts", {})
        self.assertEqual(scripts.get("atb"), "agent_taskboard.cli:main")

        find_cfg = data.get("tool", {}).get("setuptools", {}).get("packages", {}).get("find", {})
        self.assertEqual(find_cfg.get("where"), ["src"])

    def test_manifest_includes_core_files_and_prunes_repo_artifacts(self):
        manifest_path = ROOT / "MANIFEST.in"
        self.assertTrue(manifest_path.is_file())
        lines = {
            line.strip()
            for line in manifest_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        }
        self.assertIn("include LICENSE", lines)
        self.assertIn("include README.md", lines)
        self.assertIn("recursive-include src *.py", lines)
        self.assertIn("prune tests", lines)
        self.assertIn("prune .taskboard", lines)
        self.assertIn("prune examples", lines)


if __name__ == "__main__":
    unittest.main()
