"""Task-sheet parsing and serialization (TOML header + Markdown body).

A task sheet looks like::

    +++
    id = "T001"
    title = "Skeleton"
    status = "todo"
    +++

    ## 背景
    ...

The ``+++`` fence encloses a small TOML table; everything after the closing
fence is the Markdown body.  ``tomllib`` can only read TOML, so serialization
is done with a small hand-written TOML writer that supports the value types a
task sheet can contain.

Parsing accepts both LF and CRLF line endings (a leading UTF-8 BOM is also
tolerated); ``dump`` *always* emits LF (``\\n``) regardless of the input, and
never emits a BOM.  Unknown header keys are preserved: keys that are not a
bare ``[A-Za-z0-9_-]+`` token are quoted, and nested dict values are written
as inline tables so the sheet survives a parse/dump round trip.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .errors import SheetError

#: The six statuses atb understands (DESIGN.md section 3).
STATUSES = ("todo", "doing", "review", "rework", "done", "dropped")

#: Fixed output order for the well-known header fields.
HEADER_FIELDS = (
    "id",
    "title",
    "agent",
    "status",
    "base",
    "branch",
    "allow",
    "deny",
    "depends",
    "test",
    "created",
)

#: Body used by ``atb new``.  Must contain the three sections; the ``## 验收``
#: section carries two unchecked placeholders.
TEMPLATE_BODY = (
    "\n"
    "## 背景\n"
    "\n"
    "（说明为什么要做这个任务。）\n"
    "\n"
    "## 要做的事\n"
    "\n"
    "（列出具体步骤。）\n"
    "\n"
    "## 验收\n"
    "\n"
    "- [ ] \n"
    "- [ ] \n"
)


@dataclass
class Sheet:
    """A parsed task sheet."""

    id: str
    title: str
    status: str
    agent: str = ""
    base: str = ""
    branch: str = ""
    allow: list[str] = field(default_factory=list)
    deny: list[str] = field(default_factory=list)
    depends: list[str] = field(default_factory=list)
    test: str = ""
    created: datetime | str | None = None
    body: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #

def _split(text: str) -> tuple[str, str]:
    """Split a sheet into ``(toml_header, body)``.

    Accepts LF and CRLF line endings (so ``+++\\r`` is a valid closing line)
    and strips a leading UTF-8 BOM.  The returned header and body are
    normalized to LF.  ``dump`` always writes LF, so parsing is stable across
    a parse/dump round trip even for CRLF or BOM-prefixed input.
    """
    if text.startswith("\ufeff"):
        text = text[len("\ufeff"):]
    if not text.startswith("+++"):
        raise SheetError("task sheet must begin with a '+++' header line")
    # Split on \n but treat a trailing \r on each line as part of the CRLF
    # pair, so ``+++\\r`` counts as a closing ``+++`` line.  Using split("\n")
    # (rather than splitlines()) keeps the body's trailing newline intact.
    lines = [line[:-1] if line.endswith("\r") else line for line in text.split("\n")]
    close: int | None = None
    for index in range(1, len(lines)):
        if lines[index] == "+++":
            close = index
            break
    if close is None:
        raise SheetError("unterminated '+++' header: no closing '+++' line found")
    header = "\n".join(lines[1:close])
    body = "\n".join(lines[close + 1:])
    return header, body


def parse(text: str) -> Sheet:
    """Parse task-sheet ``text`` into a :class:`Sheet`."""
    header_text, body = _split(text)
    try:
        data = tomllib.loads(header_text)
    except tomllib.TOMLDecodeError as exc:
        raise SheetError(f"invalid TOML header: {exc}") from exc
    if not isinstance(data, dict):
        raise SheetError("task sheet header must be a TOML table")

    missing = [key for key in ("id", "title", "status") if key not in data]
    if missing:
        raise SheetError("missing required field(s): " + ", ".join(missing))

    for key in ("id", "title", "status", "agent", "base", "branch", "test"):
        if key in data and not isinstance(data[key], str):
            raise SheetError(
                f"field {key!r} must be a string, got {type(data[key]).__name__}"
            )

    status = data["status"]
    if status not in STATUSES:
        raise SheetError(
            f"invalid status {status!r}; must be one of {list(STATUSES)}"
        )

    def list_field(key: str) -> list[str]:
        value = data.get(key)
        if value is None:
            return []
        if not isinstance(value, list):
            raise SheetError(
                f"field {key!r} must be an array of strings, got {type(value).__name__}"
            )
        for item in value:
            if not isinstance(item, str):
                raise SheetError(
                    f"field {key!r} must contain only strings, got {type(item).__name__}"
                )
        return list(value)

    created = data.get("created")
    if created is not None and not isinstance(created, (datetime, str)):
        raise SheetError(
            f"field 'created' must be a datetime or string, got {type(created).__name__}"
        )

    known = set(HEADER_FIELDS)
    extra = {key: value for key, value in data.items() if key not in known}

    return Sheet(
        id=data["id"],
        title=data["title"],
        status=status,
        agent=data.get("agent", ""),
        base=data.get("base", ""),
        branch=data.get("branch", ""),
        allow=list_field("allow"),
        deny=list_field("deny"),
        depends=list_field("depends"),
        test=data.get("test", ""),
        created=created,
        body=body,
        extra=extra,
    )


# --------------------------------------------------------------------------- #
# Serialization
# --------------------------------------------------------------------------- #

def _format_string(value: str) -> str:
    """Quote a string as a single-line TOML basic string."""
    out = ['"']
    for char in value:
        code = ord(char)
        if char == '"':
            out.append('\\"')
        elif char == "\\":
            out.append("\\\\")
        elif char == "\n":
            out.append("\\n")
        elif char == "\r":
            out.append("\\r")
        elif char == "\t":
            out.append("\\t")
        elif char == "\b":
            out.append("\\b")
        elif char == "\f":
            out.append("\\f")
        elif code < 0x20 or code == 0x7F:
            out.append("\\u%04X" % code)
        else:
            out.append(char)
    out.append('"')
    return "".join(out)


def _format_datetime(value: datetime) -> str:
    """Format a datetime as a TOML offset (or local) date-time."""
    if value.tzinfo is None:
        return value.isoformat()
    if value.utcoffset() == timedelta(0):
        return value.replace(tzinfo=None).isoformat() + "Z"
    return value.isoformat()


def _format_value(value: Any) -> str:
    """Serialize a single TOML value (string, bool, number, datetime, array)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return _format_string(value)
    if isinstance(value, datetime):
        return _format_datetime(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise SheetError("cannot serialize a non-finite float into a TOML header")
        return repr(value)
    if isinstance(value, dict):
        return "{" + ", ".join(
            f"{_format_key(key)} = {_format_value(item)}"
            for key, item in value.items()
        ) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_format_value(item) for item in value) + "]"
    raise SheetError(f"unsupported value type for TOML header: {type(value).__name__}")


