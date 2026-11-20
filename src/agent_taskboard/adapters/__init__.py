"""Session-store adapters: read-only observers over local agent stores.

The built-in adapters cover Claude Code, Codex, Gemini CLI and OpenCode.
Third parties register additional adapters under the
``agent_taskboard.adapters`` entry-point group::

    [project.entry-points."agent_taskboard.adapters"]
    my-tool = "my_pkg.module:MyAdapter"

:func:`load_adapters` returns the built-ins plus every registered adapter
whose module can be imported; a broken registration is reported through
:attr:`LoaderState.errors` and never raises.
"""

from __future__ import annotations

import importlib.metadata
from dataclasses import dataclass, field
from typing import Any

from .base import Adapter, AdapterError, Session  # noqa: F401 (re-export)

ENTRY_POINT_GROUP = "agent_taskboard.adapters"


@dataclass
class LoaderState:
    """The adapters plus anything that failed while loading them."""

    adapters: list[Any] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)


def _builtins() -> list[Any]:
    # imported lazily so the base types never circle back through __init__
    from .claude_code import ClaudeCodeAdapter
    from .codex import CodexAdapter
    from .gemini_cli import GeminiCliAdapter
    from .opencode import OpenCodeAdapter

    return [
        ClaudeCodeAdapter(),
        CodexAdapter(),
        GeminiCliAdapter(),
        OpenCodeAdapter(),
    ]


def load_adapters() -> LoaderState:
    """Return the built-in adapters and any entry-point registered ones."""
    state = LoaderState(adapters=_builtins())

    try:
        entries = importlib.metadata.entry_points(group=ENTRY_POINT_GROUP)
    except Exception:  # pragma: no cover - packaging edge cases
        return state
    for entry in entries:
        try:
            factory = entry.load()
            adapter = factory() if callable(factory) else factory
            if not hasattr(adapter, "name"):
                raise TypeError("adapter must have a `name` attribute")
            if not hasattr(adapter, "available") or not hasattr(
                adapter, "sessions"
            ):
                raise TypeError("adapter must implement the Adapter protocol")
            state.adapters.append(adapter)
        except Exception as exc:
            state.errors.append(
                {"adapter": entry.name, "error": f"{type(exc).__name__}: {exc}"}
            )
    return state
