"""Per-task notes (``atb note``): an append-only JSONL stream.

Notes live in ``.taskboard/notes/<ID>.jsonl``, one JSON object per line —
``{"timestamp": <UTC ISO 8601>, "by": <name>, "text": <note>}`` — exactly
like ``.taskboard/runs/``: local, untracked and ignored by git. They are the
lightweight "where is this task stuck?" channel: agents and humans append a
sentence, ``atb log`` shows it on the timeline and ``atb review`` shows the
last three.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .errors import AtbError

#: Maximum length of a single note, in characters (no truncation).
NOTE_LIMIT = 4000


def notes_path(repo_root: Path, task_id: str) -> Path:
    """Return the JSONL file that stores ``task_id``'s notes."""
    return repo_root / ".taskboard" / "notes" / f"{task_id}.jsonl"


def validate_text(text: str) -> str:
    """Normalize and validate note text; empty or oversized text raises."""
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")
    if not cleaned.strip():
        raise AtbError("note text must not be empty")
    if len(cleaned) > NOTE_LIMIT:
        raise AtbError(
            f"note text is {len(cleaned)} characters; the limit is "
            f"{NOTE_LIMIT} (nothing was written)"
        )
    return cleaned


def append_note(
    repo_root: Path, task_id: str, text: str, by: str
) -> dict[str, str]:
    """Append one validated note and return the stored record."""
    cleaned = validate_text(text)
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "by": by,
        "text": cleaned,
    }
    path = notes_path(repo_root, task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def load_notes(repo_root: Path, task_id: str) -> list[dict[str, str]]:
    """Return every readable note for ``task_id``, oldest first."""
    path = notes_path(repo_root, task_id)
    if not path.exists():
        return []
    notes: list[dict[str, str]] = []
    try:
        raw_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    for raw in raw_lines:
        line = raw.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        text = record.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        notes.append({
            "timestamp": str(record.get("timestamp") or ""),
            "by": str(record.get("by") or ""),
            "text": text,
        })
    notes.sort(key=lambda note: note["timestamp"])
    return notes
