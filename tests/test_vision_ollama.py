"""Tests for Ollama vision screenshot encoding."""

from pathlib import Path

import pytest
from PIL import Image

from core.vision_ollama import encode_screenshot_for_ollama, ollama_model_supports_vision


def test_ollama_model_supports_vision():
    assert ollama_model_supports_vision("qwen3-vl:8b")
    assert ollama_model_supports_vision("Qwen3-VL-8B")
    assert not ollama_model_supports_vision("llama3.1:8b")


def test_encode_screenshot_for_ollama(tmp_path: Path):
    img_path = tmp_path / "cap.png"
    Image.new("RGB", (100, 80), color=(20, 120, 200)).save(img_path)
    b64 = encode_screenshot_for_ollama(str(img_path))
    assert b64 is not None
    assert len(b64) > 100


def test_encode_missing_file():
    assert encode_screenshot_for_ollama("/nonexistent/no.png") is None
