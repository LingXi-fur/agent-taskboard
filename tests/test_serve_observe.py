"""Tests for ``atb serve --observe``: sessions only, any directory, same safety."""

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

from agent_taskboard import serve

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _claude_session_store(base: Path) -> Path:
    """A tiny real-format store with one session (no task mapping)."""
    store = base / "claude-home" / "projects" / "demo"
    store.mkdir(parents=True)
    stamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    line = {
        "type": "assistant", "timestamp": stamp, "cwd": "/observe-demo",
        "sessionId": "obs-1", "requestId": "r1",
        "message": {
            "role": "assistant", "id": "m1", "model": "claude-opus-5-5",
            "content": [{"type": "text", "text": "hi"}],
            "usage": {"input_tokens": 10, "output_tokens": 2},
        },
    }
    (store / "obs-1.jsonl").write_text(json.dumps(line) + "\n", encoding="utf-8")
    return base / "claude-home"


class ObserveModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.base = Path(tempfile.mkdtemp(prefix="atb-observe-"))
        cls.claude_home = _claude_session_store(cls.base)
        cls._env_guard = {
            key: os.environ.get(key)
            for key in ("ATB_CLAUDE_HOME", "ATB_CODEX_HOME", "ATB_GEMINI_HOME",
                        "ATB_OPENCODE_HOME")
        }
        os.environ["ATB_CLAUDE_HOME"] = str(cls.claude_home)
        for key in ("ATB_CODEX_HOME", "ATB_GEMINI_HOME", "ATB_OPENCODE_HOME"):
            os.environ[key] = str(cls.base / f"empty-{key}")
        cls.server = serve.build_server(None, host="127.0.0.1", port=0, poll=0.2,
                                        observe=True)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        for key, value in cls._env_guard.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _get(self, path: str, origin: str | None = None) -> tuple[int, dict]:
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}")
        if origin:
            request.add_header("Origin", origin)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read().decode("utf-8"))

    def test_server_bind_does_not_call_reverse_dns(self):
        """HTTPServer's getfqdn() lookup hangs on CI runners without DNS.

        The macOS CI jobs sat in server_bind for minutes while the socket was
        already listening, so every request timed out. Building a server must
        never touch getfqdn.
        """
        from unittest import mock

        with mock.patch(
            "socket.getfqdn",
            side_effect=AssertionError("server_bind must not call getfqdn"),
        ):
            server = serve.build_server(
                None, host="127.0.0.1", port=0, poll=5, observe=True
            )
            try:
                self.assertEqual(server.server_name, "127.0.0.1")
            finally:
                server.server_close()

    def test_overview_reports_observe_mode(self):
        status, payload = self._get("/api/v1/overview")
        self.assertEqual(status, 200)
        self.assertEqual(payload["mode"], "observe")
        self.assertIsNone(payload["repo_root"])
        self.assertEqual(payload["budgets"], [])

    def test_sessions_work_in_observe_mode(self):
        status, payload = self._get("/api/v1/sessions")
        self.assertEqual(status, 200)
        self.assertIn("sessions", payload)
        self.assertGreaterEqual(len(payload["sessions"]), 1)
        self.assertEqual(payload["sessions"][0]["adapter"], "claude-code")
        self.assertIsNone(payload["sessions"][0]["task"])

    def test_task_endpoints_are_404_with_explanation(self):
        for path in ("/api/v1/tasks", "/api/v1/tasks/T001",
                     "/api/v1/tasks/T001/prompt", "/api/v1/events"):
            status, payload = self._get(path)
            self.assertEqual(status, 404, path)
            self.assertIn("observe mode", payload["error"], path)

    def test_static_page_has_observe_notice(self):
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}/")
        with urllib.request.urlopen(request, timeout=10) as response:
            page = response.read().decode("utf-8")
        self.assertIn('id="observe-notice"', page)

    def test_stream_works_in_observe_mode(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        connection.request("GET", "/api/v1/stream")
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        collected = b""
        while b"\n\n" not in collected:
            chunk = response.read(1)
            if not chunk:
                break
            collected += chunk
        self.assertIn(b"event: refresh", collected)
        connection.close()

    def test_forged_host_is_rejected_in_observe_mode(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        connection.putrequest("GET", "/api/v1/sessions", skip_host=True)
        connection.putheader("Host", "evil.example:8766")
        connection.putheader("Origin", "http://evil.example:8766")
        connection.endheaders()
        response = connection.getresponse()
        self.assertEqual(response.status, 403)
        payload = json.loads(response.read().decode("utf-8"))
        self.assertIn("host", payload["error"])
        connection.close()

    def test_origin_rejected_but_local_allowed(self):
        status, _ = self._get("/api/v1/sessions", origin="http://evil.example")
        self.assertEqual(status, 403)
        status, _ = self._get(
            "/api/v1/sessions", origin=f"http://127.0.0.1:{self.port}"
        )
        self.assertEqual(status, 200)


class ObserveAutoSwitchTests(unittest.TestCase):
    def _run_server(self, cwd: Path, *args: str):
        port = _free_port()
        env = os.environ.copy()
        env["PYTHONPATH"] = str(SRC)
        # ask the server to dump all thread stacks after 10s (test-only hook)
        env["ATB_TEST_FAULTHANDLER"] = "1"
        env["ATB_CLAUDE_HOME"] = str(self.base / "claude-home")
        for key in ("ATB_CODEX_HOME", "ATB_GEMINI_HOME", "ATB_OPENCODE_HOME"):
            env[key] = str(self.base / f"empty-{key}")
        process = subprocess.Popen(
            [sys.executable, "-m", "agent_taskboard", "serve",
             "--port", str(port), *args],
            cwd=str(cwd), env=env, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8",
            errors="replace",
        )
        return process, port

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="atb-observe-e2e-"))
        self.claude_home = _claude_session_store(self.base)

    def _wait_mode(self, process, port: int) -> str:
        # Poll the subprocess directly and, if it never answers, report its
        # exit code and stderr so a CI failure explains itself.
        deadline = time.time() + 30
        last_error = None
        while time.time() < deadline:
            if process.poll() is not None:
                break
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
            try:
                conn.request("GET", "/api/v1/overview",
                             headers={"Host": f"127.0.0.1:{port}"})
                response = conn.getresponse()
                body = response.read().decode("utf-8")
                if response.status == 200:
                    return json.loads(body)["mode"]
                last_error = f"HTTP {response.status}: {body[:200]}"
            except Exception as exc:  # not listening yet
                last_error = repr(exc)
            finally:
                conn.close()
            time.sleep(0.15)
        if process.poll() is None:
            process.terminate()
        try:
            _, stderr = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            _, stderr = process.communicate()
        self.fail(
            "server did not start: returncode=%r last_error=%s stderr=%s"
            % (process.returncode, last_error, (stderr or "")[-6000:])
        )

    def test_starts_in_observe_mode_outside_a_repository(self):
        plain = self.base / "plain-dir"
        plain.mkdir()
        process, port = self._run_server(plain)
        try:
            self.assertEqual(self._wait_mode(process, port), "observe")
            stderr = process.stderr.readline()
            self.assertIn("observe mode", stderr)
        finally:
            process.terminate()
            process.wait(timeout=10)
            process.stdout.close()
            process.stderr.close()

    def test_observe_flag_wins_inside_a_taskboard_repo(self):
        repo = self.base / "repo"
        repo.mkdir()
        env = os.environ.copy()
        env["PYTHONPATH"] = str(SRC)
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo,
                       env=env, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=repo, env=env, check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, env=env, check=True)
        (repo / ".taskboard" / "tasks").mkdir(parents=True)
        (repo / ".taskboard" / "config.toml").write_text(
            'worktree_root = "../wts"\n', encoding="utf-8"
        )
        process, port = self._run_server(repo, "--observe")
        try:
            self.assertEqual(self._wait_mode(process, port), "observe")
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/v1/tasks", timeout=5
            ) as response:
                self.fail("task endpoint should be 404")
        except urllib.error.HTTPError as error:
            self.assertEqual(error.code, 404)
        finally:
            process.terminate()
            process.wait(timeout=10)
            process.stdout.close()
            process.stderr.close()

    def test_git_repo_without_taskboard_falls_back_with_hint(self):
        repo = self.base / "repo2"
        repo.mkdir()
        env = os.environ.copy()
        env["PYTHONPATH"] = str(SRC)
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo,
                       env=env, check=True)
        process, port = self._run_server(repo)
        try:
            self.assertEqual(self._wait_mode(process, port), "observe")
            stderr = process.stderr.readline()
            self.assertIn("no .taskboard/", stderr)
        finally:
            process.terminate()
            process.wait(timeout=10)
            process.stdout.close()
            process.stderr.close()


if __name__ == "__main__":
    unittest.main()
