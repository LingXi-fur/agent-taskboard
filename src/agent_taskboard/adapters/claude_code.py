"""Claude Code sessions (read-only): ``~/.claude/projects/**/*.jsonl``.

Each session is one JSONL file: ``<uuid>.jsonl`` under a directory named
after the munged working directory. Lines carry ``timestamp``, ``cwd`` and
``gitBranch``; assistant lines add ``message.model`` and ``message.usage``.
Files grow very large (hundreds of MB), so only the first 4 KB (session
start) and the last 64 KB (everything else) are read for the metadata.
Token usage is summed in a separate bounded streaming pass (64 MiB per
file, line by line, never loaded whole); streaming writes the same
``message.id``/``requestId`` several times, so usage is de-duplicated by
that pair. Override the root with ``ATB_CLAUDE_HOME``.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from .base import (
    USAGE_SCAN_LIMIT,
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

    def _usage_from_file(self, path: Path) -> dict[str, int] | None:
        """Sum assistant ``message.usage`` across the session, streamed.

        Only the first USAGE_SCAN_LIMIT bytes are read (bounded memory and
        time for multi-hundred-MB transcripts). Streaming writes the same
        message repeatedly; the (message.id, requestId) pair de-duplicates
        them so a single response is counted once.
        """
        totals, present = usage_bucket()
        seen: set[tuple[str, str]] = set()
        budget = USAGE_SCAN_LIMIT
        try:
            with path.open("rb") as handle:
                for raw in handle:
                    budget -= len(raw)
                    if budget < 0:
                        break
                    if b'"usage"' not in raw:
                        continue
                    try:
                        obj = json.loads(raw)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    message = obj.get("message")
                    if not isinstance(message, dict):
                        continue
                    raw_usage = message.get("usage")
                    if not isinstance(raw_usage, dict):
                        continue
                    identity = (
                        str(message.get("id") or ""),
                        str(obj.get("requestId") or ""),
                    )
                    if identity != ("", "") and identity in seen:
                        continue
                    seen.add(identity)
                    add_usage(totals, present, "input", raw_usage.get("input_tokens"))
                    add_usage(totals, present, "output", raw_usage.get("output_tokens"))
                    add_usage(
                        totals, present, "cache_read",
                        raw_usage.get("cache_read_input_tokens"),
                    )
                    add_usage(
                        totals, present, "cache_write",
                        raw_usage.get("cache_creation_input_tokens"),
                    )
        except OSError:
            return None
        return finish_usage(totals, present)

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
                model = pretty_model(str(message["model"]))
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
            usage=None,
            usage_loader=lambda path=path: self._usage_from_file(path),
        )
