"""Adapter framework base types: the ``Session`` record and shared helpers.

Adapters are read-only observers over each tool's local session store. They
never write, never take locks (SQLite is opened ``mode=ro``) and fail soft:
anything unexpected raises :class:`AdapterError`, which the CLI reports for
that adapter alone while the others keep working.

State rules (documented here so every adapter agrees):

- ``working``  — a user turn has no assistant/tool reply after it yet, or the
  store explicitly marks the session busy (OpenCode ``time_busy > time_idle``).
- ``waiting``  — the last turn finished (the newest event is the assistant's
  reply) and there was activity within the idle window (30 minutes).
- ``idle``     — no recorded activity for longer than the idle window.
- ``unknown``  — the store carries no timestamps for the phase.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sqlite3  # noqa: F401  (re-exported for adapters)
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Protocol

#: How long a session stays "waiting" after its last activity.
IDLE_WINDOW = timedelta(minutes=30)

#: How much of a big JSONL file an adapter reads (head and tail only).
TAIL_BYTES = 64 * 1024
HEAD_BYTES = 4 * 1024

#: Truncation limit for the last-message summary.
SUMMARY_LIMIT = 120

#: Token-usage keys shared by every adapter. Adapters only include keys the
#: store actually records — never zeros for missing data.
USAGE_KEYS = ("input", "output", "cache_read", "cache_write", "total")

#: Per-file byte budget for token-usage scanning. Usage totals are
#: accumulated by streaming the store (bounded memory); beyond this budget
#: the scan stops, so a monstrous transcript cannot slow review down.
USAGE_SCAN_LIMIT = 64 * 1024 * 1024


def usage_bucket() -> tuple[dict[str, int], set[str]]:
    """Return ``(totals, present_keys)`` for accumulating usage."""
    return {}, set()


def add_usage(
    totals: dict[str, int], present: set[str], key: str, value: object
) -> None:
    """Accumulate one source value; ``None`` means "not recorded"."""
    if value is None:
        return
    totals[key] = totals.get(key, 0) + int(value)
    present.add(key)


def finish_usage(
    totals: dict[str, int], present: set[str], total: int | None = None
) -> dict[str, int] | None:
    """Build the public usage dict: only recorded keys, plus ``total``."""
    if total is None and not present:
        return None
    result = {
        key: totals[key]
        for key in ("input", "output", "cache_read", "cache_write")
        if key in present
    }
    if total is not None:
        result["total"] = int(total)
    elif result:
        result["total"] = sum(result.values())
    return result


class AdapterError(Exception):
    """Raised when an adapter's store cannot be read or understood."""


@dataclass
class Session:
    """One observed agent session, normalised across tools."""

    adapter: str
    id: str
    cwd: str = ""
    branch: str = ""
    started_at: datetime | None = None
    last_active_at: datetime | None = None
    state: str = "unknown"
    model: str = ""
    last_message: str = ""
    usage: dict[str, int] | None = None
    #: Optional lazy usage reader (e.g. claude-code streams a big file only
    #: when somebody actually asks for the tokens); excluded from compare.
    usage_loader: Any = field(default=None, repr=False, compare=False)
    extra: dict[str, Any] = field(default_factory=dict)

    def resolved_usage(self) -> dict[str, int] | None:
        """Return usage, running the lazy loader at most once."""
        if self.usage is None and self.usage_loader is not None:
            try:
                self.usage = self.usage_loader()
            except OSError:
                self.usage = None
        return self.usage


class Adapter(Protocol):
    """The contract every session-store adapter implements."""

    name: str

    def available(self) -> bool:
        """Return whether this adapter's store exists on this machine."""
        ...

    def sessions(self, since: datetime) -> Iterator[Session]:
        """Yield sessions with activity on or after ``since`` (read-only)."""
        ...


# --------------------------------------------------------------------------- #
# helpers shared by the built-in adapters
# --------------------------------------------------------------------------- #

