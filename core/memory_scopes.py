"""Memory scopes - user / project / local.

Provides scope-aware memory directories so different types of memory are kept
in the right place:
- user:    Global to the user (~/.jarvis/memory/) - preferences, profile
- project: Project-specific (Jarvis/memory/) - local project notes, not versioned by default
- local:   Ephemeral project-local (Jarvis/data/local_memory/) - session scratch, not versioned
"""

from __future__ import annotations

import logging
import re
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class MemoryScope(str, Enum):
    USER = "user"
    PROJECT = "project"
    LOCAL = "local"


def _sanitize_for_path(name: str) -> str:
    """Replace non-alphanumeric chars with dashes (safe on Windows)."""
    return re.sub(r"[^a-zA-Z0-9_-]", "-", name).strip("-")[:80]


def get_user_memory_dir() -> Path:
    """Global user memory - survives across projects."""
    home = Path.home() / ".jarvis" / "memory"
    home.mkdir(parents=True, exist_ok=True)
    return home


def get_project_memory_dir() -> Path:
    """Project-scoped memory - local to this checkout unless you export it yourself."""
    d = PROJECT_ROOT / "memory"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_local_memory_dir() -> Path:
    """Ephemeral local memory - not versioned, session scratch."""
    d = PROJECT_ROOT / "data" / "local_memory"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_scoped_memory_dir(scope: MemoryScope) -> Path:
    """Return the memory directory for a given scope."""
    if scope == MemoryScope.USER:
        return get_user_memory_dir()
    if scope == MemoryScope.PROJECT:
        return get_project_memory_dir()
    return get_local_memory_dir()


def get_skill_memory_dir(skill_name: str, scope: MemoryScope = MemoryScope.PROJECT) -> Path:
    """Per-skill memory subdirectory within a scope."""
    safe_name = _sanitize_for_path(skill_name)
    d = get_scoped_memory_dir(scope) / "skills" / safe_name
    d.mkdir(parents=True, exist_ok=True)
    return d


def classify_memory_scope(key: str) -> MemoryScope:
    """Heuristic: classify a memory key into its most appropriate scope.

    - Keys starting with 'preference:', 'user:' -> USER
    - Keys starting with 'session:', 'temp:', 'scratch:' -> LOCAL
    - Everything else -> PROJECT
    """
    lower = key.lower()
    if lower.startswith(("preference:", "user:", "profile:")):
        return MemoryScope.USER
    if lower.startswith(("session:", "temp:", "scratch:", "ephemeral:")):
        return MemoryScope.LOCAL
    return MemoryScope.PROJECT


def save_scoped_memory(key: str, content: str, scope: MemoryScope | None = None) -> Path:
    """Save a memory file to the appropriate scope directory."""
    if scope is None:
        scope = classify_memory_scope(key)
    directory = get_scoped_memory_dir(scope)
    safe_key = _sanitize_for_path(key) + ".md"
    fpath = directory / safe_key
    fpath.write_text(content, encoding="utf-8")
    logger.info("Memory saved: %s -> %s (%s scope)", key, fpath, scope.value)
    return fpath


def load_scoped_memory(key: str, scope: MemoryScope | None = None) -> str | None:
    """Load a memory file from the appropriate scope directory."""
    if scope is None:
        scope = classify_memory_scope(key)
    directory = get_scoped_memory_dir(scope)
    safe_key = _sanitize_for_path(key) + ".md"
    fpath = directory / safe_key
    if fpath.exists():
        return fpath.read_text(encoding="utf-8")
    return None


def list_scoped_memories(scope: MemoryScope) -> list[str]:
    """List all memory files in a scope."""
    directory = get_scoped_memory_dir(scope)
    return [f.stem for f in sorted(directory.glob("*.md"))]
