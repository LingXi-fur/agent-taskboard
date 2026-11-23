"""Codex sessions (read-only): ``~/.codex/sessions/**``.

The desktop app and the CLI share the same store, laid out as
``sessions/YYYY/MM/DD/rollout-<timestamp>-<uuid>.jsonl[.zst]``. Each file is
a JSONL rollout: line 1 is ``session_meta`` (session id, start time, cwd,
originator), ``turn_context`` lines carry the model and the current cwd,
``event_msg/token_count`` lines the cumulative token usage, and
``response_item`` lines the messages. Files are zstd-compressed in newer
versions, so the reader shells out to the ``zstd`` binary and keeps only a
small head/tail window in memory. Token usage comes from the cumulative
``total_token_usage`` (``event_msg/token_count``) or ``thread_token_usage``
(``token_usage_record``); when only per-turn ``usage`` is recorded the turns
are summed with a ``response_id``/``turn_id`` de-duplication. Override the
root with ``ATB_CODEX_HOME``.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from .base import (
    AdapterError,
    Session,
    add_usage,
    classify_state,
    finish_usage,
    home,
    json_lines,
    parse_iso,
    pretty_model,
    usage_bucket,
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

    def _session_meta(self, head: str) -> dict:
        """Parse the ``session_meta`` line out of the head window.

        That line often exceeds any sane head window because it embeds the
        (huge) base instructions, so when full JSON parsing fails the few
        scalar string fields we need are pulled straight out of the
        truncated text with anchored patterns.
        """
        for obj in json_lines(head):
            if obj.get("type") == "session_meta":
                return obj.get("payload") or {}
        meta: dict = {}
        for key in ("session_id", "timestamp", "cwd", "originator", "model"):
            match = re.search(r'"%s"\s*:\s*"([^"\\]*)"' % key, head)
            if match is not None:
                meta[key] = match.group(1)
        return meta

    @staticmethod
    def _usage_from(total: dict) -> dict[str, int] | None:
        """Map codex token fields to the shared usage keys (only present ones)."""
        totals, present = usage_bucket()
        add_usage(totals, present, "input", total.get("input_tokens"))
        add_usage(totals, present, "output", total.get("output_tokens"))
        add_usage(totals, present, "cache_read", total.get("cached_input_tokens"))
        add_usage(
            totals, present, "cache_write", total.get("cache_write_input_tokens")
        )
        return finish_usage(totals, present)

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
        meta_model = ""
        meta = self._session_meta(read_head(path, limit=256 * 1024))
        if meta:
            session_id = str(meta.get("session_id") or path.stem)
            started = parse_iso(meta.get("timestamp"))
            cwd = str(meta.get("cwd") or "")
            originator = str(meta.get("originator") or "")
            meta_model = str(meta.get("model") or "")

        model = ""
        usage: dict[str, int] | None = None
        turn_totals, turn_present = usage_bucket()
        seen_turns: set[str] = set()
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
                    model = pretty_model(str(payload["model"]))
            elif kind == "event_msg" and payload.get("type") == "token_count":
                info = payload.get("info") or {}
                total = info.get("total_token_usage")
                if isinstance(total, dict):
                    usage = self._usage_from(total)
            elif kind == "token_usage_record":
                cumulative = payload.get("thread_token_usage")
                if isinstance(cumulative, dict):
                    # cumulative for the thread: last one wins
                    usage = self._usage_from(cumulative)
                else:
                    per_turn = payload.get("usage")
                    if isinstance(per_turn, dict):
                        record_id = str(
                            payload.get("response_id")
                            or payload.get("turn_id")
                            or ""
                        )
                        if record_id == "" or record_id not in seen_turns:
                            seen_turns.add(record_id)
                            add_usage(turn_totals, turn_present, "input",
                                      per_turn.get("input_tokens"))
                            add_usage(turn_totals, turn_present, "output",
                                      per_turn.get("output_tokens"))
                            add_usage(turn_totals, turn_present, "cache_read",
                                      per_turn.get("cached_input_tokens"))
                            add_usage(turn_totals, turn_present, "cache_write",
                                      per_turn.get("cache_write_input_tokens"))
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

        if usage is None:
            usage = finish_usage(turn_totals, turn_present)
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
            model=model or pretty_model(meta_model),
            last_message=last_text,
            usage=usage,
            extra={"originator": originator} if originator else {},
        )
