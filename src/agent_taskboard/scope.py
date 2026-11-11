"""Allow/deny path matching for task scope (pure functions).

Paths are always repo-relative and use ``/`` as the separator.  Patterns use
gitignore-style globs where ``*`` never crosses a ``/``, ``**`` matches zero or
more directory levels, and ``?`` matches a single character.
"""

from __future__ import annotations

import re
from typing import Iterable


def _match_segment(segment: str, pattern: str) -> bool:
    """Match a single path segment (no ``/``) against a segment pattern."""
    regex = []
    for char in pattern:
        if char == "*":
            regex.append(".*")
        elif char == "?":
            regex.append(".")
        else:
            regex.append(re.escape(char))
    return re.fullmatch("".join(regex), segment) is not None


def _match_parts(path_parts: list[str], pattern_parts: list[str]) -> bool:
    """Recursively match path segments against pattern segments."""
    if not pattern_parts:
        return not path_parts
    head, *tail = pattern_parts
    if head == "**":
        # ``**`` matches zero or more directory levels.
        if _match_parts(path_parts, tail):
            return True
        if path_parts and _match_parts(path_parts[1:], pattern_parts):
            return True
        return False
    if not path_parts:
        return False
    if _match_segment(path_parts[0], head):
        return _match_parts(path_parts[1:], tail)
    return False


def match(path: str, pattern: str) -> bool:
    """Return whether repo-relative ``path`` matches glob ``pattern``."""
    path = path.strip("/")
    pattern = pattern.strip("/")
    path_parts = path.split("/") if path else []
    pattern_parts = pattern.split("/") if pattern else []
    return _match_parts(path_parts, pattern_parts)


def classify(
    paths: Iterable[str],
    allow: Iterable[str] = (),
    deny: Iterable[str] = (),
) -> dict[str, str]:
    """Classify each path as ``"ok"``, ``"denied"`` or ``"outside"``.

    ``deny`` wins over ``allow``; an empty ``allow`` means "everything that is
    not denied is allowed".
    """
    allow_list = list(allow)
    deny_list = list(deny)
    result: dict[str, str] = {}
    for path in paths:
        if any(match(path, pattern) for pattern in deny_list):
            result[path] = "denied"
        elif not allow_list or any(match(path, pattern) for pattern in allow_list):
            result[path] = "ok"
        else:
            result[path] = "outside"
    return result
