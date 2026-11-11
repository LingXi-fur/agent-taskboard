"""Repository-level configuration for agent-taskboard.

Locates the repository root with ``git rev-parse --show-toplevel`` and reads
``.taskboard/config.toml``, filling in defaults for missing keys and
preserving unknown keys without complaining.
"""

from __future__ import annotations

import os
import re
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import ConfigError

#: Default values for the known configuration keys (DESIGN.md section 3).
DEFAULTS: dict[str, Any] = {
    "target": "main",
    "worktree_root": "../{repo}-tasks",
    "branch_pattern": "{agent}/{slug}",
    "test": "python3 -m unittest discover -s tests",
    "id_prefix": "T",
    "test_timeout": 1800,
}

#: The known keys and the type each one must have.
TYPES: dict[str, type] = {
    "target": str,
    "worktree_root": str,
    "branch_pattern": str,
    "test": str,
    "id_prefix": str,
    "test_timeout": int,
}

#: Text written by ``atb init`` when no config exists yet.
DEFAULT_CONFIG_TOML = """\
target = "main"
worktree_root = "../{repo}-tasks"
branch_pattern = "{agent}/{slug}"
test = "python3 -m unittest discover -s tests"
id_prefix = "T"
test_timeout = 1800
"""


def find_repo_root(start: str | os.PathLike | None = None) -> Path:
    """Return the absolute path of the enclosing repository's root.

    Uses ``git rev-parse --show-toplevel``, so inside a linked worktree it
    returns that worktree's own root, not the main worktree's.
    """
    if start is None:
        start = os.getcwd()
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(start),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError as exc:  # git is not on PATH
        raise ConfigError("git executable not found on PATH") from exc
    if proc.returncode != 0:
        detail = proc.stderr.strip() or "not a git repository"
        raise ConfigError(f"could not locate repository root: {detail}")
    return Path(proc.stdout.strip()).resolve()


@dataclass
class Config:
    """Loaded repository configuration.

    Known keys are exposed as attributes; unknown keys are kept in
    :attr:`extra` so they survive a round trip.
    """

    target: str = DEFAULTS["target"]
    worktree_root: str = DEFAULTS["worktree_root"]
    branch_pattern: str = DEFAULTS["branch_pattern"]
    test: str = DEFAULTS["test"]
    id_prefix: str = DEFAULTS["id_prefix"]
    test_timeout: int = DEFAULTS["test_timeout"]
    extra: dict[str, Any] = field(default_factory=dict)
    repo_root: Path = field(default_factory=Path)
    config_path: Path = field(default_factory=Path)


def load_config(repo_root: str | os.PathLike) -> Config:
    """Read ``<repo_root>/.taskboard/config.toml`` with defaults filled in."""
    repo_root = Path(repo_root)
    config_path = repo_root / ".taskboard" / "config.toml"

    data: dict[str, Any] = {}
    if config_path.exists():
        try:
            with config_path.open("rb") as handle:
                data = tomllib.load(handle)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"invalid config file {config_path}: {exc}") from exc

    merged: dict[str, Any] = dict(DEFAULTS)
    extra: dict[str, Any] = {}
    for key, value in data.items():
        if key in TYPES:
            expected = TYPES[key]
            if not isinstance(value, expected):
                raise ConfigError(
                    f"config key {key!r} must be a {expected.__name__}, "
                    f"got {type(value).__name__}"
                )
            merged[key] = value
        else:
            extra[key] = value  # unknown keys are preserved, not rejected

    return Config(
        target=merged["target"],
        worktree_root=merged["worktree_root"],
        branch_pattern=merged["branch_pattern"],
        test=merged["test"],
        id_prefix=merged["id_prefix"],
        test_timeout=merged["test_timeout"],
        extra=extra,
        repo_root=repo_root,
        config_path=config_path,
    )


def resolve_worktree_root(
    config: Config, repo_root: str | os.PathLike | None = None
) -> Path:
    """Resolve ``config.worktree_root`` to an absolute path.

    ``{repo}`` is replaced with the repository directory name; relative paths
    are interpreted against the repository root.
    """
    root = Path(repo_root) if repo_root is not None else config.repo_root
    raw = config.worktree_root.replace("{repo}", root.name)
    path = Path(raw)
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def _parse_git_version(output: str) -> tuple[int, int] | None:
    """Extract a ``(major, minor)`` version tuple from ``git --version``."""
    match = re.search(r"(\d+)\.(\d+)", output)
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)))


def git_version() -> tuple[int, int] | None:
    """Return the installed git ``(major, minor)`` version, or ``None``."""
    try:
        proc = subprocess.run(
            ["git", "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError:
        return None
    if proc.returncode != 0:
        return None
    return _parse_git_version(proc.stdout)
