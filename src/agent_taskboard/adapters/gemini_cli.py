"""Gemini CLI sessions (read-only): ``~/.gemini/tmp/**``.

Each scratch directory under ``~/.gemini/tmp`` holds a ``.project_root`` file
(the working directory) and ``chats/session-*.json`` files. A chat file is a
single JSON object: ``sessionId``, ``startTime``, ``lastUpdated`` and
``messages``; assistant messages carry ``model`` and a ``tokens`` object.
The files are plain JSON (a few MB at most), so they are parsed whole with a
32 MiB guard — they cannot be tail-parsed safely. Token usage sums the
``tokens`` object of every assistant message (``input``/``output``/
``cached``); Gemini records no cache-write counter, so that key is absent.
Override the root with ``ATB_GEMINI_HOME``.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from .base import (
    AdapterError,
    Session,
    SessionEvent,
    add_usage,
    classify_state,
    evidence_text,
    finish_usage,
    home,
    parse_iso,
    pretty_model,
    usage_bucket,
    summarize,
    utcnow,
)

NAME = "gemini-cli"

#: Whole-parse guard: a chat file above this size is skipped, not loaded.
MAX_CHAT_BYTES = 32 * 1024 * 1024


class GeminiCliAdapter:
    name = NAME

    def __init__(self, root: Path | None = None):
        self._root = root

    def store(self) -> Path:
        if self._root is None:
            base = os.environ.get("ATB_GEMINI_HOME")
            self._root = Path(base) if base else home() / ".gemini"
        return self._root / "tmp"

    def available(self) -> bool:
        return self.store().is_dir()

    def sessions(self, since: datetime) -> Iterator[Session]:
        store = self.store()
        if not store.is_dir():
            return
        now = utcnow()
        for path in sorted(store.rglob("chats/session-*.json")):
            try:
                session = self._session_from_file(path, since, now)
            except OSError as exc:
                raise AdapterError(f"cannot read {path.name}: {exc}") from exc
            if session is not None:
                yield session

    def _project_root(self, scratch: Path) -> str:
        marker = scratch / ".project_root"
        try:
            return marker.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return ""

    def _events_from_messages(self, messages: list) -> list[SessionEvent]:
        """Tool-call evidence and per-message tokens from the chat JSON."""
        events: list[SessionEvent] = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            stamp = parse_iso(message.get("timestamp"))
            parts: list[str] = []
            for call in message.get("toolCalls") or []:
                if not isinstance(call, dict):
                    continue
                args = call.get("args") or call.get("params") or call.get("input")
                parts.append(evidence_text(
                    str(call.get("name") or ""),
                    args if isinstance(args, dict) else None,
                ))
            text = message.get("content")
            if isinstance(text, str) and text:
                parts.append(evidence_text("", None, text))
            usage = None
            tokens = message.get("tokens")
            if isinstance(tokens, dict):
                totals, present = usage_bucket()
                add_usage(totals, present, "input", tokens.get("input"))
                add_usage(totals, present, "output", tokens.get("output"))
                add_usage(totals, present, "cache_read", tokens.get("cached"))
                total = tokens.get("total")
                usage = finish_usage(
                    totals, present,
                    int(total) if isinstance(total, (int, float)) else None,
                )
            if parts or usage is not None:
                events.append(SessionEvent(
                    stamp, " ".join(part for part in parts if part), usage
                ))
        return events

    def _session_from_file(
        self, path: Path, since: datetime, now: datetime
    ) -> Session | None:
        if path.stat().st_size > MAX_CHAT_BYTES:
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except (json.JSONDecodeError, ValueError) as exc:
            raise AdapterError(f"{path.name} is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            return None

        started = parse_iso(data.get("startTime"))
        last_active = parse_iso(data.get("lastUpdated"))
        messages = data.get("messages")
        if not isinstance(messages, list):
            messages = []
        if last_active is None:
            for message in reversed(messages):
                if isinstance(message, dict):
                    last_active = parse_iso(message.get("timestamp"))
                    if last_active is not None:
                        break
        if last_active is None or last_active < since:
            return None

        model = ""
        last_text = ""
        last_role = ""
        for message in reversed(messages):
            if not isinstance(message, dict):
                continue
            kind = message.get("type")
            if not model and message.get("model"):
                model = pretty_model(str(message["model"]))
            if not last_role and kind in ("user", "gemini"):
                last_role = str(kind)
                text = summarize(str(message.get("content") or ""))
                if text:
                    last_text = text
            if model and last_role:
                break  # newest message and model are all in hand

        # token usage: sum every message's tokens (the chat file is parsed
        # whole, so no streaming needed); cached -> cache_read, no cache_write
        totals, present = usage_bucket()
        usage_total: int | None = None
        for message in messages:
            if not isinstance(message, dict):
                continue
            tokens = message.get("tokens")
            if not isinstance(tokens, dict):
                continue
            add_usage(totals, present, "input", tokens.get("input"))
            add_usage(totals, present, "output", tokens.get("output"))
            add_usage(totals, present, "cache_read", tokens.get("cached"))
            if tokens.get("total") is not None:
                usage_total = (usage_total or 0) + int(tokens["total"])
        usage = finish_usage(totals, present, usage_total)

        if last_role == "user":
            # a user turn without an assistant reply: the run is in flight
            state = classify_state(
                last_user_after_reply=True, last_active=last_active, now=now
            )
        else:
            state = classify_state(
                last_user_after_reply=False, last_active=last_active, now=now
            )

        return Session(
            adapter=NAME,
            id=str(data.get("sessionId") or path.stem),
            cwd=self._project_root(path.parent.parent),
            started_at=started,
            last_active_at=last_active,
            state=state,
            model=model,
            last_message=last_text,
            usage=usage,
            events_loader=(
                lambda messages=messages: self._events_from_messages(messages)
            ),
        )
