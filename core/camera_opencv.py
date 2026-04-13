"""OpenCV helpers for local USB / built-in webcams (DirectShow on Windows)."""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _capture_backend() -> int:
    """Prefer DirectShow on Windows — more reliable for built-in webcams."""
    if sys.platform == "win32":
        import cv2

        return int(getattr(cv2, "CAP_DSHOW", 0))
    return 0


def probe_local_cameras(max_index: int = 10) -> dict[str, dict[str, Any]]:
    """
    Try camera indices 0..max_index-1. Returns dict keyed by index string,
    e.g. {"0": {"index": 0, "width": 640, "height": 480, "type": "local", "label": "Camera 0"}}.
    """
    import cv2

    found: dict[str, dict[str, Any]] = {}
    backend = _capture_backend()
    for i in range(max_index):
        cap = cv2.VideoCapture(i, backend) if backend else cv2.VideoCapture(i)
        try:
            if not cap.isOpened():
                continue
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            ret, _frame = cap.read()
            if not ret:
                continue
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            found[str(i)] = {
                "index": i,
                "width": w,
                "height": h,
                "type": "local",
                "label": f"Camera {i}",
            }
        except Exception as e:
            logger.debug("probe index %s: %s", i, e)
        finally:
            cap.release()
    return found


def snapshot_from_index(index: int, dest: Path) -> tuple[bool, str]:
    """
    Grab one frame from OpenCV camera index and write PNG. Returns (ok, message_or_path).
    """
    import cv2

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    backend = _capture_backend()
    cap = cv2.VideoCapture(index, backend) if backend else cv2.VideoCapture(index)
    try:
        if not cap.isOpened():
            return False, f"Could not open camera index {index}"
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        # Warm up — first frame is often black on some drivers
        for _ in range(3):
            cap.read()
        ret, frame = cap.read()
        if not ret or frame is None:
            return False, f"Failed to read frame from camera {index}"
        ok = cv2.imwrite(str(dest), frame)
        if not ok:
            return False, "cv2.imwrite failed"
        return True, str(dest.resolve())
    finally:
        cap.release()


def default_snapshot_path(project_root: Path, index: int) -> Path:
    d = project_root / "data" / "camera_snapshots"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"cam_{index}_{int(time.time())}.png"
