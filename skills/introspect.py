"""Introspection skill — lets JARVIS read and search its own source code."""

from __future__ import annotations

import logging
from pathlib import Path

from core.skill_base import BaseSkill

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MAX_OUTPUT = 6000

_SKIP_DIRS = {
    "__pycache__", ".git", "node_modules", ".venv", "venv",
    "data", "whatsapp", ".mypy_cache", ".pytest_cache",
}
_SKIP_SUFFIXES = {".pyc", ".pyo", ".db", ".db-journal", ".blob", ".bin", ".exe"}


def _is_safe(path: Path) -> bool:
    """Only allow files inside the project root, skip junk."""
    try:
        path.resolve().relative_to(PROJECT_ROOT)
    except ValueError:
        return False
    return not any(part in _SKIP_DIRS for part in path.parts)


class IntrospectSkill(BaseSkill):
    name = "introspect"
    description = (
        "Read and search JARVIS's own source code. "
        "Use introspect_tree to see project structure, "
        "introspect_read to read a file, "
        "introspect_search to find text across the codebase."
    )

    RISK_MAP = {
        "tree": "low",
        "read": "low",
        "search": "low",
    }

    async def execute(self, action: str, params: dict | None = None) -> dict:
        method = getattr(self, f"do_{action}", None)
        if not method:
            return {"error": f"Unknown action: {action}"}
        try:
            return await method(**(params or {}))
        except Exception as e:
            logger.exception("introspect.%s failed", action)
            return {"error": str(e)}

    async def do_tree(self, path: str = "", depth: int = 2) -> dict:
        """List project file tree. path is relative to project root (e.g. 'core' or 'skills'). depth controls how deep to recurse (default 2)."""
        depth = min(int(depth), 4)
        base = PROJECT_ROOT / path
        if not base.is_dir() or not _is_safe(base):
            return {"error": f"Not a valid project directory: {path}"}

        lines: list[str] = []

        def _walk(p: Path, indent: int, remaining: int):
            if remaining < 0:
                return
            try:
                entries = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name))
            except PermissionError:
                return
            for entry in entries:
                name = entry.name
                if name.startswith(".") and name not in (".env.example",):
                    continue
                if entry.is_dir():
                    if name in _SKIP_DIRS:
                        continue
                    lines.append(f"{'  ' * indent}{name}/")
                    _walk(entry, indent + 1, remaining - 1)
                else:
                    if entry.suffix in _SKIP_SUFFIXES:
                        continue
                    lines.append(f"{'  ' * indent}{name}")

        _walk(base, 0, depth)
        tree_text = "\n".join(lines[:200])
        if len(lines) > 200:
            tree_text += f"\n... ({len(lines) - 200} more entries)"
        return {"status": "ok", "tree": tree_text}

    async def do_read(self, file: str) -> dict:
        """Read a source file from the project. file is relative to project root (e.g. 'core/memory.py')."""
        target = (PROJECT_ROOT / file).resolve()
        if not _is_safe(target) or not target.is_file():
            return {"error": f"Cannot read: {file}"}
        if target.suffix in _SKIP_SUFFIXES:
            return {"error": "Binary file, skipping"}
        try:
            content = target.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return {"error": str(e)}
        if len(content) > MAX_OUTPUT:
            content = content[:MAX_OUTPUT] + f"\n\n... (truncated, {len(content)} chars total)"
        return {"status": "ok", "file": file, "lines": content.count("\n") + 1, "content": content}

    async def do_search(self, pattern: str, path: str = "") -> dict:
        """Search for a text pattern across project Python files. Returns matching lines with file paths. path narrows the search (e.g. 'core')."""
        pattern_lower = pattern.lower()
        base = PROJECT_ROOT / path
        if not base.exists() or not _is_safe(base):
            return {"error": f"Invalid search path: {path}"}

        matches: list[str] = []
        glob_target = base if base.is_dir() else base.parent
        for py_file in sorted(glob_target.rglob("*.py")):
            if not _is_safe(py_file):
                continue
            try:
                text = py_file.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            rel = py_file.relative_to(PROJECT_ROOT)
            for i, line in enumerate(text.splitlines(), 1):
                if pattern_lower in line.lower():
                    matches.append(f"{rel}:{i}: {line.rstrip()}")
                    if len(matches) >= 50:
                        break
            if len(matches) >= 50:
                break

        if not matches:
            return {"status": "empty", "reply_to_user_hebrew": f"לא מצאתי '{pattern}' בקוד."}
        return {"status": "ok", "count": len(matches), "matches": "\n".join(matches)}
