"""WhatsApp API server — exposes Jarvis orchestrator over HTTP for the Baileys bridge.

Key fix vs naive approach: each sender gets their own conversation list AND
a per-sender asyncio.Lock so two concurrent messages from the same sender
are serialised while different senders run in parallel.
"""

import asyncio
import logging
import sys
import time as _time
from contextlib import asynccontextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import uvicorn

from config import get_settings
from core.bootstrap import bootstrap, shutdown

logger = logging.getLogger("jarvis.whatsapp")

_shared_ctx = None
conversations: dict[str, list[dict]] = {}
_sender_locks: dict[str, asyncio.Lock] = {}
_sender_last_active: dict[str, float] = {}
_SESSION_IDLE_SECONDS = 30 * 60


def set_context(ctx):
    """Inject an already-bootstrapped JarvisContext (used when launched from main.py)."""
    global _shared_ctx
    _shared_ctx = ctx


def _get_ctx():
    return _shared_ctx


def _get_lock(sender: str) -> asyncio.Lock:
    """Get or create a per-sender lock for serialising requests."""
    if sender not in _sender_locks:
        _sender_locks[sender] = asyncio.Lock()
    return _sender_locks[sender]


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _shared_ctx
    if _shared_ctx is None:
        logger.info("Booting Jarvis for WhatsApp (standalone)...")
        _shared_ctx = await bootstrap()
        owns_ctx = True
    else:
        owns_ctx = False
        logger.info("WhatsApp API using shared Jarvis context")
    logger.info("WhatsApp API ready — waiting for messages")
    yield
    if owns_ctx and _shared_ctx:
        await shutdown(_shared_ctx)
        logger.info("Jarvis shut down")


app = FastAPI(title="Jarvis WhatsApp API", lifespan=lifespan)


def _is_allowed(sender: str) -> bool:
    settings = get_settings()
    raw = settings.whatsapp_allowed_numbers.strip()
    if not raw:
        return True
    allowed = {n.strip() for n in raw.split(",") if n.strip()}
    clean_sender = sender.split("@")[0]
    return clean_sender in allowed


@app.post("/chat")
async def chat(request: Request):
    ctx = _get_ctx()
    body = await request.json()
    sender = body.get("sender", "")
    message = body.get("message", "").strip()
    name = body.get("name", "")

    if not message:
        return JSONResponse({"reply": ""})

    if not _is_allowed(sender):
        logger.warning("Blocked message from %s (not in allowed list)", sender)
        return JSONResponse({"reply": ""}, status_code=403)

    logger.info("Message from %s (%s): %s", name or "unknown", sender, message[:80])

    lock = _get_lock(sender)
    async with lock:
        if sender not in conversations:
            conversations[sender] = []

        # If sender was idle for 30+ min, summarize old session before continuing
        now = _time.time()
        last_active = _sender_last_active.get(sender, 0)
        if (
            last_active
            and (now - last_active) > _SESSION_IDLE_SECONDS
            and conversations[sender]
            and ctx.memory_manager
        ):
            try:
                await ctx.memory_manager.end_session(conversations[sender])
                logger.info("WhatsApp session for %s auto-summarized after idle", sender)
            except Exception as exc:
                logger.warning("WhatsApp session summary for %s failed: %s", sender, exc)
            conversations[sender] = []

        _sender_last_active[sender] = now
        ctx.orchestrator.conversation = conversations[sender]

        try:
            reply = await ctx.orchestrator.handle(message)
        except Exception as e:
            logger.exception("Error handling message from %s", sender)
            reply = f"שגיאה: {e}"

        conversations[sender] = ctx.orchestrator.conversation

    logger.info("Reply to %s: %s", sender, reply[:80])
    image_paths = list(getattr(ctx.orchestrator, "outgoing_chat_images", []) or [])
    return JSONResponse({"reply": reply, "image_paths": image_paths})


@app.get("/health")
async def health():
    return {"status": "ok"}


async def run_server(ctx=None, port: int | None = None):
    """Start the uvicorn server as an async task. Returns the server for later shutdown."""
    if ctx:
        set_context(ctx)
    settings = get_settings()
    _port = port or settings.whatsapp_api_port
    config = uvicorn.Config(app, host="127.0.0.1", port=_port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    settings = get_settings()
    uvicorn.run(app, host="127.0.0.1", port=settings.whatsapp_api_port, log_level="info")
