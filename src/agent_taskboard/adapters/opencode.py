"""OpenCode sessions (read-only): ``~/.local/share/opencode/opencode.db``.

The SQLite database is opened with ``file:...?mode=ro`` (plus
``PRAGMA query_only``), so atb never takes a write lock and a running
OpenCode keeps working: SQLite readers and the writer coexist through the
WAL. The columns used are ``session_v2`` (directory, model, tokens, busy and
idle timestamps) and the newest ``session_message`` row per session for the
last text. Token usage comes from the session row's cumulative counter
columns (``tokens_input``/``tokens_output``/``tokens_cache_read``/
``tokens_cache_write``); absent columns are omitted rather than zeroed.
Override the data directory with ``ATB_OPENCODE_HOME``.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from .base import (
    AdapterError,
    Session,
    add_usage,
    classify_state,
    epoch_ms,
    finish_usage,
    home,
    usage_bucket,
    pretty_model,
    summarize,
    text_from_content,
    utcnow,
)

NAME = "opencode"


class OpenCodeAdapter:
    name = NAME

    def __init__(self, root: Path | None = None):
        self._root = root

    def db_path(self) -> Path:
        if self._root is not None:
            return Path(self._root) / "opencode.db"
        override = os.environ.get("ATB_OPENCODE_HOME")
        if override:
            return Path(override) / "opencode.db"
        data_home = os.environ.get("XDG_DATA_HOME")
        if data_home:
            return Path(data_home) / "opencode" / "opencode.db"
        local_appdata = os.environ.get("LOCALAPPDATA")
        if local_appdata:
            return Path(local_appdata) / "opencode" / "opencode.db"
        return home() / ".local" / "share" / "opencode" / "opencode.db"

    def available(self) -> bool:
        return self.db_path().is_file()

    def sessions(self, since: datetime) -> Iterator[Session]:
        db_path = self.db_path()
        if not db_path.is_file():
            return
        since_ms = int(since.timestamp() * 1000)
        uri = f"file:{db_path.as_posix()}?mode=ro"
        try:
            # mode=ro: no journal, no write lock, WAL readers coexist with the
            # running app's writer.
            connection = sqlite3.connect(uri, uri=True, timeout=5.0)
        except sqlite3.Error as exc:
            raise AdapterError(f"cannot open {db_path.name}: {exc}") from exc
        try:
            connection.execute("PRAGMA query_only = 1")
            rows = connection.execute(
                """
                SELECT id, directory, title, model, time_created, time_updated,
                       time_busy, time_idle, time_archived,
                       tokens_input, tokens_output,
                       tokens_cache_read, tokens_cache_write
                FROM session_v2
                WHERE time_updated >= ?
                ORDER BY time_updated DESC
                """,
                (since_ms,),
            ).fetchall()
        except sqlite3.Error as exc:
            # close before raising: on Windows an open handle keeps the file
            # locked, which would break test cleanup (and any external tool)
            connection.close()
            raise AdapterError(f"cannot read sessions: {exc}") from exc

        now = utcnow()
        sessions = [
            self._session_from_row(row, now) for row in rows
        ]
        sessions = [item for item in sessions if item is not None]
        try:
            self._attach_last_messages(connection, sessions)
        finally:
            connection.close()
        yield from sessions

    def _attach_last_messages(
        self, connection: sqlite3.Connection, sessions: list[Session]
    ) -> None:
        """Fill ``last_message`` for every session with one grouped query."""
        by_id = {session.id: session for session in sessions}
        ids = list(by_id)
        remaining = set(by_id)
        while remaining:
            chunk = sorted(remaining)[:100]
            placeholders = ",".join("?" * len(chunk))
            try:
                cursor = connection.execute(
                    """
                    SELECT session_id, data FROM session_message
                    WHERE session_id IN (%s)
                    ORDER BY time_created DESC
                    """ % placeholders,
                    chunk,
                )
                for session_id, raw in cursor:
                    session = by_id.get(str(session_id))
                    if session is None or session.last_message:
                        continue
                    text = self._message_text(raw)
                    if text:
                        session.last_message = text
                    remaining.discard(str(session_id))
                    if not remaining:
                        break
                remaining -= set(chunk)  # sessions with no readable message
            except sqlite3.Error as exc:
                raise AdapterError(f"cannot read messages: {exc}") from exc

    def _message_text(self, raw) -> str:
        if not raw:
            return ""
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return ""
        if not isinstance(data, dict):
            return ""
        return summarize(text_from_content(data.get("content")))

    def _session_from_row(self, row, now: datetime) -> Session | None:
        (
            session_id,
            directory,
            _title,
            model_raw,
            time_created,
            time_updated,
            time_busy,
            time_idle,
            time_archived,
            tokens_input,
            tokens_output,
            tokens_cache_read,
            tokens_cache_write,
        ) = row
        last_active = epoch_ms(time_updated)
        if last_active is None:
            return None
        started = epoch_ms(time_created)
        model = ""
        if model_raw:
            try:
                parsed = json.loads(model_raw)
                if isinstance(parsed, dict):
                    model = pretty_model(
                        str(parsed.get("id") or parsed.get("model") or "")
                    )
                elif isinstance(parsed, str):
                    model = pretty_model(parsed)
            except (json.JSONDecodeError, TypeError):
                model = pretty_model(str(model_raw))

        busy: bool | None = None
        if time_busy is not None and time_idle is not None:
            busy = int(time_busy) > int(time_idle)
        # the session row carries cumulative token counters
        totals, present = usage_bucket()
        add_usage(totals, present, "input", tokens_input)
        add_usage(totals, present, "output", tokens_output)
        add_usage(totals, present, "cache_read", tokens_cache_read)
        add_usage(totals, present, "cache_write", tokens_cache_write)
        usage = finish_usage(totals, present)

        session = Session(
            adapter=NAME,
            id=str(session_id),
            cwd=str(directory or ""),
            started_at=started,
            last_active_at=last_active,
            state=classify_state(busy=busy, last_active=last_active, now=now),
            model=model,
            last_message="",
            usage=usage,
        )
        if time_archived is not None:
            session.extra["archived"] = True
        return session

