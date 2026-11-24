"""``atb serve``: a local, read-only console over ``.taskboard/``.

Standard library only (``http.server``), vanilla JS and CSS from
``agent_taskboard/web`` — no build step, no CDN, fully offline. The JSON API
under ``/api/v1`` reuses the same payload builders as ``atb status``,
``atb review --json``, ``atb log`` and ``atb sessions``; nothing here
re-implements them.

Phase 1 is read-only by design: no write endpoints exist, and the only
frontend action ("copy handoff") just puts ``atb prompt`` output on the
clipboard. ``--read-only`` is accepted for forward compatibility.

Security posture: the server binds to ``127.0.0.1`` unless ``--host`` says
otherwise (with a printed warning), and any request carrying a non-local
``Origin`` header is rejected with 403.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import budgets, config, gitops, history, prompts
from .adapters.base import AdapterError  # noqa: F401  (documented contract)
from .errors import AtbError
from .sheet import STATUSES, Sheet

#: The static files served from agent_taskboard/web.
STATIC_FILES: dict[str, tuple[str, str]] = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/locales/en.json": ("locales/en.json", "application/json; charset=utf-8"),
    "/locales/zh.json": ("locales/zh.json", "application/json; charset=utf-8"),
}

_WEB_DIR = Path(__file__).resolve().parent / "web"

_DEFAULT_HOST = "127.0.0.1"


# --------------------------------------------------------------------------- #
# payload builders (thin adapters over the existing CLI logic)
# --------------------------------------------------------------------------- #

def _cli():
    """Import cli lazily: cmd_serve imports this module, so a top-level
    import would be circular. Inside a request handler the module is loaded
    anyway."""
    from . import cli

    return cli


def _sheets(repo_root: Path) -> list[Sheet]:
    return _cli()._load_tasks(repo_root)


def _payload_overview(repo_root: Path, cfg: config.Config, rows: list[dict]) -> dict:
    summary = {status: 0 for status in STATUSES}
    for row in rows:
        if row["status"] in summary:
            summary[row["status"]] += 1
    return {
        "mode": "task",
        "target": cfg.target,
        "repo_root": str(repo_root),
        "upstream": gitops.upstream_status(cfg.target, cwd=repo_root),
        "summary": summary,
        "active": sum(
            summary[status] for status in ("doing", "review", "rework")
        ),
        "awaiting_review": summary["review"],
        "merged_today": _merged_today(repo_root, cfg),
        "budgets": budgets.compute(cfg),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _payload_observe_overview() -> dict:
    """Overview for ``--observe``: sessions only, no repository at all."""
    return {
        "mode": "observe",
        "repo_root": None,
        "budgets": [],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _merged_today(repo_root: Path, cfg: config.Config) -> int:
    """Count today's task merges on the target (one git log call)."""
    midnight = datetime.now().astimezone().replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    try:
        proc = subprocess.run(
            [
                "git",
                "log",
                f"--since={int(midnight.timestamp())}",
                f"--format=%s",
                cfg.target,
            ],
            cwd=str(repo_root),
            env=gitops.git_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError:
        return 0
    if proc.returncode != 0:
        return 0
    pattern = re.compile(
        rf"^Merge \S+ \({re.escape(cfg.id_prefix)}\d{{3,}}: "
    )
    return sum(1 for line in proc.stdout.splitlines() if pattern.match(line))


def _payload_tasks(repo_root: Path, cfg: config.Config, sheets: list[Sheet]) -> dict:
    cli = _cli()
    rows, _conflicts = cli._build_board_rows(sheets, cfg, repo_root, lang="en")
    summary = {status: 0 for status in STATUSES}
    for row in rows:
        if row["status"] in summary:
            summary[row["status"]] += 1
    return {
        "tasks": rows,
        "summary": summary,
        "upstream": gitops.upstream_status(cfg.target, cwd=repo_root),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _payload_task(repo_root: Path, cfg: config.Config, task_id: str) -> dict:
    cli = _cli()
    sheet = cli._load_task(repo_root, task_id)
    payload = json.loads(cli._build_review_json(sheet, cfg, repo_root, lang="en"))
    payload["events"] = history.collect_task_history(
        repo_root, cfg, sheet
    )["events"]
    return payload


def _task_prompt(repo_root: Path, cfg: config.Config, task_id: str) -> dict:
    cli = _cli()
    sheet = cli._load_task(repo_root, task_id)
    if not sheet.branch:
        raise AtbError(f"task {task_id} has no branch; run 'atb start' first")
    worktree = config.resolve_worktree_root(cfg) / sheet.id
    text = prompts.render_prompt(
        sheet, str(worktree), sheet.branch, sheet.test or cfg.test, lang="en"
    )
    return {"id": task_id, "text": text}


def _payload_events(
    repo_root: Path,
    cfg: config.Config,
    sheets: list[Sheet],
    *,
    day: str | None,
    days: int | None,
    task_id: str | None,
) -> dict:
    """Every task's history events, filtered to a local day or a day range."""
    items: list[dict[str, Any]] = []
    if task_id is not None:
        selected = [sheet for sheet in sheets if sheet.id == task_id]
        if not selected:
            raise AtbError(f"task {task_id!r} not found")
        sheets = selected

    if day is not None:
        wanted = {day}
    elif days is not None:
        today = datetime.now().astimezone()
        wanted = {
            (today - timedelta(offset)).strftime("%Y-%m-%d")
            for offset in range(max(1, days))
        }
    else:
        wanted = None

    for sheet in sheets:
        try:
            collected = history.collect_task_history(repo_root, cfg, sheet)
        except (AtbError, OSError):
            continue  # one unreadable task must not break the timeline
        for event in collected["events"]:
            if event.get("kind") not in (
                "status", "test", "rework", "merge", "note"
            ):
                continue
            if wanted is not None:
                stamp = _parse_iso_local(event.get("timestamp"))
                if stamp is None or stamp.strftime("%Y-%m-%d") not in wanted:
                    continue
            items.append({
                "task": sheet.id,
                "title": sheet.title,
                "agent": sheet.agent,
                "kind": event["kind"],
                "timestamp": event.get("timestamp"),
                "time_local": event.get("time_local"),
                "text": event.get("text", ""),
                "from": event.get("from"),
                "to": event.get("to"),
                "exit_code": event.get("exit_code"),
                "commit": event.get("commit"),
                "by": event.get("by"),
                "note": event.get("note"),
            })
    items.sort(key=lambda item: item.get("timestamp") or "")
    if day is not None:
        range_info = {"from": day, "to": day, "days": 1}
    elif days is not None:
        range_info = {
            "from": min(wanted),
            "to": max(wanted),
            "days": max(1, days),
        }
    else:
        range_info = {"from": None, "to": None, "days": None}
    return {"events": items, "count": len(items), "range": range_info}


def _parse_iso_local(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    raw = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone()


def _payload_sessions(
    repo_root: Path | None, cfg: config.Config | None, sheets: list[Sheet],
    since_spec: str | None, adapter: str | None,
) -> dict:
    """The same data ``atb sessions --json`` prints (phase 2 wired in)."""
    cli = _cli()
    since, spec = cli._parse_sessions_since(since_spec)
    sessions, entries = cli._collect_sessions(since, adapter)
    if cfg is None:
        worktrees, branches = [], {}
    else:
        worktrees, branches = cli._session_task_maps(cfg, sheets)
    rows = []
    for session in sessions:
        task_id = cli._map_session_to_task(session, worktrees, branches)
        rows.append(cli._session_to_row(session, task_id, redact=False))
    rows.sort(
        key=lambda row: row["last_active_at"] or row["started_at"] or "",
        reverse=True,
    )
    return {
        "since": since.isoformat(),
        "since_spec": spec,
        "adapters": entries,
        "sessions": rows,
        "unmapped": sum(1 for row in rows if not row["mapped"]),
    }


# --------------------------------------------------------------------------- #
# change detection for the SSE stream
# --------------------------------------------------------------------------- #

def _state_checksum(repo_root: Path | None) -> str:
    """A cheap fingerprint of .taskboard/ plus the target's HEAD.

    In observe mode there is no repository; returning a fresh value on every
    call makes the SSE stream refresh the page once per poll interval.
    """
    if repo_root is None:
        return f"observe:{time.time_ns()}"
    parts: list[str] = []
    try:
        parts.append(gitops.rev_parse("HEAD", cwd=repo_root))
    except gitops.GitError:
        parts.append("(no commits)")
    board = repo_root / ".taskboard"
    for path in sorted(board.rglob("*")):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        parts.append(f"{path.relative_to(board)}:{stat.st_mtime_ns}:{stat.st_size}")
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# HTTP plumbing
# --------------------------------------------------------------------------- #

class _ServeServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address,
        handler,
        *,
        repo_root: Path | None,
        observe: bool = False,
        poll: float,
        allowed_hosts: set[str],
    ):
        super().__init__(address, handler)
        self.repo_root = repo_root
        self.observe = bool(observe) or repo_root is None
        self.poll = poll
        #: Host headers we answer to. Everything else — including a name that
        #: some DNS record points at 127.0.0.1 (DNS rebinding) — gets 403.
        self.allowed_hosts = allowed_hosts


def _allowed_hosts(host: str, port: int) -> set[str]:
    """The Host headers this server answers, given its bind address."""
    hosts = {
        f"127.0.0.1:{port}",
        f"localhost:{port}",
        f"[::1]:{port}",
        f"{host.lower()}:{port}",
    }
    if port in (80, 443):
        hosts.update({"127.0.0.1", "localhost", "[::1]", host.lower()})
    return hosts


class _RequestHandler(BaseHTTPRequestHandler):
    server: _ServeServer

    # silence the default stderr access log; errors still surface in tests
    def log_message(self, fmt: str, *args: Any) -> None:
        pass

    # -- security -------------------------------------------------------- #

    def _host_allowed(self) -> bool:
        """Every request is Host-checked, Origin or not (DNS-rebinding guard).

        A hostile page can point its own domain at 127.0.0.1 so the Origin
        and Host headers agree; only an explicit allow-list of local host
        names defeats that.
        """
        host = (self.headers.get("Host") or "").strip().lower()
        return host in self.server.allowed_hosts

    def _origin_allowed(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True  # curl, tests, EventSource on same origin all fine
        netloc = urllib.parse.urlparse(origin).netloc.lower()
        return netloc in self.server.allowed_hosts

    # -- helpers --------------------------------------------------------- #

    def _send(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload: Any, status: int = 200) -> None:
        self._send(
            json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
            "application/json; charset=utf-8",
            status,
        )

    def _context(self) -> tuple[Path, config.Config, list[Sheet]]:
        repo_root = self.server.repo_root
        cfg = config.load_config(repo_root)
        return repo_root, cfg, _sheets(repo_root)

    # -- routing --------------------------------------------------------- #

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        if not self._host_allowed():
            self._send_json({"error": "host not allowed"}, status=403)
            return
        if not self._origin_allowed():
            self._send_json({"error": "origin not allowed"}, status=403)
            return
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        try:
            if path in STATIC_FILES:
                self._serve_static(path)
            elif self.server.observe and path not in (
                "/api/v1/overview",
                "/api/v1/sessions",
                "/api/v1/stream",
            ):
                self._send_json(
                    {
                        "error": "task endpoints are unavailable in observe "
                                 "mode (no .taskboard/); only /api/v1/overview, "
                                 "/api/v1/sessions and /api/v1/stream work"
                    },
                    status=404,
                )
            elif path == "/api/v1/overview" and self.server.observe:
                self._send_json(_payload_observe_overview())
            elif path == "/api/v1/overview":
                repo_root, cfg, sheets = self._context()
                payload = _payload_tasks(repo_root, cfg, sheets)
                overview = _payload_overview(repo_root, cfg, payload["tasks"])
                self._send_json(overview)
            elif path == "/api/v1/tasks":
                repo_root, cfg, sheets = self._context()
                self._send_json(_payload_tasks(repo_root, cfg, sheets))
            elif path.startswith("/api/v1/tasks/"):
                repo_root, cfg, sheets = self._context()
                rest = path[len("/api/v1/tasks/"):]
                if rest.endswith("/prompt"):
                    self._send_json(
                        _task_prompt(repo_root, cfg, rest[: -len("/prompt")])
                    )
                else:
                    self._send_json(_payload_task(repo_root, cfg, rest))
            elif path == "/api/v1/events":
                repo_root, cfg, sheets = self._context()
                day = (query.get("day") or [None])[0]
                days_raw = (query.get("days") or [None])[0]
                days = None
                if days_raw is not None:
                    try:
                        days = max(1, int(days_raw))
                    except ValueError:
                        self._send_json(
                            {"error": f"invalid days {days_raw!r}"}, status=400
                        )
                        return
                task_id = (query.get("task") or [None])[0]
                self._send_json(
                    _payload_events(
                        repo_root, cfg, sheets, day=day, days=days,
                        task_id=task_id,
                    )
                )
            elif path == "/api/v1/sessions":
                if self.server.observe:
                    self._send_json(
                        _payload_sessions(
                            None, None, [],
                            (query.get("since") or [None])[0],
                            (query.get("adapter") or [None])[0],
                        )
                    )
                else:
                    repo_root, cfg, sheets = self._context()
                    self._send_json(
                        _payload_sessions(
                            repo_root,
                            cfg,
                            sheets,
                            (query.get("since") or [None])[0],
                            (query.get("adapter") or [None])[0],
                        )
                    )
            elif path == "/api/v1/stream":
                self._serve_stream()
            else:
                self._send_json({"error": f"no route for {path}"}, status=404)
        except AtbError as exc:
            self._send_json({"error": str(exc)}, status=404)
        except (OSError, ValueError) as exc:
            self._send_json({"error": f"{type(exc).__name__}: {exc}"}, status=500)
        except Exception as exc:  # one broken payload must never kill a request
            self._send_json(
                {"error": f"unexpected {type(exc).__name__}: {exc}"}, status=500
            )

    def _serve_static(self, path: str) -> None:
        filename, content_type = STATIC_FILES[path]
        try:
            body = (_WEB_DIR / filename).read_bytes()
        except OSError as exc:
            self._send_json(
                {"error": f"missing static file {filename}: {exc}"}, status=500
            )
            return
        self._send(body, content_type)

    def _serve_stream(self) -> None:
        """SSE: one ``refresh`` event whenever .taskboard/ or HEAD changes."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        repo_root = self.server.repo_root
        poll = max(0.05, float(self.server.poll))
        last = None
        try:
            while True:
                checksum = _state_checksum(repo_root)
                if checksum != last:
                    last = checksum
                    payload = json.dumps(
                        {"type": "refresh", "checksum": checksum}
                    )
                    self.wfile.write(
                        f"event: refresh\ndata: {payload}\n\n".encode("utf-8")
                    )
                    self.wfile.flush()
                time.sleep(poll)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return


def nonlocal_host_warning(host: str) -> str | None:
    """The warning printed when --host is not a loopback address."""
    if host in ("127.0.0.1", "::1", "localhost"):
        return None
    return (
        f"warning: atb serve is bound to {host!r}, which other machines can "
        "reach; the console shows local task data. Keep it on 127.0.0.1 "
        "unless you know why not."
    )


def build_server(
    repo_root: Path | None,
    *,
    host: str = _DEFAULT_HOST,
    port: int = 8766,
    poll: float = 5.0,
    observe: bool = False,
) -> _ServeServer:
    server = _ServeServer(
        (host, port),
        _RequestHandler,
        repo_root=repo_root,
        observe=observe or repo_root is None,
        poll=poll,
        allowed_hosts=set(),
    )
    # the real port is only known after bind (tests ask for an ephemeral one)
    server.allowed_hosts = _allowed_hosts(host, server.server_address[1])
    return server


def run(
    repo_root: Path | None = None,
    *,
    host: str = _DEFAULT_HOST,
    port: int = 8766,
    poll: float = 5.0,
    read_only: bool = True,
    observe: bool = False,
) -> int:
    """Serve the console until interrupted; returns the exit code.

    ``observe=True`` (or ``repo_root=None``) serves sessions only: it works
    in any directory, no git repository or ``.taskboard/`` needed.
    """
    # Test/diagnostic hook: when the test suite asks for it, dump every
    # thread's stack to stderr after 10s so a hung request explains itself.
    # Normal runs never set this variable.
    if os.environ.get("ATB_TEST_FAULTHANDLER") == "1":
        import faulthandler

        faulthandler.dump_traceback_later(10, exit=False, repeat=False)
    if repo_root is None:
        observe = True
    warning = nonlocal_host_warning(host)
    if warning:
        import sys

        print(warning, file=sys.stderr)

    server = build_server(
        repo_root, host=host, port=port, poll=poll, observe=observe
    )
    address = server.server_address
    print(
        f"atb serve: http://{address[0]}:{address[1]} "
        f"(poll {poll:g}s, Ctrl-C to stop)"
    )
    if observe:
        print("observe mode: local agent sessions only (no .taskboard/)")
    else:
        print("read-only: no write endpoints in this phase")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
