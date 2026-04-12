"""Build OpenAI Chat Completions user messages with inline images (JPEG data URLs)."""

from __future__ import annotations

from pathlib import Path

from core.vision_ollama import encode_screenshot_for_ollama


def build_openai_user_image_message(image_path: str, caption: str = "") -> dict:
    """
    Multimodal user message for GPT/Codex vision: text + image_url (base64 JPEG).
    Downscales via encode_screenshot_for_ollama for token/latency.
    """
    p = Path(image_path)
    if not p.is_file():
        return {
            "role": "user",
            "content": caption or f"(Missing image file: {image_path})",
        }

    b64 = encode_screenshot_for_ollama(str(p))
    if not b64:
        return {
            "role": "user",
            "content": caption or f"(Could not read image: {image_path})",
        }

    url = f"data:image/jpeg;base64,{b64}"
    parts: list[dict] = []
    text = (caption or "").strip() or "Describe this image in detail (answer the user's question in their language)."
    parts.append({"type": "text", "text": text})
    parts.append({"type": "image_url", "image_url": {"url": url}})
    return {"role": "user", "content": parts}
