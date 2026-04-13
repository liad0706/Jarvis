"""File manager skill -- search, analyze, and organize files on the local system.

ניהול קבצים -- חיפוש, ניתוח וארגון קבצים במערכת המקומית.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from core.skill_base import BaseSkill

logger = logging.getLogger(__name__)

# Safety limits
MAX_SEARCH_RESULTS = 100
MAX_DUPLICATE_SCAN_FILES = 10_000
MAX_HASH_SIZE = 500 * 1024 * 1024  # 500 MB -- skip huge files when hashing

SKIP_DIRS = {
    "node_modules", ".gradle", ".git", "__pycache__", ".venv",
    "venv", ".cache", ".npm", ".nuget", "AppData", ".android",
    "dist", "build", ".tox", ".mypy_cache", ".pytest_cache",
    "$Recycle.Bin", "System Volume Information",
}

# File type categories for organizing
FILE_CATEGORIES = {
    "images": {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".svg", ".webp", ".ico", ".tiff", ".heic", ".raw"},
    "documents": {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods", ".odp", ".rtf", ".txt", ".csv"},
    "videos": {".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".mpg", ".mpeg"},
    "audio": {".mp3", ".wav", ".flac", ".aac", ".ogg", ".wma", ".m4a", ".opus"},
    "archives": {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".iso"},
    "code": {".py", ".js", ".ts", ".html", ".css", ".java", ".c", ".cpp", ".h", ".cs", ".go", ".rs", ".rb", ".php", ".sh", ".bat", ".ps1", ".json", ".xml", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".md"},
    "executables": {".exe", ".msi", ".bat", ".cmd", ".com", ".scr"},
}


def _categorize_file(suffix: str) -> str:
    """Return the category name for a file extension."""
    suffix = suffix.lower()
    for category, extensions in FILE_CATEGORIES.items():
        if suffix in extensions:
            return category
    return "other"


def _human_size(size_bytes: int) -> str:
    """Convert bytes to human-readable string."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(size_bytes) < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"


def _should_skip(path: Path) -> bool:
    """Check if a path should be skipped."""
    return any(part in SKIP_DIRS for part in path.parts)


