"""Encode screen captures for Ollama vision models (e.g. qwen3-vl)."""

from __future__ import annotations

import base64
import logging
from io import BytesIO
from pathlib import Path

logger = logging.getLogger(__name__)

# Keep payloads reasonable for context window and latency
_MAX_SIDE = 1280
_JPEG_QUALITY = 82


def encode_screenshot_for_ollama(path: str) -> str | None:
    """Load a screenshot file, downscale if needed, return base64 JPEG (ASCII)."""
    p = Path(path)
    if not p.is_file():
        return None
    try:
        from PIL import Image

        with Image.open(p) as img:
            img = img.convert("RGB")
            img.thumbnail((_MAX_SIDE, _MAX_SIDE), Image.Resampling.LANCZOS)
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=_JPEG_QUALITY)
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        logger.debug("PIL encode failed for %s, falling back to raw bytes", path, exc_info=True)
        try:
            return base64.b64encode(p.read_bytes()).decode("ascii")
        except OSError:
            return None


def ollama_model_supports_vision(model_name: str) -> bool:
    m = model_name.lower()
    markers = ("vl", "vision", "llava", "moondream", "bakllava", "minicpm-v")
    return any(x in m for x in markers)
