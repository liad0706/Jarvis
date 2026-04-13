"""Development auto-reload helpers for Jarvis."""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_WATCH_DIRS = (
    "config",
    "core",
    "skills",
    "dashboard",
    "integrations",
    "voice",
    "whatsapp",
)
DEFAULT_WATCH_FILES = (
    ".env",
    "main.py",
    "whatsapp_server.py",
    "requirements.txt",
)
DEFAULT_SUFFIXES = {
    ".css",
    ".env",
    ".html",
    ".js",
    ".json",
    ".mjs",
    ".py",
}
IGNORED_DIRS = {
    "__pycache__",
    ".git",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "node_modules",
    "venv",
    "data",
    "memory",
}


class AutoReloadWatcher:
    """Simple polling watcher that tracks source files relevant to Jarvis."""

    def __init__(
        self,
        root: str | Path,
        watch_dirs: tuple[str, ...] = DEFAULT_WATCH_DIRS,
        watch_files: tuple[str, ...] = DEFAULT_WATCH_FILES,
        suffixes: set[str] | None = None,
    ):
        self.root = Path(root).resolve()
        self.watch_dirs = tuple(watch_dirs)
        self.watch_files = tuple(watch_files)
        self.suffixes = {s.lower() for s in (suffixes or DEFAULT_SUFFIXES)}
        self._snapshot: dict[str, tuple[int, int]] = {}

    def prime(self) -> None:
        self._snapshot = self._build_snapshot()

    def scan_changes(self) -> list[Path]:
        """Return changed/new/deleted files since the last snapshot."""
        current = self._build_snapshot()
        changed: set[str] = set()
        for path_str, stat_sig in current.items():
            if self._snapshot.get(path_str) != stat_sig:
                changed.add(path_str)
        for path_str in self._snapshot:
            if path_str not in current:
                changed.add(path_str)
        self._snapshot = current
        return [Path(path_str) for path_str in sorted(changed)]

    def _build_snapshot(self) -> dict[str, tuple[int, int]]:
        snapshot: dict[str, tuple[int, int]] = {}
        for path in self._iter_files():
            try:
                stat = path.stat()
            except OSError:
                continue
            snapshot[str(path)] = (stat.st_mtime_ns, stat.st_size)
        return snapshot

    def _iter_files(self):
        for rel in self.watch_dirs:
            target = (self.root / rel).resolve()
            if not target.exists():
                continue
            if target.is_file():
                if self._should_watch(target):
                    yield target
                continue
            for dirpath, dirnames, filenames in os.walk(target):
                dirnames[:] = [name for name in dirnames if name not in IGNORED_DIRS]
                for filename in filenames:
                    path = Path(dirpath) / filename
                    if self._should_watch(path):
                        yield path

        for rel in self.watch_files:
            path = (self.root / rel).resolve()
            if path.is_file() and self._should_watch(path):
                yield path

    def _should_watch(self, path: Path) -> bool:
        try:
            path.relative_to(self.root)
        except ValueError:
            return False
        if any(part in IGNORED_DIRS for part in path.parts):
            return False
        if path.name in {".env"}:
            return True
        return path.suffix.lower() in self.suffixes


def summarize_changed_files(paths: list[str | Path], root: str | Path, limit: int = 3) -> str:
    root_path = Path(root).resolve()
    rels: list[str] = []
    for raw in paths:
        path = Path(raw)
        try:
            rels.append(path.resolve().relative_to(root_path).as_posix())
        except (OSError, ValueError):
            rels.append(path.name or str(path))
    rels = sorted(dict.fromkeys(rels))
    if not rels:
        return "unknown changes"
    shown = rels[:limit]
    extra = len(rels) - len(shown)
    summary = ", ".join(shown)
    if extra > 0:
        summary += f", +{extra} more"
    return summary


def build_restart_reason(paths: list[str | Path], root: str | Path) -> str:
    return f"Auto-reload after code changes: {summarize_changed_files(paths, root)}"


def build_resume_message(paths: list[str | Path], root: str | Path) -> str:
    return f"Reloaded with fresh code: {summarize_changed_files(paths, root)}"