class FileManagerSkill(BaseSkill):
    """Search, inspect, and organize files on the local system."""

    name = "file_manager"
    description = (
        "Search for files, get file info, find duplicates, "
        "organize downloads, and watch folders for changes."
    )

    RISK_MAP = {
        "search_files": "low",
        "file_info": "low",
        "find_duplicates": "low",
        "organize_downloads": "low",
        "watch_folder": "low",
    }

    async def execute(self, action: str, params: dict | None = None) -> dict:
        method = getattr(self, f"do_{action}", None)
        if not method:
            return {"error": f"Unknown action: {action}"}
        try:
            return await method(**(params or {}))
        except Exception as e:
            logger.exception("file_manager.%s failed", action)
            return {"error": str(e)}

    # ------------------------------------------------------------------
    # actions
    # ------------------------------------------------------------------

    async def do_search_files(
        self, query: str, directory: str = "", extensions: str = ""
    ) -> dict:
        """Search for files by name pattern using glob. directory defaults to user's home. extensions is comma-separated like 'py,txt,pdf'. חיפוש קבצים לפי שם."""
        base = Path(directory) if directory else Path.home()
        if not base.exists():
            return {"error": f"Directory not found: {base}"}

        ext_filter = set()
        if extensions:
            ext_filter = {
                f".{e.strip().lstrip('.')}" for e in extensions.split(",") if e.strip()
            }

        pattern = f"*{query}*" if "*" not in query else query

        def _search():
            results = []
            try:
                for f in base.rglob(pattern):
                    if len(results) >= MAX_SEARCH_RESULTS:
                        break
                    try:
                        if _should_skip(f):
                            continue
                        if ext_filter and f.suffix.lower() not in ext_filter:
                            continue
                        stat = f.stat()
                        results.append({
                            "path": str(f),
                            "name": f.name,
                            "size": _human_size(stat.st_size),
                            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                            "is_dir": f.is_dir(),
                        })
                    except (PermissionError, FileNotFoundError, OSError):
                        continue
            except (PermissionError, FileNotFoundError, OSError):
                pass
            return results

        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(None, _search)

        return {
            "status": "ok",
            "query": query,
            "directory": str(base),
            "extensions_filter": list(ext_filter) if ext_filter else "all",
            "count": len(results),
            "capped": len(results) >= MAX_SEARCH_RESULTS,
            "results": results,
        }

    async def do_file_info(self, path: str) -> dict:
        """Get detailed file information: size, dates, type, permissions. מידע על קובץ."""
        p = Path(path)
        if not p.exists():
            return {"error": f"File not found: {path}"}

        try:
            stat = p.stat()
        except OSError as e:
            return {"error": f"Cannot stat file: {e}"}

        return {
            "status": "ok",
            "name": p.name,
            "path": str(p.resolve()),
            "is_directory": p.is_dir(),
            "extension": p.suffix if p.is_file() else None,
            "category": _categorize_file(p.suffix) if p.is_file() else "directory",
            "size_bytes": stat.st_size,
            "size_human": _human_size(stat.st_size),
            "created": datetime.fromtimestamp(stat.st_ctime).isoformat(timespec="seconds"),
            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            "accessed": datetime.fromtimestamp(stat.st_atime).isoformat(timespec="seconds"),
            "readonly": not os.access(str(p), os.W_OK),
        }

    async def do_find_duplicates(self, directory: str) -> dict:
        """Find duplicate files by size + hash in a directory. Scans recursively. איתור קבצים כפולים."""
        base = Path(directory)
        if not base.exists():
            return {"error": f"Directory not found: {directory}"}

        def _scan():
            # Phase 1: group files by size
            size_map: dict[int, list[Path]] = {}
            file_count = 0

            for f in base.rglob("*"):
                if file_count >= MAX_DUPLICATE_SCAN_FILES:
                    break
                try:
                    if not f.is_file() or _should_skip(f):
                        continue
                    sz = f.stat().st_size
                    if sz == 0:
                        continue
                    size_map.setdefault(sz, []).append(f)
                    file_count += 1
                except (PermissionError, FileNotFoundError, OSError):
                    continue

            # Phase 2: for files with same size, compare by hash
            duplicates = []
            for sz, files in size_map.items():
                if len(files) < 2:
                    continue
                if sz > MAX_HASH_SIZE:
                    continue

                hash_map: dict[str, list[Path]] = {}
                for f in files:
                    try:
                        h = hashlib.md5()
                        with open(f, "rb") as fh:
                            for chunk in iter(lambda: fh.read(8192), b""):
                                h.update(chunk)
                        digest = h.hexdigest()
                        hash_map.setdefault(digest, []).append(f)
                    except (PermissionError, FileNotFoundError, OSError):
                        continue

                for digest, dup_files in hash_map.items():
                    if len(dup_files) >= 2:
                        duplicates.append({
                            "hash": digest,
                            "size": _human_size(sz),
                            "count": len(dup_files),
                            "files": [str(f) for f in dup_files],
                        })

            return duplicates, file_count

        loop = asyncio.get_event_loop()
        duplicates, scanned = await loop.run_in_executor(None, _scan)

        total_wasted = 0
        for dup in duplicates:
            # wasted space = size * (count - 1)
            files = [Path(f) for f in dup["files"]]
            try:
                sz = files[0].stat().st_size
                total_wasted += sz * (dup["count"] - 1)
            except OSError:
                pass

        return {
            "status": "ok",
            "directory": str(base),
            "files_scanned": scanned,
            "duplicate_groups": len(duplicates),
            "wasted_space": _human_size(total_wasted),
            "duplicates": duplicates[:50],  # cap output
        }

    async def do_organize_downloads(self, directory: str = "") -> dict:
        """List files in downloads folder grouped by type (images, docs, videos, archives, code, other). ארגון תיקיית הורדות."""
        if directory:
            base = Path(directory)
        else:
            # Try common download locations
            base = Path.home() / "Downloads"
            if not base.exists():
                base = Path.home() / "Download"

        if not base.exists():
            return {"error": f"Downloads directory not found: {base}"}

        def _organize():
            categories: dict[str, list[dict]] = {}
            total_size = 0

            try:
                for f in sorted(base.iterdir()):
                    if not f.is_file():
                        continue
                    try:
                        stat = f.stat()
                    except OSError:
                        continue

                    cat = _categorize_file(f.suffix)
                    total_size += stat.st_size
                    entry = {
                        "name": f.name,
                        "size": _human_size(stat.st_size),
                        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                    }
                    categories.setdefault(cat, []).append(entry)
            except (PermissionError, OSError):
                pass

            return categories, total_size

        loop = asyncio.get_event_loop()
        categories, total_size = await loop.run_in_executor(None, _organize)

        summary = {cat: len(files) for cat, files in categories.items()}

        return {
            "status": "ok",
            "directory": str(base),
            "total_files": sum(summary.values()),
            "total_size": _human_size(total_size),
            "summary": summary,
            "categories": categories,
        }

    async def do_watch_folder(self, directory: str, pattern: str = "*") -> dict:
        """List files modified in the last 24 hours in a folder. מעקב אחר שינויים בתיקייה."""
        base = Path(directory)
        if not base.exists():
            return {"error": f"Directory not found: {directory}"}

        cutoff = time.time() - 86400  # 24 hours ago

        def _watch():
            changes = []
            try:
                glob_pattern = pattern if pattern != "*" else "*"
                for f in base.rglob(glob_pattern):
                    if len(changes) >= MAX_SEARCH_RESULTS:
                        break
                    try:
                        if _should_skip(f):
                            continue
                        stat = f.stat()
                        if stat.st_mtime >= cutoff:
                            changes.append({
                                "path": str(f),
                                "name": f.name,
                                "is_dir": f.is_dir(),
                                "size": _human_size(stat.st_size) if f.is_file() else None,
                                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                            })
                    except (PermissionError, FileNotFoundError, OSError):
                        continue
            except (PermissionError, FileNotFoundError, OSError):
                pass

            # Sort by modification time, newest first
            changes.sort(key=lambda x: x["modified"], reverse=True)
            return changes

        loop = asyncio.get_event_loop()
        changes = await loop.run_in_executor(None, _watch)

        return {
            "status": "ok",
            "directory": str(base),
            "pattern": pattern,
            "period": "last 24 hours",
            "count": len(changes),
            "capped": len(changes) >= MAX_SEARCH_RESULTS,
            "changes": changes,
        }
