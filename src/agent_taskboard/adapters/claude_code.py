"""Claude Code sessions (read-only): ``~/.claude/projects/**/*.jsonl``.

Each session is one JSONL file: ``<uuid>.jsonl`` under a directory named
after the munged working directory. Lines carry ``timestamp``, ``cwd`` and
``gitBranch``; assistant lines add ``message.model`` and ``message.usage``.
Files grow very large (hundreds of MB), so only the first 4 KB (session
start) and the last 64 KB (everything else) are read. Override the root with
``ATB_CLAUDE_HOME``.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from .base import (
    AdapterError,
    Session,
    classify_state,
    home,
    json_lines,
    parse_iso,
    read_head,
    read_tail,
    summarize,
    text_from_content,
    utcnow,
)

NAME = "claude-code"


class ClaudeCodeAdapter:
    name = NAME

    def __init__(self, root: Path | None = None):
        self._root = root

    def store(self) -> Path:
        if self._root is None:
            base = os.environ.get("ATB_CLAUDE_HOME")
            self._root = Path(base) if base else home() / ".claude"
        return self._root / "projects"

    def available(self) -> bool:
        return self.store().is_dir()

    def sessions(self, since: datetime) -> Iterator[Session]:
        store = self.store()
        if not store.is_dir():
            return
        now = utcnow()
        for path in sorted(store.rglob("*.jsonl")):
            if "subagents" in path.parts:
                continue  # sub-agent transcripts are not user sessions
            try:
                session = self._session_from_file(path, since, now)
            except AdapterError:
                raise
            except OSError as exc:
                raise AdapterError(f"cannot read {path.name}: {exc}") from exc
            if session is not None:
                yield session

    def _session_from_file(
        self, path: Path, since: datetime, now: datetime
    ) -> Session | None:
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=now.tzinfo)
        if mtime < since:
            return None
        session_id = path.stem

        started: datetime | None = None
        head = read_head(path)
        for obj in json_lines(head):
            stamp = parse_iso(obj.get("timestamp"))
            if stamp is not None:
                started = stamp
                break

        cwd = ""
        branch = ""
        model = ""
        usage: dict[str, int] | None = None
        last_active: datetime | None = None
        last_text = ""
        pending_user_turn = False

        for obj in json_lines(read_tail(path)):
            stamp = parse_iso(obj.get("timestamp"))
            if stamp is not None:
                last_active = stamp
            if obj.get("cwd"):
                cwd = obj["cwd"]
            if obj.get("gitBranch"):
                branch = obj["gitBranch"]
            role = obj.get("type")
            message = obj.get("message")
            if not isinstance(message, dict):
                continue
            if message.get("model"):
                model = message["model"]
            raw_usage = message.get("usage")
            if isinstance(raw_usage, dict):
                usage = {
                    "input": int(raw_usage.get("input_tokens") or 0),
                    "output": int(raw_usage.get("output_tokens") or 0),
                    "cache_read": int(raw_usage.get("cache_read_input_tokens") or 0),
                    "cache_write": int(
                        raw_usage.get("cache_creation_input_tokens") or 0
                    ),
                }
            text = summarize(text_from_content(message.get("content")))
            if not text:
                continue
            if role == "user":
                pending_user_turn = True
                last_text = text
            elif role == "assistant":
                pending_user_turn = False
                last_text = text

        if last_active is None or last_active < since:
            return None
        return Session(
            adapter=NAME,
            id=session_id,
            cwd=cwd,
            branch=branch,
            started_at=started,
            last_active_at=last_active,
            state=classify_state(
                last_user_after_reply=pending_user_turn,
                last_active=last_active,
                now=now,
            ),
            model=model,
            last_message=last_text,
            usage=usage,
        )
