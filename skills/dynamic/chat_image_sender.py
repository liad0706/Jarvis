"""Push image files to connected chat UIs (dashboard WebSocket + WhatsApp bridge) via orchestrator."""

from pathlib import Path

from core.skill_base import BaseSkill

_ALLOWED_SUFFIX = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


def _resolve_image_path(image_path: str) -> tuple[str | None, str | None]:
    raw = (image_path or "").strip().strip('"').strip("'")
    if not raw:
        return None, "Empty path"
    p = Path(raw).expanduser()
    try:
        p = p.resolve()
    except OSError:
        return None, f"Invalid path: {image_path!r}"
    if not p.is_file():
        return None, f"File not found: {p}"
    if p.suffix.lower() not in _ALLOWED_SUFFIX:
        return None, f"Not an allowed image type (got {p.suffix})"
    return str(p), None


class ChatImageSender(BaseSkill):
    name = "chat_image_sender"
    description = (
        "Send an existing image file to the user's chat surfaces (web dashboard + WhatsApp when connected). "
        "Use after camera_vision_take_snapshot or system_screenshot — pass the same path (e.g. data/camera_snapshots/...)."
    )

    async def execute(self, action: str, params: dict | None = None) -> dict:
        method = getattr(self, f"do_{action}", None)
        if not method:
            return {"error": f"Unknown action: {action}"}
        try:
            return await method(**(params or {}))
        except Exception as e:
            return {"error": str(e)}

    async def do_send_screen_capture_to_chat(self, image_path: str) -> dict:
        """Alias: send a screen capture or any image path to the chat UI(s). Same as send_file_to_chat."""
        return await self.do_send_file_to_chat(image_path)

    async def do_send_file_to_chat(self, image_path: str) -> dict:
        """Queue image_path for dashboard + WhatsApp; path must exist under this machine."""
        resolved, err = _resolve_image_path(image_path)
        if err:
            return {"error": err}
        return {
            "status": "ok",
            "chat_outgoing_images": [resolved],
            "message": "Image queued for chat (dashboard / WhatsApp).",
            "path": resolved,
        }
