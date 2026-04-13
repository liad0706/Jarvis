"""OpenAI-compatible API server — lets any OpenAI client talk to Jarvis.

Endpoints:
  POST /v1/chat/completions — Chat completions (streaming + non-streaming)
  GET  /v1/models           — List available models
  GET  /health              — Health check

Usage:
  from core.api_server import create_api_app
  app = create_api_app(orchestrator)
  uvicorn.run(app, host="0.0.0.0", port=8600)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

logger = logging.getLogger(__name__)


def create_api_app(orchestrator=None, registry=None) -> FastAPI:
    """Create a FastAPI app that serves an OpenAI-compatible API."""

    app = FastAPI(
        title="Jarvis API",
        description="OpenAI-compatible API server for Jarvis",
        version="1.0.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "jarvis", "timestamp": time.time()}

    @app.get("/v1/models")
    async def list_models():
        """List available models (OpenAI-compatible)."""
        from config import get_settings
        settings = get_settings()
        models = [
            {
                "id": "jarvis",
                "object": "model",
                "created": int(time.time()),
                "owned_by": "jarvis-local",
            },
            {
                "id": settings.ollama_model,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "ollama",
            },
        ]
        if settings.openai_api_key:
            models.append({
                "id": settings.openai_model,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "openai",
            })
        return {"object": "list", "data": models}

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        """OpenAI-compatible chat completions endpoint."""
        body = await request.json()
        messages = body.get("messages", [])
        stream = body.get("stream", False)
        model = body.get("model", "jarvis")
        temperature = body.get("temperature")
        max_tokens = body.get("max_tokens")

        if not messages:
            return JSONResponse(
                status_code=400,
                content={"error": {"message": "messages is required", "type": "invalid_request_error"}},
            )

        # Extract the last user message
        user_msg = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, list):
                    user_msg = " ".join(
                        c.get("text", "") for c in content if c.get("type") == "text"
                    )
                else:
                    user_msg = content
                break

        if not user_msg:
            return JSONResponse(
                status_code=400,
                content={"error": {"message": "No user message found", "type": "invalid_request_error"}},
            )

        completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        created = int(time.time())

        if stream:
            return StreamingResponse(
                _stream_response(orchestrator, user_msg, completion_id, created, model),
                media_type="text/event-stream",
            )

        # Non-streaming
        try:
            if orchestrator:
                response_text = await orchestrator.process(user_msg)
            else:
                response_text = "Jarvis orchestrator not available."
        except Exception as e:
            logger.error("API: orchestrator error — %s", e)
            response_text = f"Error: {e}"

        return {
            "id": completion_id,
            "object": "chat.completion",
            "created": created,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": response_text,
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": len(user_msg.split()) * 2,
                "completion_tokens": len(response_text.split()) * 2,
                "total_tokens": (len(user_msg.split()) + len(response_text.split())) * 2,
            },
        }

    return app


async def _stream_response(orchestrator, user_msg: str, completion_id: str, created: int, model: str):
    """Generate SSE stream in OpenAI format."""
    try:
        if orchestrator:
            response_text = await orchestrator.process(user_msg)
        else:
            response_text = "Jarvis orchestrator not available."
    except Exception as e:
        response_text = f"Error: {e}"

    # Simulate streaming by chunking the response
    words = response_text.split(" ")
    for i, word in enumerate(words):
        chunk = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "content": word + (" " if i < len(words) - 1 else ""),
                    },
                    "finish_reason": None,
                }
            ],
        }
        yield f"data: {json.dumps(chunk)}\n\n"
        await asyncio.sleep(0.02)

    # Final chunk
    final = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    yield f"data: {json.dumps(final)}\n\n"
    yield "data: [DONE]\n\n"
