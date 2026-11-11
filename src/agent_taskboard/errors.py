"""Error types for agent-taskboard."""

from __future__ import annotations


class AtbError(Exception):
    """Base class for expected atb errors.

    Every error maps to a process exit code so the CLI can report it without
    a stack trace.  Usage and environment errors use exit code 2 (see
    DESIGN.md section 4); check failures (added in later tasks) use 1.
    """

    exit_code = 2  # usage or environment error


class ConfigError(AtbError):
    """Raised for configuration problems (missing/invalid config.toml, ...)."""


class SheetError(AtbError):
    """Raised for task-sheet parsing/serialization problems."""


class GitError(AtbError):
    """Raised when a git subprocess fails (exit code 2)."""


class CheckError(AtbError):
    """Raised for check/scope/test failures (exit code 1, see DESIGN §4)."""

    exit_code = 1
