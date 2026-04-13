"""Local webcam snapshots via OpenCV — for Jarvis vision (PNG path)."""

import asyncio
import logging
from pathlib import Path

from core.skill_base import BaseSkill
from config import get_settings

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class CameraVisionSkill(BaseSkill):
    name = "camera_vision"
    description = (
        "Discover local webcams (OpenCV) and capture PNG snapshots. "
        "Returns image_path / vision_attach_path for models that accept images."
    )
    REQUIREMENTS: list[str] = ["opencv-python"]

    def __init__(self):
        self.settings = get_settings()
        self.cameras: dict[str, dict] = {}

    async def execute(self, action: str, params: dict | None = None) -> dict:
        method = getattr(self, f"do_{action}", None)
        if not method:
            return {"error": f"Unknown action: {action}"}
        try:
            return await method(**(params or {}))
        except Exception as e:
            logger.error("Error executing %s: %s", action, e)
            return {"error": str(e)}

    async def do_discover(self) -> dict:
        """Probe local camera indices 0–9."""
        from core.camera_opencv import probe_local_cameras

        try:
            discovered = await asyncio.to_thread(probe_local_cameras, 10)
        except Exception as e:
            return {"error": f"OpenCV discover failed: {e}. Install: pip install opencv-python"}

        self.cameras = {k: dict(v) for k, v in discovered.items()}
        return {
            "status": "ok",
            "count": len(discovered),
            "cameras": discovered,
            "message": f"Found {len(discovered)} local camera(s). Use take_snapshot with camera='0' etc.",
        }

    async def do_take_snapshot(self, camera: str = "0", save_path: str = "") -> dict:
        """Capture one PNG from camera index (default '0'). Optional save_path; else data/camera_snapshots/."""
        from core.camera_opencv import default_snapshot_path, snapshot_from_index

        try:
            idx = int(str(camera).strip())
        except ValueError:
            return {"error": f"Invalid camera index: {camera!r}"}

        dest = Path(save_path.strip()) if save_path.strip() else default_snapshot_path(PROJECT_ROOT, idx)

        try:
            ok, msg = await asyncio.to_thread(snapshot_from_index, idx, dest)
        except Exception as e:
            return {"error": str(e)}

        if not ok:
            return {"error": msg}

        return {
            "status": "ok",
            "image_path": msg,
            "vision_attach_path": msg,
            "camera_index": idx,
            "message": f"Snapshot saved: {msg}",
        }

    async def do_get_stream(self, camera: str = "0") -> dict:
        """Local webcams have no HTTP stream here — use take_snapshot for a still frame."""
        return {
            "status": "ok",
            "camera": str(camera),
            "message": (
                "No RTSP/HTTP stream from this skill for USB webcams. "
                "Call take_snapshot to save a PNG and attach it for vision."
            ),
        }
