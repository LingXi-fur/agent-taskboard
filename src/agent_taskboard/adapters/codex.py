"""Codex sessions (read-only): ``~/.codex/sessions/**``.

The desktop app and the CLI share the same store, laid out as
``sessions/YYYY/MM/DD/rollout-<timestamp>-<uuid>.jsonl[.zst]``. Each file is
a JSONL rollout: line 1 is ``session_meta`` (session id, start time, cwd,
originator), ``turn_context`` lines carry the model and the current cwd,
``event_msg/token_count`` lines the cumulative token usage, and
``response_item`` lines the messages. Files are zstd-compressed in newer
versions, so the reader shells out to the ``zstd`` binary and keeps only a
small head/tail window in memory. Override the root with ``ATB_CODEX_HOME``.
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
    json_lines,
    parse_iso,
    read_head,
    read_tail,
    summarize,
    utcnow,
)

NAME = "codex"


class CodexAdapter:
    name = NAME

    def __init__(self, root: Path | None = None):
        self._root = root

    def store(self) -> Path:
        if self._root is None:
            base = os.environ.get("ATB_CODEX_HOME")
            self._root = Path(base) if base else home() / ".codex"
        return self._root / "sessions"

    def available(self) -> bool:
        return self.store().is_dir()

    def sessions(self, since: datetime) -> Iterator[Session]:
        store = self.store()
        if not store.is_dir():
            return
        now = utcnow()
        for path in sorted(store.rglob("rollout-*.jsonl*")):
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

        session_id = ""
        started: datetime | None = None
        cwd = ""
        originator = ""
        meta = json_lines(read_head(path))
        for obj in meta:
            if obj.get("type") != "session_meta":
                continue
            payload = obj.get("payload") or {}
            session_id = str(payload.get("session_id") or path.stem)
            started = parse_iso(payload.get("timestamp"))
            cwd = str(payload.get("cwd") or "")
            originator = str(payload.get("originator") or "")
            break

        model = ""
        usage: dict[str, int] | None = None
        last_active: datetime | None = None
        last_text = ""
        last_role = ""

        for obj in json_lines(read_tail(path)):
            stamp = parse_iso(obj.get("timestamp"))
            if stamp is not None:
                last_active = stamp
            kind = obj.get("type")
            payload = obj.get("payload") or {}
            if kind == "turn_context":
                if payload.get("cwd"):
                    cwd = payload["cwd"]
                if payload.get("model"):
                    model = payload["model"]
            elif kind == "event_msg" and payload.get("type") == "token_count":
                info = payload.get("info") or {}
                total = info.get("total_token_usage")
                if isinstance(total, dict):
                    usage = {
                        "input": int(total.get("input_tokens") or 0),
                        "output": int(total.get("output_tokens") or 0),
                        "cache_read": int(total.get("cached_input_tokens") or 0),
                        "cache_write": int(
                            total.get("cache_write_input_tokens") or 0
                        ),
                    }
            elif kind == "response_item":
                role = payload.get("role")
                if role in ("user", "assistant"):
                    last_role = role
                    content = payload.get("content")
                    text = summarize(
                        "\n".join(
                            part.get("text", "")
                            for part in content
                            if isinstance(part, dict)
                        )
                        if isinstance(content, list)
                        else str(content or "")
                    )
                    if text:
                        last_text = text

        if last_active is None or last_active < since:
            return None
        return Session(
            adapter=NAME,
            id=session_id,
            cwd=cwd,
            started_at=started,
            last_active_at=last_active,
            state=classify_state(
                last_user_after_reply=(last_role == "user"),
                last_active=last_active,
                now=now,
            ),
            model=model,
            last_message=last_text,
            usage=usage,
            extra={"originator": originator} if originator else {},
        )
