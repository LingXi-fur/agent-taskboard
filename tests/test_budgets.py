"""Tests for per-agent token budgets (config, doctor, stats, API)."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_taskboard import budgets, config
from agent_taskboard.adapters.base import Session

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

UTC = timezone.utc
SHANGHAI = timezone(timedelta(hours=8))


def _session(adapter: str, last: datetime, total: int | None) -> Session:
    usage = None if total is None else {"input": total, "total": total}
    return Session(
        adapter=adapter, id=f"{adapter}-1", last_active_at=last, usage=usage,
    )


def _cfg(tmp: Path, body: str) -> config.Config:
    repo = tmp / "repo"
    (repo / ".taskboard").mkdir(parents=True, exist_ok=True)
    (repo / ".taskboard" / "config.toml").write_text(body, encoding="utf-8")
    return config.load_config(repo)


class BudgetConfigTests(unittest.TestCase):
    def test_both_keys_optional_and_positive(self):
        parsed, errors = config.parse_budgets({
            "opencode": {"tokens_per_day": 10},
            "codex": {"tokens_per_week": 20},
            "gemini-cli": {},
        })
        self.assertEqual(errors, [])
        self.assertEqual(parsed["opencode"].tokens_per_day, 10)
        self.assertIsNone(parsed["opencode"].tokens_per_week)
        self.assertEqual(parsed["codex"].tokens_per_week, 20)
        self.assertNotIn("gemini-cli", parsed)  # empty table = no budget

    def test_unknown_key_and_bad_values_are_reported(self):
        parsed, errors = config.parse_budgets({
            "opencode": {
                "tokens_per_day": 0,
                "tokens_per_week": -5,
                "usd": 3,
            },
            "codex": {"tokens_per_day": "many"},
            "broken": 7,
        })
        self.assertEqual(parsed, {})
        joined = " | ".join(errors)
        self.assertIn("tokens_per_day", joined)
        self.assertIn("positive integer", joined)
        self.assertIn("usd", joined)
        self.assertIn("codex", joined)
        self.assertIn("broken", joined)

    def test_no_budgets_is_empty(self):
        self.assertEqual(config.parse_budgets(None), ({}, []))


class DoctorBudgetTests(unittest.TestCase):
    def _env(self, base: Path) -> dict[str, str]:
        env = os.environ.copy()
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        env["GIT_CONFIG_SYSTEM"] = os.devnull
        env["GIT_CONFIG_GLOBAL"] = os.devnull
        env["HOME"] = str(base)
        env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
        return env

    def _repo(self, base: Path, config_body: str) -> Path:
        repo = base / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo,
                       env=self._env(base), check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=repo,
                       env=self._env(base), check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo,
                       env=self._env(base), check=True)
        (repo / "src").mkdir()
        (repo / "src" / "app.py").write_text("x\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=repo, env=self._env(base),
                       check=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=repo,
                       env=self._env(base), check=True)
        (repo / ".taskboard" / "tasks").mkdir(parents=True)
        (repo / ".taskboard" / "config.toml").write_text(
            config_body, encoding="utf-8"
        )
        (repo / ".gitignore").write_text(
            ".taskboard/runs/\n.taskboard/notes/\n", encoding="utf-8"
        )
        return repo

    def _doctor(self, repo: Path, base: Path):
        return subprocess.run(
            [sys.executable, "-m", "agent_taskboard", "doctor", "--json"],
            cwd=str(repo), env=self._env(base), capture_output=True,
            text=True, encoding="utf-8", errors="replace",
        )

    def test_invalid_budget_fails_doctor(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._repo(base, (
                'worktree_root = "../wts"\n'
                "[budgets.opencode]\n"
                "tokens_per_day = 0\n"
                "usd = 5\n"
            ))
            result = self._doctor(repo, base)
            self.assertEqual(result.returncode, 1, result.stdout)
            payload = json.loads(result.stdout)
            by_name = {item["name"]: item for item in payload["checks"]}
            self.assertEqual(by_name["budgets"]["status"], "FAIL")
            self.assertIn("tokens_per_day", by_name["budgets"]["message"])
            self.assertIn("usd", by_name["budgets"]["message"])

    def test_valid_budget_is_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._repo(base, (
                'worktree_root = "../wts"\n'
                "[budgets.opencode]\n"
                "tokens_per_day = 1000\n"
            ))
            result = self._doctor(repo, base)
            self.assertEqual(result.returncode, 0, result.stdout)
            payload = json.loads(result.stdout)
            by_name = {item["name"]: item for item in payload["checks"]}
            self.assertEqual(by_name["budgets"]["status"], "OK")
            self.assertIn("opencode", by_name["budgets"]["message"])

    def test_no_budget_is_just_info(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._repo(base, 'worktree_root = "../wts"\n')
            result = self._doctor(repo, base)
            payload = json.loads(result.stdout)
            by_name = {item["name"]: item for item in payload["checks"]}
            self.assertEqual(by_name["budgets"]["status"], "INFO")
            self.assertEqual(payload["status"], "OK")


class BudgetComputeTests(unittest.TestCase):
    def _cfg(self, tmp: Path) -> config.Config:
        return _cfg(tmp, (
            'worktree_root = "../wts"\n'
            "[budgets.opencode]\n"
            "tokens_per_day = 1000\n"
            "tokens_per_week = 5000\n"
        ))

    def test_timezone_boundary_changes_today_and_week(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._cfg(Path(tmp))
            now = datetime(2026, 11, 23, 1, 0, tzinfo=UTC)  # Monday 01:00 UTC
            # Sunday 20:00 UTC = Monday 04:00 in Shanghai
            session = _session("opencode", datetime(2026, 11, 22, 20, 0,
                                                    tzinfo=UTC), 100)
            utc_entry = budgets.compute(cfg, now=now, tz=UTC,
                                        collected=[session])[0]
            self.assertIsNone(utc_entry["used_today"])   # Sunday in UTC
            self.assertIsNone(utc_entry["used_week"])    # previous week
            self.assertEqual(utc_entry["status"], "no_data")
            self.assertEqual(utc_entry["day_status"], "no_data")

            sh_entry = budgets.compute(cfg, now=now, tz=SHANGHAI,
                                       collected=[session])[0]
            self.assertEqual(sh_entry["used_today"], 100)  # Monday there
            self.assertEqual(sh_entry["used_week"], 100)
            self.assertEqual(sh_entry["day_ratio"], 0.1)

    def test_week_starts_on_monday(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._cfg(Path(tmp))
            now = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)  # Wednesday
            friday = _session("opencode",
                              datetime(2026, 11, 20, 10, 0, tzinfo=UTC), 300)
            monday = _session("opencode",
                              datetime(2026, 11, 23, 10, 0, tzinfo=UTC), 200)
            entry = budgets.compute(cfg, now=now, tz=UTC,
                                    collected=[friday, monday])[0]
            self.assertEqual(entry["used_week"], 200)   # Friday excluded
            self.assertIsNone(entry["used_today"])      # both older than today
            self.assertEqual(entry["week_ratio"], 0.04)

    def test_thresholds_80_and_100(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._cfg(Path(tmp))
            now = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
            noon = datetime(2026, 11, 25, 9, 0, tzinfo=UTC)

            ok = budgets.compute(cfg, now=now, tz=UTC,
                                 collected=[_session("opencode", noon, 799)])[0]
            self.assertEqual(ok["day_status"], "ok")
            self.assertEqual(ok["status"], "ok")

            near = budgets.compute(cfg, now=now, tz=UTC,
                                   collected=[_session("opencode", noon, 800)])[0]
            self.assertEqual(near["day_status"], "near")
            self.assertEqual(near["status"], "near")

            over = budgets.compute(cfg, now=now, tz=UTC,
                                   collected=[_session("opencode", noon, 1000)])[0]
            self.assertEqual(over["day_status"], "over")
            self.assertEqual(over["status"], "over")

    def test_no_usage_data_stays_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._cfg(Path(tmp))
            now = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
            no_usage = _session("opencode",
                                datetime(2026, 11, 25, 9, 0, tzinfo=UTC), None)
            entry = budgets.compute(cfg, now=now, tz=UTC,
                                    collected=[no_usage])[0]
            self.assertIsNone(entry["used_today"])
            self.assertIsNone(entry["used_week"])
            self.assertEqual(entry["status"], "no_data")

    def test_unconfigured_agents_are_not_listed(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._cfg(Path(tmp))
            now = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
            other = _session("codex",
                             datetime(2026, 11, 25, 9, 0, tzinfo=UTC), 999)
            entries = budgets.compute(cfg, now=now, tz=UTC, collected=[other])
            self.assertEqual([entry["agent"] for entry in entries], ["opencode"])

    def test_no_budgets_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _cfg(Path(tmp), 'worktree_root = "../wts"\n')
            self.assertEqual(budgets.compute(cfg, collected=[]), [])


class BudgetStatsTests(unittest.TestCase):
    def _env(self, base: Path) -> dict[str, str]:
        env = os.environ.copy()
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        env["GIT_CONFIG_SYSTEM"] = os.devnull
        env["GIT_CONFIG_GLOBAL"] = os.devnull
        env["HOME"] = str(base)
        env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
        return env

    def _setup(self, base: Path, *, budgets_toml: str) -> Path:
        repo = base / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo,
                       env=self._env(base), check=True)
        (repo / ".taskboard" / "tasks").mkdir(parents=True)
        (repo / ".taskboard" / "config.toml").write_text(
            'worktree_root = "../wts"\n' + budgets_toml, encoding="utf-8"
        )
        (repo / ".gitignore").write_text(
            ".taskboard/runs/\n.taskboard/notes/\n", encoding="utf-8"
        )
        return repo

    def _claude_home(self, base: Path, tokens: int) -> Path:
        root = base / "claude-home"
        folder = root / "projects" / "demo"
        folder.mkdir(parents=True)
        stamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        line = {
            "type": "assistant", "timestamp": stamp, "cwd": "/demo",
            "sessionId": "b1", "requestId": "r1",
            "message": {
                "role": "assistant", "id": "m1",
                "model": "claude-opus-5-5",
                "content": [{"type": "text", "text": "x"}],
                "usage": {"input_tokens": tokens, "output_tokens": 0},
            },
        }
        (folder / "b1.jsonl").write_text(json.dumps(line) + "\n", encoding="utf-8")
        return root

    def test_stats_shows_budget_section_and_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base, budgets_toml=(
                "[budgets.claude-code]\n"
                "tokens_per_day = 1000\n"
                "tokens_per_week = 10000\n"
            ))
            claude = self._claude_home(base, 250)
            env = self._env(base)
            env["ATB_CLAUDE_HOME"] = str(claude)
            for key in ("ATB_CODEX_HOME", "ATB_GEMINI_HOME", "ATB_OPENCODE_HOME"):
                env[key] = str(base / f"empty-{key}")

            result = subprocess.run(
                [sys.executable, "-m", "agent_taskboard", "stats", "--json"],
                cwd=str(repo), env=env, capture_output=True, text=True,
                encoding="utf-8", errors="replace",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(len(payload["budgets"]), 1)
            entry = payload["budgets"][0]
            self.assertEqual(entry["agent"], "claude-code")
            self.assertEqual(entry["used_today"], 250)
            self.assertEqual(entry["day_ratio"], 0.25)
            self.assertEqual(entry["status"], "ok")

            text = subprocess.run(
                [sys.executable, "-m", "agent_taskboard", "stats"],
                cwd=str(repo), env=env, capture_output=True, text=True,
                encoding="utf-8", errors="replace",
            )
            self.assertIn("## 预算（token）", text.stdout)
            self.assertIn("25%", text.stdout)
            self.assertIn("✓ 正常", text.stdout)

            en = subprocess.run(
                [sys.executable, "-m", "agent_taskboard", "stats", "--lang", "en"],
                cwd=str(repo), env=env, capture_output=True, text=True,
                encoding="utf-8", errors="replace",
            )
            self.assertIn("## Token budgets", en.stdout)
            self.assertIn("✓ ok", en.stdout)

    def test_stats_without_budgets_has_no_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base, budgets_toml="")
            env = self._env(base)
            for key in ("ATB_CLAUDE_HOME", "ATB_CODEX_HOME", "ATB_GEMINI_HOME",
                        "ATB_OPENCODE_HOME"):
                env[key] = str(base / f"empty-{key}")
            result = subprocess.run(
                [sys.executable, "-m", "agent_taskboard", "stats"],
                cwd=str(repo), env=env, capture_output=True, text=True,
                encoding="utf-8", errors="replace",
            )
            self.assertNotIn("预算", result.stdout)
            data = subprocess.run(
                [sys.executable, "-m", "agent_taskboard", "stats", "--json"],
                cwd=str(repo), env=env, capture_output=True, text=True,
                encoding="utf-8", errors="replace",
            )
            self.assertEqual(json.loads(data.stdout)["budgets"], [])


class BudgetOverviewTests(unittest.TestCase):
    def test_overview_exposes_budgets(self):
        from agent_taskboard import serve

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = base / "repo"
            (repo / ".taskboard" / "tasks").mkdir(parents=True)
            (repo / ".taskboard" / "config.toml").write_text(
                'worktree_root = "../wts"\n'
                "[budgets.opencode]\n"
                "tokens_per_day = 100\n",
                encoding="utf-8",
            )
            cfg = config.load_config(repo)
            payload = serve._payload_overview(repo, cfg, [])
            self.assertIn("budgets", payload)
            self.assertEqual(payload["budgets"][0]["agent"], "opencode")
            self.assertEqual(payload["budgets"][0]["tokens_per_day"], 100)
            self.assertEqual(serve._payload_observe_overview()["budgets"], [])


if __name__ == "__main__":
    unittest.main()