_BARE_KEY_RE = re.compile(r"[A-Za-z0-9_-]+")


def _format_key(key: str) -> str:
    """Format a TOML key: bare when ``[A-Za-z0-9_-]+``, otherwise quoted."""
    if _BARE_KEY_RE.fullmatch(key):
        return key
    return _format_string(key)


def dump(sheet: Sheet) -> str:
    """Serialize ``sheet`` back to text so that ``parse(dump(x)) == x``.

    The output always uses LF (``\\n``) line endings, never CRLF, and never
    includes a BOM, even if the original input had either.
    """
    parts = ["+++"]
    for key in HEADER_FIELDS:
        value = getattr(sheet, key)
        if value is None:
            continue
        parts.append(f"{key} = {_format_value(value)}")
    for key, value in sheet.extra.items():
        parts.append(f"{_format_key(key)} = {_format_value(value)}")
    parts.append("+++")
    return "\n".join(parts) + "\n" + sheet.body


def to_dict(sheet: Sheet) -> dict[str, Any]:
    """Flatten a sheet into a JSON-friendly dict (stable key order)."""
    result: dict[str, Any] = {}
    for key in HEADER_FIELDS:
        value = getattr(sheet, key)
        if value is None:
            continue
        if isinstance(value, datetime):
            value = _format_datetime(value)
        elif isinstance(value, list):
            value = list(value)
        result[key] = value
    for key, value in sheet.extra.items():
        if isinstance(value, datetime):
            value = _format_datetime(value)
        result[key] = value
    result["body"] = sheet.body
    return result