def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_iso(value: Any) -> datetime | None:
    """Parse an ISO timestamp (``Z``-suffixed or offset) into an aware UTC dt."""
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def epoch_ms(value: Any) -> datetime | None:
    """Parse a millisecond unix timestamp into an aware UTC datetime."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(number / 1000, tz=timezone.utc)


def classify_state(
    *,
    busy: bool | None = None,
    last_user_after_reply: bool | None = None,
    last_active: datetime | None,
    now: datetime | None = None,
) -> str:
    """Apply the shared state rules (see the module docstring)."""
    if last_user_after_reply is True or busy is True:
        return "working"
    if last_active is None:
        return "unknown"
    reference = now or utcnow()
    if last_active >= reference - IDLE_WINDOW:
        return "waiting"
    return "idle"


def summarize(text: str, limit: int = SUMMARY_LIMIT) -> str:
    """Collapse whitespace and truncate ``text`` to ``limit`` characters."""
    collapsed = re.sub(r"\s+", " ", (text or "")).strip()
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "…"


#: CCR-style routed model names carry the real model hex-encoded after "-h",
#: e.g. "anthropic/claude-ccr-h7a686970752f676c6d2d352e332d666c617368" is
#: "zhipu-glm-5.3-flash".
_CCR_MODEL_RE = re.compile(r"^(?P<route>.*)-h(?P<hex>[0-9a-f]{8,})$")

#: Long unreadable model ids are truncated to this length for display.
MODEL_DISPLAY_LIMIT = 24


def pretty_model(name: str) -> str:
    """Decode hex-encoded routed model names; truncate anything unreadable."""
    if not name:
        return ""
    match = _CCR_MODEL_RE.match(name)
    if match is not None:
        try:
            decoded = bytes.fromhex(match.group("hex")).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            decoded = ""
        if decoded and all(char.isprintable() for char in decoded):
            return decoded
    if len(name) > MODEL_DISPLAY_LIMIT:
        return name[: MODEL_DISPLAY_LIMIT - 1].rstrip() + "…"
    return name


def text_from_content(content: Any) -> str:
    """Extract displayable text from an OpenAI/Anthropic content value."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if isinstance(block.get("text"), str):
                    parts.append(block["text"])
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return ""


def json_lines(raw: str) -> Iterator[dict]:
    """Yield the JSON objects of a JSONL chunk, skipping broken lines."""
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def _head_plain(path: Path, limit: int) -> str:
    with path.open("rb") as handle:
        return handle.read(limit).decode("utf-8", errors="replace")


def _tail_plain(path: Path, limit: int) -> str:
    size = path.stat().st_size
    with path.open("rb") as handle:
        handle.seek(max(0, size - limit))
        return handle.read().decode("utf-8", errors="replace")


def _stream_decompressed(path: Path) -> "subprocess.Popen[bytes]":
    if shutil.which("zstd") is None:
        raise AdapterError(
            f"{path.name} is zstd-compressed but the zstd binary was not found"
        )
    return subprocess.Popen(  # noqa: S603 - fixed argv, no shell
        ["zstd", "-dc", str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )


def _head_zstd(path: Path, limit: int) -> str:
    proc = _stream_decompressed(path)
    try:
        chunk = proc.stdout.read(limit) if proc.stdout else b""
    finally:
        if proc.stdout is not None:
            proc.stdout.close()
        proc.kill()
        proc.wait()
    return chunk.decode("utf-8", errors="replace")


def _tail_zstd(path: Path, limit: int) -> str:
    proc = _stream_decompressed(path)
    window = b""
    try:
        assert proc.stdout is not None
        while True:
            chunk = proc.stdout.read(64 * 1024)
            if not chunk:
                break
            window = (window + chunk)[-limit:]
    finally:
        if proc.stdout is not None:
            proc.stdout.close()
        proc.wait()
    return window.decode("utf-8", errors="replace")


def read_head(path: Path, limit: int = HEAD_BYTES) -> str:
    """Read the first ``limit`` bytes; handles plain and zstd-compressed files.

    Only a small chunk is held in memory, and the decompressor process is
    killed as soon as the chunk is read.
    """
    if path.suffix == ".zst":
        return _head_zstd(path, limit)
    return _head_plain(path, limit)


def read_tail(path: Path, limit: int = TAIL_BYTES) -> str:
    """Read the last ``limit`` bytes; handles plain and zstd-compressed files.

    Decompression streams the whole file through a pipe but keeps only the
    rolling ``limit`` window in memory.
    """
    if path.suffix == ".zst":
        return _tail_zstd(path, limit)
    return _tail_plain(path, limit)


def _root(primary: str | os.PathLike, *fallbacks: str | os.PathLike) -> Path:
    """First existing directory among ``primary`` and ``fallbacks``."""
    for candidate in (primary, *fallbacks):
        if Path(candidate).is_dir():
            return Path(candidate)
    return Path(primary)


def home() -> Path:
    """The user home (``$HOME`` / ``%USERPROFILE%``)."""
    return Path(os.environ.get("HOME") or Path.home())
