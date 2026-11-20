"""Gemini CLI sessions (read-only): ``~/.gemini/tmp/**``.

Each scratch directory under ``~/.gemini/tmp`` holds a ``.project_root`` file
(the working directory) and ``chats/session-*.json`` files. A chat file is a
single JSON object: ``sessionId``, ``startTime``, ``lastUpdated`` and
``messages``; assistant messages carry ``model`` and a ``tokens`` object.
The files are plain JSON (a few MB at most), so they are parsed whole with a
32 MiB guard — they cannot be tail-parsed safely. Override the root with
``ATB_GEMINI_HOME``.
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
    classify_state,
    home,
    parse_iso,
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
        usage: dict[str, int] | None = None
        last_text = ""
        last_role = ""
        for message in reversed(messages):
            if not isinstance(message, dict):
                continue
            kind = message.get("type")
            tokens = message.get("tokens")
            if not model and message.get("model"):
                model = str(message["model"])
            if usage is None and isinstance(tokens, dict):
                usage = {
                    "input": int(tokens.get("input") or 0),
                    "output": int(tokens.get("output") or 0),
                    "cache_read": int(tokens.get("cached") or 0),
                }
            if not last_role and kind in ("user", "gemini"):
                last_role = str(kind)
                text = summarize(str(message.get("content") or ""))
                if text:
                    last_text = text
            if model and usage and last_role:
                break  # newest message, model and usage are all in hand

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
        )