# --------------------------------------------------------------------------- #
# Helpers for the CLI
# --------------------------------------------------------------------------- #

def next_id(tasks_dir: str | Path, prefix: str = "T") -> str:
    """Return the next free task id such as ``T001``, ``T002``, ..."""
    tasks_dir = Path(tasks_dir)
    taken: set[str] = set()
    if tasks_dir.is_dir():
        for path in tasks_dir.iterdir():
            if path.is_file() and path.suffix == ".md":
                taken.add(path.stem)
    number = 1
    while True:
        candidate = f"{prefix}{number:03d}"
        if candidate not in taken:
            return candidate
        number += 1


def slugify(title: str, fallback: str = "") -> str:
    """Turn ``title`` into a branch-name slug.

    Lowercased, only ``[a-z0-9-]`` survive, capped at 40 characters.  If the
    result is empty (e.g. a Chinese-only title), fall back to the task id in
    lower case (``t003``).
    """
    slug = re.sub(r"[^a-z0-9-]+", "-", title.lower())
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    if not slug:
        return fallback.lower()
    return slug[:40].rstrip("-")


# --------------------------------------------------------------------------- #
# Markdown body helpers
# --------------------------------------------------------------------------- #

def extract_section(body: str, heading: str) -> str:
    """Return the Markdown section starting at ``heading`` (e.g. ``## 验收``).

    The section runs from the heading line up to (but not including) the next
    ``## `` heading.  Returns ``""`` when the heading is absent.
    """
    lines = body.split("\n")
    start: int | None = None
    for index, line in enumerate(lines):
        if line == heading or line.startswith(heading + " "):
            start = index
            break
    if start is None:
        return ""
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if lines[index].startswith("## "):
            end = index
            break
    return "\n".join(lines[start:end])


def _find_heading(lines: list[str], heading: str) -> int | None:
    for index, line in enumerate(lines):
        if line == heading or line.startswith(heading + " "):
            return index
    return None


def _note_lines(timestamp: str, note: str) -> list[str]:
    """Render a rework note as a Markdown list item (multi-line friendly)."""
    text_lines = note.split("\n")
    out = [f"- [{timestamp}] {text_lines[0]}"]
    for line in text_lines[1:]:
        out.append(f"  {line}" if line.strip() else "")
    return out


def append_rework_note(body: str, note: str, timestamp: str) -> str:
    """Append a timestamped note to the ``## 返工`` section, creating it if needed.

    The section is kept at the end of the body so the prompt (which hoists it to
    the top) keeps working; repeated calls accumulate notes in order.
    """
    heading = "## 返工"
    lines = body.split("\n")
    heading_index = _find_heading(lines, heading)

    if heading_index is None:
        block = [heading, ""] + _note_lines(timestamp, note)
        return body.rstrip("\n") + "\n\n" + "\n".join(block) + "\n"

    end = len(lines)
    for index in range(heading_index + 1, len(lines)):
        if lines[index].startswith("## "):
            end = index
            break
    last_content = heading_index
    for index in range(heading_index + 1, end):
        if lines[index].strip():
            last_content = index
    for offset, line in enumerate(_note_lines(timestamp, note)):
        lines.insert(last_content + 1 + offset, line)
    return "\n".join(lines)
