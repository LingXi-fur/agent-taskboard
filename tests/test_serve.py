"""Tests for ``atb serve``: JSON API v1, static UI, SSE and host safety."""

import http.client
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from agent_taskboard import serve, sheet as sheet_mod

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
FIXTURES = ROOT / "tests" / "fixtures" / "adapters"
PLACEHOLDER = "REPO_WT_PLACEHOLDER"


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class ServeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.base = Path(tempfile.mkdtemp(prefix="atb-serve-"))
        cls.repo = cls._setup(cls.base)
        cls.server = serve.build_server(cls.repo, host="127.0.0.1", port=0, poll=0.2)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _env(base: Path) -> dict[str, str]:
        env = os.environ.copy()
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        env["GIT_CONFIG_SYSTEM"] = os.devnull
        env["GIT_CONFIG_GLOBAL"] = os.devnull
        env["HOME"] = str(base)
        env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
        return env

    @classmethod
    def _git(cls, *args: str, cwd: Path = None) -> None:
        subprocess.run(
            ["git", *args], cwd=str(cwd or cls.repo), env=cls._env(cls.base),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=True,
        )

    @classmethod
    def _atb(cls, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "agent_taskboard", *args],
            cwd=str(cls.repo), env=cls._env(cls.base), capture_output=True,
            text=True, encoding="utf-8", errors="replace",
        )

    @classmethod
    def _setup(cls, base: Path) -> Path:
        repo = base / "repo"
        repo.mkdir()
        cls.repo = repo
        cls._git("init", "-q", "-b", "main", cwd=repo)
        cls._git("config", "user.name", "t", cwd=repo)
        cls._git("config", "user.email", "t@t", cwd=repo)
        (repo / "src").mkdir()
        (repo / "src" / "app.py").write_text("line\n", encoding="utf-8")
        cls._git("add", "-A", cwd=repo)
        cls._git("commit", "-qm", "base", cwd=repo)
        (repo / ".taskboard" / "tasks").mkdir(parents=True)
        (repo / ".taskboard" / "config.toml").write_text(
            'worktree_root = "../wts"\n', encoding="utf-8"
        )

        sheet = sheet_mod.Sheet(
            id="T001", title="Serve task", agent="opencode", status="todo",
            allow=["src/**"], test="python3 -c 'pass'",
            body="\n## 背景\n\nx\n\n## 验收\n\n- [ ] item one\n- [x] item two\n",
        )
        (repo / ".taskboard" / "tasks" / "T001.md").write_text(
            sheet_mod.dump(sheet), encoding="utf-8"
        )
        cls._git("add", ".taskboard", cwd=repo)
        cls._git("commit", "-qm", "add T001", cwd=repo)

        assert cls._atb("start", "T001").returncode == 0
        worktree = base / "wts" / "T001"
        (worktree / "src" / "app.py").write_text("T001\n", encoding="utf-8")
        cls._git("add", "-A", cwd=worktree)
        cls._git("commit", "-qm", "agent work", cwd=worktree)
        cls._git("add", ".taskboard", cwd=repo)
        cls._git("commit", "-qm", "mark doing", cwd=repo)

        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "exit_code": 0, "duration": 0.1,
            "command": "python3 -c 'pass'", "output": "",
        }
        (repo / ".taskboard" / "runs").mkdir(parents=True, exist_ok=True)
        with (repo / ".taskboard" / "runs" / "T001.jsonl").open(
            "a", encoding="utf-8"
        ) as handle:
            handle.write(json.dumps(record) + "\n")

        assert cls._atb("review", "T001").returncode == 0
        cls._git("add", ".taskboard", cwd=repo)
        cls._git("commit", "-qm", "mark review", cwd=repo)
        return repo

    def _get(self, path: str, origin: str | None = None) -> tuple[int, dict | str, str]:
        url = f"http://127.0.0.1:{self.port}{path}"
        request = urllib.request.Request(url)
        if origin is not None:
            request.add_header("Origin", origin)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                body = response.read().decode("utf-8")
                content_type = response.headers.get("Content-Type", "")
                if "json" in content_type:
                    return response.status, json.loads(body), content_type
                return response.status, body, content_type
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8")
            try:
                return error.code, json.loads(body), error.headers.get("Content-Type", "")
            except json.JSONDecodeError:
                return error.code, body, error.headers.get("Content-Type", "")

    # ------------------------------------------------------------------ #
    # static UI
    # ------------------------------------------------------------------ #

    def test_index_html_structure(self):
        status, body, content_type = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", content_type)
        for marker in ('id="summary"', 'id="swimlanes"', 'id="task-rows"',
                       'id="detail"', 'id="theme-toggle"', "/app.js", "/style.css"):
            self.assertIn(marker, body)

    def test_static_assets_and_locales(self):
        status, js, content_type = self._get("/app.js")
        self.assertEqual(status, 200)
        self.assertIn("text/javascript", content_type)
        self.assertIn("EventSource", js)  # live updates without any CDN
        self.assertNotIn("http://", js.replace("http://127.0.0.1", ""))

        status, css, _ = self._get("/style.css")
        self.assertEqual(status, 200)
        self.assertIn("prefers-color-scheme", css)
        self.assertIn('[data-theme="contrast"]', css)

        status, zh, _ = self._get("/locales/zh.json")
        self.assertEqual(status, 200)
        self.assertEqual(zh["taskList"], "任务")
        status, en, _ = self._get("/locales/en.json")
        self.assertEqual(status, 200)
        self.assertEqual(en["taskList"], "Tasks")

    def test_unknown_route_404(self):
        status, payload, _ = self._get("/api/v1/nope")
        self.assertEqual(status, 404)
        self.assertIn("error", payload)

    # ------------------------------------------------------------------ #
    # JSON API v1
    # ------------------------------------------------------------------ #

    def test_overview_structure(self):
        status, payload, _ = self._get("/api/v1/overview")
        self.assertEqual(status, 200)
        self.assertEqual(
            set(payload),
            {
                "mode", "target", "repo_root", "upstream", "summary", "active",
                "awaiting_review", "merged_today", "generated_at",
            },
        )
        self.assertEqual(payload["mode"], "task")
        self.assertEqual(payload["target"], "main")
        self.assertEqual(payload["repo_root"], str(self.repo))
        self.assertEqual(payload["active"], 1)
        self.assertEqual(payload["awaiting_review"], 1)
        self.assertEqual(payload["merged_today"], 0)  # no merge commits today
        self.assertEqual(payload["summary"]["review"], 1)
        self.assertIn("upstream", payload["upstream"])

    def test_tasks_structure(self):
        status, payload, _ = self._get("/api/v1/tasks")
        self.assertEqual(status, 200)
        self.assertIn("tasks", payload)
        self.assertEqual(payload["summary"]["review"], 1)
        row = next(item for item in payload["tasks"] if item["id"] == "T001")
        self.assertEqual(row["agent"], "opencode")
        self.assertEqual(row["status"], "review")
        self.assertIn("ready", row)
        self.assertIn("blockers", row)
        self.assertIn("ahead", row)
        self.assertIn("behind", row)

    def test_task_detail_and_prompt(self):
        status, payload, _ = self._get("/api/v1/tasks/T001")
        self.assertEqual(status, 200)
        for key in (
            "id", "title", "status", "scope", "test", "activity", "diffstat",
            "conflicts", "acceptance", "ready", "blockers", "events",
        ):
            self.assertIn(key, payload)
        self.assertEqual(payload["id"], "T001")
        self.assertEqual(payload["test"]["result"], "ok")
        self.assertEqual(payload["acceptance"][0]["text"], "item one")
        self.assertFalse(payload["acceptance"][0]["checked"])
        self.assertTrue(payload["acceptance"][1]["checked"])
        self.assertTrue(
            any(event["kind"] == "status" for event in payload["events"])
        )

        status, prompt, _ = self._get("/api/v1/tasks/T001/prompt")
        self.assertEqual(status, 200)
        # Windows temp dirs may come back as 8.3 short names (RUNNER~1)
        # while atb reports the long path, so accept either spelling.
        worktree = self.base / "wts" / "T001"
        spellings = {str(worktree), str(worktree.resolve())}
        self.assertTrue(
            any(path in prompt["text"] for path in spellings),
            f"none of {sorted(spellings)} in prompt text",
        )
        self.assertIn("Do NOT push", prompt["text"])

    def test_task_not_found(self):
        status, payload, _ = self._get("/api/v1/tasks/T999")
        self.assertEqual(status, 404)
        self.assertIn("error", payload)

    def test_events_today_and_task_filter(self):
        today = time.strftime("%Y-%m-%d")
        status, payload, _ = self._get(f"/api/v1/events?day={today}")
        self.assertEqual(status, 200)
        kinds = {event["kind"] for event in payload["events"]}
        self.assertIn("status", kinds)
        self.assertIn("test", kinds)
        for event in payload["events"]:
            self.assertIn(event["task"], ("T001",))

        status, filtered, _ = self._get("/api/v1/events?task=T999")
        self.assertEqual(status, 404)

    def test_sessions_endpoint_wired_to_adapters(self):
        # point every adapter at the anonymized fixtures: T001's worktree is
        # the mapped cwd in the fixture stores
        worktree = self.base / "wts" / "T001"
        posix = str(worktree).replace(os.sep, "/")
        homes = {
            "ATB_CLAUDE_HOME": "claude_code",
            "ATB_CODEX_HOME": "codex",
            "ATB_GEMINI_HOME": "gemini_cli",
        }
        try:
            for var, fixture in homes.items():
                target = self.base / f"{fixture}-copy"
                for path in (FIXTURES / fixture).rglob("*"):
                    if path.is_dir():
                        continue
                    destination = target / path.relative_to(FIXTURES / fixture)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_text(
                        path.read_text(encoding="utf-8").replace(PLACEHOLDER, posix),
                        encoding="utf-8",
                    )
                os.environ[var] = str(target)
            # keep the test off this machine's real OpenCode store
            empty = self.base / "empty-oc"
            empty.mkdir(exist_ok=True)
            os.environ["ATB_OPENCODE_HOME"] = str(empty)
            # the fixture sessions are dated 2026-11-19: widen the window
            status, payload, _ = self._get(
                "/api/v1/sessions?since=2026-01-01"
            )
            self.assertEqual(status, 200)
            self.assertGreater(len(payload["sessions"]), 0)
            names = {entry["name"] for entry in payload["adapters"]}
            self.assertIn("claude-code", names)
            self.assertIn("codex", names)
            self.assertIn("gemini-cli", names)
            mapped = [
                row for row in payload["sessions"] if row["task"] == "T001"
            ]
            self.assertGreater(len(mapped), 0)
        finally:
            for var in list(homes) + ["ATB_OPENCODE_HOME"]:
                os.environ.pop(var, None)

    # ------------------------------------------------------------------ #
    # live updates and host safety
    # ------------------------------------------------------------------ #

    def test_sse_stream_pushes_changes(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        connection.request("GET", "/api/v1/stream")
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertIn("text/event-stream", response.headers.get("Content-Type", ""))

        def read_event() -> str:
            collected = b""
            while b"\n\n" not in collected:
                chunk = response.read(1)  # blocks until data or timeout
                if not chunk:
                    raise AssertionError("stream closed before an event")
                collected += chunk
            return collected.decode("utf-8")

        first = read_event()
        self.assertIn("event: refresh", first)

        marker = self.repo / ".taskboard" / "runs" / "T001.jsonl"
        record = {
            "timestamp": "2026-11-20T09:00:00+00:00", "exit_code": 0,
            "duration": 0.2, "command": "python3 -c 'pass'", "output": "",
        }
        with marker.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")

        second = read_event()
        self.assertIn("event: refresh", second)
        connection.close()

    def test_non_local_origin_rejected(self):
        local = f"http://127.0.0.1:{self.port}"
        status, _, _ = self._get("/", origin="http://evil.example")
        self.assertEqual(status, 403)

        status, _, _ = self._get("/", origin=local)
        self.assertEqual(status, 200)
        status, _, _ = self._get("/api/v1/overview", origin=local)
        self.assertEqual(status, 200)
        # no Origin header at all (curl, EventSource same-origin): allowed
        status, _, _ = self._get("/api/v1/overview")
        self.assertEqual(status, 200)

    def _raw_get(
        self, path: str, host: str, origin: str | None = None
    ) -> tuple[int, dict]:
        """Issue a request with a forged Host header (bypasses urllib)."""
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.port, timeout=10
        )
        try:
            connection.putrequest("GET", path, skip_host=True)
            connection.putheader("Host", host)
            if origin is not None:
                connection.putheader("Origin", origin)
            connection.endheaders()
            response = connection.getresponse()
            body = response.read().decode("utf-8")
            return response.status, json.loads(body)
        finally:
            connection.close()

    def test_dns_rebinding_forged_host_is_rejected(self):
        """Host+Origin from an attacker domain that resolves here: 403.

        This is the DNS-rebinding case: the browser sends the attacker's
        hostname in both headers and they agree, so comparing Origin to Host
        alone would let the page read local task data. The Host allow-list
        must reject it.
        """
        status, payload = self._raw_get(
            "/api/v1/tasks",
            host="evil.example:8766",
            origin="http://evil.example:8766",
        )
        self.assertEqual(status, 403)
        self.assertIn("host", payload["error"])

        # requests without Origin are Host-checked too
        status, payload = self._raw_get(
            "/api/v1/tasks", host="evil.example:8766"
        )
        self.assertEqual(status, 403)
        self.assertIn("host", payload["error"])

        # the overview and sessions payloads must be unreachable the same way
        for path in ("/api/v1/overview", "/api/v1/sessions"):
            status, _ = self._raw_get(
                path, host="evil.example:8766",
                origin="http://evil.example:8766",
            )
            self.assertEqual(status, 403, path)

    def test_local_hosts_are_accepted(self):
        for host in (
            f"127.0.0.1:{self.port}",
            f"localhost:{self.port}",
        ):
            status, payload = self._raw_get("/api/v1/overview", host=host)
            self.assertEqual(status, 200, host)
            self.assertEqual(payload["target"], "main")

    def test_allowed_hosts_list(self):
        server = serve.build_server(self.repo, host="0.0.0.0", port=0, poll=5)
        try:
            port = server.server_address[1]
            for name in (
                f"127.0.0.1:{port}",
                f"localhost:{port}",
                f"[::1]:{port}",
                f"0.0.0.0:{port}",
            ):
                self.assertIn(name, server.allowed_hosts)
            self.assertNotIn("evil.example:8766", server.allowed_hosts)
            self.assertNotIn(f"127.0.0.2:{port}", server.allowed_hosts)
        finally:
            server.server_close()

    def test_nonlocal_host_warning(self):
        self.assertIsNone(serve.nonlocal_host_warning("127.0.0.1"))
        self.assertIsNone(serve.nonlocal_host_warning("localhost"))
        self.assertIsNone(serve.nonlocal_host_warning("::1"))
        warning = serve.nonlocal_host_warning("0.0.0.0")
        self.assertIn("warning", warning)
        self.assertIn("0.0.0.0", warning)

    def test_serve_prints_warning_for_nonlocal_host(self):
        port = _free_port()
        process = subprocess.Popen(
            [sys.executable, "-m", "agent_taskboard", "serve",
             "--host", "0.0.0.0", "--port", str(port)],
            cwd=str(self.repo), env=self._env(self.base),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
        )
        try:
            deadline = time.time() + 10
            stderr = ""
            while time.time() < deadline:
                if process.poll() is not None:
                    break
                # read incrementally without blocking the pipe
                try:
                    process.stderr.flush()
                except Exception:
                    pass
                line = process.stderr.readline()
                if line:
                    stderr += line
                    if "warning" in stderr and "0.0.0.0" in stderr:
                        break
                else:
                    time.sleep(0.05)
            self.assertIn("warning", stderr)
            self.assertIn("0.0.0.0", stderr)
        finally:
            process.terminate()
            process.wait(timeout=10)
            process.stdout.close()
            process.stderr.close()


if __name__ == "__main__":
    unittest.main()
