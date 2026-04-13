"""Skill Store — metadata, import/export, enable/disable for skills."""

from __future__ import annotations

import json
import logging
import shutil
import time
import zipfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SKILL_STORE_FILE = PROJECT_ROOT / "data" / "skill_store.json"
DYNAMIC_SKILLS_DIR = PROJECT_ROOT / "skills" / "dynamic"


class SkillMetadata:
    """Metadata for a registered skill."""

    def __init__(self, name: str, version: str = "1.0.0", author: str = "Jarvis",
                 description: str = "", enabled: bool = True,
                 is_dynamic: bool = False, file_path: str = "",
                 dependencies: list[str] | None = None,
                 created_at: float = 0, updated_at: float = 0):
        self.name = name
        self.version = version
        self.author = author
        self.description = description
        self.enabled = enabled
        self.is_dynamic = is_dynamic
        self.file_path = file_path
        self.dependencies = dependencies or []
        self.created_at = created_at or time.time()
        self.updated_at = updated_at or time.time()

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "author": self.author,
            "description": self.description,
            "enabled": self.enabled,
            "is_dynamic": self.is_dynamic,
            "file_path": self.file_path,
            "dependencies": self.dependencies,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> SkillMetadata:
        return cls(**d)


class SkillStore:
    """Manages skill metadata, enable/disable, and import/export."""

    def __init__(self, registry=None):
        self.registry = registry
        self._metadata: dict[str, SkillMetadata] = {}
        self.load()

    def load(self):
        """Load skill metadata from disk."""
        if SKILL_STORE_FILE.exists():
            try:
                data = json.loads(SKILL_STORE_FILE.read_text(encoding="utf-8"))
                self._metadata = {
                    name: SkillMetadata.from_dict(meta)
                    for name, meta in data.items()
                }
                logger.info("Skill store: loaded %d entries", len(self._metadata))
            except Exception as e:
                logger.warning("Failed to load skill store: %s", e)

    def save(self):
        """Save skill metadata to disk."""
        SKILL_STORE_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {name: meta.to_dict() for name, meta in self._metadata.items()}
        SKILL_STORE_FILE.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def register_skill_metadata(self, skill) -> SkillMetadata:
        """Register or update metadata for a skill from the registry."""
        name = skill.name
        if name in self._metadata:
            meta = self._metadata[name]
            meta.description = skill.description
            meta.updated_at = time.time()
        else:
            meta = SkillMetadata(
                name=name,
                description=skill.description,
                is_dynamic=hasattr(skill, '_dynamic_loaded'),
                file_path=str(getattr(skill, '_source_file', '')),
            )
            self._metadata[name] = meta
        self.save()
        return meta

    def sync_with_registry(self):
        """Sync metadata with all currently registered skills."""
        if not self.registry:
            return
        for skill in self.registry.all_skills():
            self.register_skill_metadata(skill)

    def enable_skill(self, name: str) -> bool:
        """Enable a skill."""
        if name in self._metadata:
            self._metadata[name].enabled = True
            self._metadata[name].updated_at = time.time()
            self.save()
            return True
        return False

    def disable_skill(self, name: str) -> bool:
        """Disable a skill (won't be loaded on next restart)."""
        if name in self._metadata and self.can_disable(name):
            self._metadata[name].enabled = False
            self._metadata[name].updated_at = time.time()
            self.save()
            return True
        return False

    def is_enabled(self, name: str) -> bool:
        """Check if a skill is enabled."""
        meta = self._metadata.get(name)
        return meta.enabled if meta else True  # default to enabled

    def get_all(self) -> list[dict]:
        """Get all skill metadata."""
        return [meta.to_dict() for meta in self._metadata.values()]

    def get(self, name: str) -> dict | None:
        meta = self._metadata.get(name)
        return meta.to_dict() if meta else None

    def dependents_of(self, name: str) -> list[str]:
        return sorted(
            meta.name
            for meta in self._metadata.values()
            if meta.enabled and name in (meta.dependencies or [])
        )

    def can_disable(self, name: str) -> bool:
        return not self.dependents_of(name)

    def export_skill(self, name: str, output_dir: str) -> str | None:
        """Export a dynamic skill to a directory."""
        meta = self._metadata.get(name)
        if not meta or not meta.is_dynamic:
            return None

        source = Path(meta.file_path)
        if not source.exists():
            return None

        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)

        # Create a package with metadata + source
        export_data = {
            "metadata": meta.to_dict(),
            "source_file": source.name,
        }

        dest_source = output / source.name
        shutil.copy2(source, dest_source)

        manifest = output / f"{name}_manifest.json"
        manifest.write_text(json.dumps(export_data, indent=2, ensure_ascii=False), encoding="utf-8")

        logger.info("Exported skill %s to %s", name, output)
        return str(output)

    def export_skill_archive(self, name: str, output_dir: str) -> str | None:
        """Export a dynamic skill as a ZIP archive with metadata and source."""
        meta = self._metadata.get(name)
        if not meta or not meta.is_dynamic:
            return None

        staged = self.export_skill(name, output_dir)
        if not staged:
            return None

        output = Path(output_dir)
        archive_path = output / f"{name}.zip"
        manifest_path = output / f"{name}_manifest.json"
        source_path = output / Path(meta.file_path).name

        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            if manifest_path.exists():
                zf.write(manifest_path, arcname=manifest_path.name)
            if source_path.exists():
                zf.write(source_path, arcname=source_path.name)

        logger.info("Exported skill %s archive to %s", name, archive_path)
        return str(archive_path)

    def import_skill(self, manifest_path: str) -> bool:
        """Import a skill from a manifest file."""
        try:
            manifest = Path(manifest_path)
            data = json.loads(manifest.read_text(encoding="utf-8"))

            source_name = data.get("source_file", "")
            source_path = manifest.parent / source_name

            if not source_path.exists():
                logger.error("Source file not found: %s", source_path)
                return False

            # Copy to dynamic skills directory
            dest = DYNAMIC_SKILLS_DIR / source_name
            DYNAMIC_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, dest)

            # Update metadata
            meta_dict = data.get("metadata", {})
            meta_dict["file_path"] = str(dest)
            meta_dict["updated_at"] = time.time()
            meta = SkillMetadata.from_dict(meta_dict)
            self._metadata[meta.name] = meta
            self.save()

            logger.info("Imported skill %s from %s", meta.name, manifest_path)
            return True
        except Exception as e:
            logger.error("Failed to import skill: %s", e)
            return False

    def import_skill_archive(self, archive_path: str) -> bool:
        """Import a ZIP produced by export_skill_archive()."""
        archive = Path(archive_path)
        if not archive.exists():
            return False

        temp_dir = DYNAMIC_SKILLS_DIR / "_imports" / archive.stem
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        temp_dir.mkdir(parents=True, exist_ok=True)

        try:
            with zipfile.ZipFile(archive, "r") as zf:
                zf.extractall(temp_dir)

            manifest = next(temp_dir.glob("*_manifest.json"), None)
            if not manifest:
                return False
            return self.import_skill(str(manifest))
        except Exception as e:
            logger.error("Failed to import skill archive: %s", e)
            return False
