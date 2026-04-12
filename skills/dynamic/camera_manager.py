"""Discover and manage local webcams (OpenCV) and optional RTSP cameras."""

import asyncio
import logging
from core.skill_base import BaseSkill
from config import get_settings

logger = logging.getLogger(__name__)


class CameraManager(BaseSkill):
    name = "camera_manager"
    description = (
        "Discover and test local USB/built-in webcams (Windows DirectShow) via OpenCV, "
        "list them, optional RTSP IP cameras. Use discover then test_stream."
    )
    REQUIREMENTS: list[str] = ["opencv-python"]

    def __init__(self):
        self.settings = get_settings()
        # key: str index "0","1" or "rtsp:host:port"
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
        """Probe local camera indices 0–9 (OpenCV). Merges with any existing RTSP entries."""
        from core.camera_opencv import probe_local_cameras

        try:
            discovered = await asyncio.to_thread(probe_local_cameras, 10)
        except Exception as e:
            return {"error": f"OpenCV discover failed: {e}. Install: pip install opencv-python"}

        # Drop stale local entries, keep RTSP keys
        rtsp_only = {k: v for k, v in self.cameras.items() if v.get("type") == "rtsp"}
        self.cameras = {**rtsp_only, **discovered}
        return {
            "status": "ok",
            "count": len(discovered),
            "cameras": discovered,
            "message": f"Found {len(discovered)} local camera(s). Keys are index strings, e.g. '0'.",
        }

    async def do_test_stream(self, camera_key: str = "0") -> dict:
        """Read one test frame from a local camera by index key (e.g. '0') or verify RTSP opens."""
        key = str(camera_key).strip()
        if key not in self.cameras:
            return {"error": f"Unknown camera_key '{key}'. Run discover first.", "known_keys": list(self.cameras.keys())}

        info = self.cameras[key]
        try:
            import cv2
        except ImportError:
            return {"error": "opencv-python not installed"}

        if info.get("type") == "rtsp":
            url = info.get("rtsp_url", "")
            cap = cv2.VideoCapture(url)
            try:
                if not cap.isOpened():
                    return {"error": "Failed to open RTSP stream"}
                ret, _frame = cap.read()
                return {"status": "ok" if ret else "error", "preview": bool(ret), "rtsp": True}
            finally:
                cap.release()

        import sys

        idx = int(info.get("index", key))
        backend = cv2.CAP_DSHOW if sys.platform == "win32" else 0
        cap = cv2.VideoCapture(idx, backend) if backend else cv2.VideoCapture(idx)
        try:
            if not cap.isOpened():
                return {"error": f"Could not open camera index {idx}"}
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            ret, _frame = cap.read()
            return {"status": "ok" if ret else "error", "preview": bool(ret), "index": idx}
        finally:
            cap.release()

    async def do_add(self, ip: str, port: int, username: str, password: str) -> dict:
        """Add RTSP camera by IP/port/credentials; tests stream before storing."""
        try:
            import cv2
        except ImportError:
            return {"error": "opencv-python not installed"}

        rtsp_url = f"rtsp://{username}:{password}@{ip}:{port}/live"
        key = f"rtsp:{ip}:{port}"
        cap = cv2.VideoCapture(rtsp_url)
        try:
            if not cap.isOpened():
                return {"error": "Failed to open RTSP stream (check URL/credentials)."}
            ret, _frame = cap.read()
            if not ret:
                return {"error": "RTSP opened but no frame received."}
        finally:
            cap.release()

        self.cameras[key] = {
            "type": "rtsp",
            "ip": ip,
            "port": port,
            "username": username,
            "password": password,
            "rtsp_url": rtsp_url,
            "label": key,
        }
        return {"status": "ok", "camera_key": key, "message": "RTSP camera added."}

    async def do_list(self) -> dict:
        """List configured / discovered cameras (safe fields only)."""
        safe = {}
        for k, v in self.cameras.items():
            entry = {**v}
            entry.pop("password", None)
            safe[k] = entry
        return {"status": "ok", "cameras": safe, "count": len(safe)}

    async def do_remove(self, camera_key: str) -> dict:
        """Remove a camera by its key (e.g. '0' or 'rtsp:192.168.1.10:554')."""
        key = str(camera_key).strip()
        if key not in self.cameras:
            return {"error": f"Camera key not found: {key}", "known_keys": list(self.cameras.keys())}
        del self.cameras[key]
        return {"status": "ok", "message": f"Removed {key}"}
