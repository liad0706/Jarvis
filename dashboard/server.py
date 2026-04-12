"""Real-time Jarvis dashboard — serves UI and streams EventBus events over WebSocket."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import shutil
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
from starlette.websockets import WebSocketState

from dashboard.api_routes import (
    bridge_components as bridge_dashboard_components,
    router as dashboard_api_router,
)
from dashboard.session_store import SessionStore

logger = logging.getLogger(__name__)

DASHBOARD_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = DASHBOARD_DIR.parent
_IMAGE_TOKEN_TTL_SEC = 900
# token -> (resolved Path, created_ts)
_image_tokens: dict[str, tuple[Path, float]] = {}
_ALLOWED_IMG_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
_CHATA_MEDIA_DIR = PROJECT_ROOT / "data" / "dashboard_chat_images"
_DASHBOARD_CHAT_FILENAME_RE = re.compile(
    r"^[a-f0-9]{32}\.(png|jpg|jpeg|gif|webp|bmp)$",
    re.IGNORECASE,
)
_UI_TRANSCRIPT_MAX = 200
DEFAULT_SESSION_ID = "default"
# CLI / WhatsApp / mic — show in the default dashboard thread
EXTERNAL_CHAT_SESSION_ID = DEFAULT_SESSION_ID
# Per-session: UI transcript + LLM message list (same list ref as orchestrator when active)
_chat_sessions: dict[str, dict[str, Any]] = {}
# While dashboard handle() runs, route tool images to this session transcript
_pending_dashboard_session_id: str | None = None
_pending_progress_by_session: dict[str, list[str]] = {}
_MAX_PROGRESS_LINES = 12

# ── Persistent session store ──────────────────────────────────────────
_session_store: SessionStore | None = None


def _init_session_store():
    """Initialize the session store and load persisted sessions into memory."""
    global _session_store, _chat_sessions
    _session_store = SessionStore()
    loaded = _session_store.load_all_sessions()
    if loaded:
        _chat_sessions.update(loaded)
        logger.info("Loaded %d persisted chat session(s)", len(loaded))


# Initialize on module load
_init_session_store()


def _is_safe_image_path(p: Path) -> bool:
    try:
        resolved = p.resolve()
        resolved.relative_to(PROJECT_ROOT.resolve())
    except (ValueError, OSError):
        return False
    return resolved.suffix.lower() in _ALLOWED_IMG_EXT and resolved.is_file()


def _register_chat_image_token(path_str: str) -> str | None:
    try:
        p = Path(path_str).resolve()
    except OSError:
        return None
    if not _is_safe_image_path(p):
        return None
    now = time.time()
    dead = [t for t, (_, ts) in _image_tokens.items() if now - ts > _IMAGE_TOKEN_TTL_SEC]
    for t in dead:
        del _image_tokens[t]
    token = secrets.token_urlsafe(24)
    _image_tokens[token] = (p, now)
    return token


def _ensure_session(session_id: str) -> dict[str, Any]:
    if session_id not in _chat_sessions:
        title = "שיחה" if session_id == DEFAULT_SESSION_ID else "שיחה חדשה"
        _chat_sessions[session_id] = {
            "title": title,
            "transcript": [],
            "conv": [],
            "updated_at": time.time(),
        }
        if _session_store:
            _session_store.create_session(session_id, title)
    return _chat_sessions[session_id]


def _touch_session(session_id: str) -> None:
    s = _ensure_session(session_id)
    s["updated_at"] = time.time()
    if _session_store:
        try:
            _session_store.touch(session_id)
        except Exception:
            pass


def _new_session_id() -> str:
    return secrets.token_hex(8)


def _append_ui_transcript(session_id: str, entry: dict) -> None:
    s = _ensure_session(session_id)
    tr = s["transcript"]
    tr.append(entry)
    while len(tr) > _UI_TRANSCRIPT_MAX:
        tr.pop(0)
    _touch_session(session_id)
    # Persist to SQLite
    if _session_store:
        try:
            msg_type = entry.get("type", "text")
            _session_store.add_message(
                session_id=session_id,
                role=entry.get("role", "user"),
                content=entry.get("content", ""),
                msg_type=msg_type,
                url=entry.get("url"),
            )
        except Exception as e:
            logger.debug("session store add_message failed: %s", e)


def _progress_session_id() -> str:
    return _pending_dashboard_session_id or EXTERNAL_CHAT_SESSION_ID


def _record_progress_line(session_id: str, summary: str) -> None:
    text = " ".join((summary or "").split())
    if not text:
        return
    lines = _pending_progress_by_session.setdefault(session_id, [])
    if lines and lines[-1] == text:
        return
    lines.append(text)
    while len(lines) > _MAX_PROGRESS_LINES:
        lines.pop(0)


def _flush_progress_entry(session_id: str) -> str:
    lines = _pending_progress_by_session.pop(session_id, [])
    if not lines:
        return ""
    content = "\n".join(f"- {line}" for line in lines)
    _append_ui_transcript(session_id, {
        "role": "assistant",
        "type": "progress",
        "content": content,
    })
    return content


def _maybe_title_from_first_message(session_id: str, text: str) -> None:
    s = _ensure_session(session_id)
    if session_id == DEFAULT_SESSION_ID:
        return
    if s.get("title") != "שיחה חדשה":
        return
    t = (text or "").strip().replace("\n", " ")
    if not t:
        return
    new_title = (t[:48] + "…") if len(t) > 48 else t
    s["title"] = new_title
    if _session_store:
        try:
            _session_store.update_title(session_id, new_title)
        except Exception:
            pass


_SESSION_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def _normalize_session_id(raw: str | None) -> str:
    if not raw or not isinstance(raw, str):
        return DEFAULT_SESSION_ID
    s = raw.strip()
    if not _SESSION_ID_RE.match(s):
        return DEFAULT_SESSION_ID
    return s


def _copy_image_for_dashboard_chat(src: Path) -> str | None:
    """Copy image into data/dashboard_chat_images/; return URL path for <img src>."""
    if not _is_safe_image_path(src):
        return None
    try:
        _CHATA_MEDIA_DIR.mkdir(parents=True, exist_ok=True)
        ext = src.suffix.lower() if src.suffix else ".png"
        if ext not in _ALLOWED_IMG_EXT:
            ext = ".png"
        fname = f"{secrets.token_hex(16)}{ext}"
        dest = _CHATA_MEDIA_DIR / fname
        shutil.copy2(src, dest)
        return f"/api/dashboard-chat-image/{fname}"
    except OSError as e:
        logger.warning("dashboard chat image copy failed: %s", e)
        return None


def create_app() -> FastAPI:
    return FastAPI(title="Jarvis Dashboard", docs_url=None, redoc_url=None)


app = create_app()
app.include_router(dashboard_api_router)


# ── Connected WebSocket clients ──────────────────────────────────────
_clients: set[WebSocket] = set()
_event_log: list[dict] = []          # ring buffer of recent events
_MAX_EVENT_LOG = 200
_current_status: dict = {"state": "idle", "detail": "", "ts": time.time()}
_orchestrator = None                 # set by bridge_orchestrator()
_tts = None                          # set by bridge_tts()
_chat_lock = asyncio.Lock()          # serialize dashboard chat messages


def is_chat_busy() -> bool:
    """True while the dashboard is actively waiting on Jarvis chat processing."""
    return _chat_lock.locked()


async def broadcast(payload: dict):
    """Send JSON payload to every connected dashboard client."""
    _event_log.append(payload)
    if len(_event_log) > _MAX_EVENT_LOG:
        _event_log.pop(0)

    dead: list[WebSocket] = []
    data = json.dumps(payload, ensure_ascii=False, default=str)
    for ws in _clients:
        try:
            if ws.client_state == WebSocketState.CONNECTED:
                await ws.send_text(data)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _clients.discard(ws)


def _set_status(state: str, detail: str = ""):
    _current_status["state"] = state
    _current_status["detail"] = detail
    _current_status["ts"] = time.time()


# ── EventBus listeners (registered by bridge_event_bus) ──────────────

async def _on_llm_start(**kw):
    round_num = kw.get("round", 0)
    detail = f"round {round_num + 1}" if round_num else ""
    if kw.get("slow_vl_model"):
        detail = "vision model loading..."
    _set_status("thinking", detail)
    await broadcast({"type": "llm.start", "round": round_num, "session_id": _progress_session_id(), "ts": time.time()})


async def _on_llm_complete(**kw):
    # Don't reset to idle if task.progress set us to "working" — the task is still active
    if _current_status.get("state") != "working":
        _set_status("idle")
    await broadcast({"type": "llm.complete", "round": kw.get("round", 0), "ts": time.time()})


async def _on_tool_start(**kw):
    tool = kw.get("tool", "unknown")
    args = kw.get("args", {})
    _set_status("tool", tool)
    await broadcast({
        "type": "tool.start", "tool": tool,
        "args": _safe_args(args), "session_id": _progress_session_id(), "ts": time.time(),
    })


async def _on_tool_complete(**kw):
    tool = kw.get("tool", "unknown")
    has_error = kw.get("has_error", False)
    if _current_status.get("state") != "working":
        _set_status("idle")
    await broadcast({
        "type": "tool.complete", "tool": tool,
        "has_error": has_error, "ts": time.time(),
    })


async def _on_task_progress(**kw):
    summary = kw.get("summary", "")
    if not summary:
        return
    sid = _progress_session_id()
    _record_progress_line(sid, summary)
    _set_status("working", summary[:120])
    await broadcast({
        "type": "task.progress",
        "summary": summary,
        "session_id": sid,
        "ts": time.time(),
    })


async def _on_plan_deciding(**kw):
    _set_status("planning", "analyzing complexity")
    await broadcast({"type": "plan.deciding", "ts": time.time()})


async def _on_plan_created(**kw):
    plan = kw.get("plan", {})
    steps = plan.get("steps", [])
    await broadcast({
        "type": "plan.created",
        "steps": [s if isinstance(s, str) else str(s) for s in steps],
        "ts": time.time(),
    })


async def _on_plan_start(**kw):
    goal = kw.get("goal", "")
    total = kw.get("total_steps", 0)
    _set_status("executing plan", f"{goal[:60]}")
    await broadcast({
        "type": "plan.start", "goal": goal,
        "total_steps": total, "ts": time.time(),
    })


async def _on_step_start(**kw):
    current = kw.get("current", "?")
    total = kw.get("total", "?")
    desc = kw.get("description", "")
    _set_status("step", f"{current}/{total}: {desc[:50]}")
    await broadcast({
        "type": "step.start", "current": current,
        "total": total, "description": desc, "ts": time.time(),
    })


async def _on_step_complete(**kw):
    await broadcast({
        "type": "step.complete",
        "current": kw.get("current", "?"),
        "total": kw.get("total", "?"),
        "ts": time.time(),
    })


async def _on_step_failed(**kw):
    _set_status("error", kw.get("error", "")[:80])
    await broadcast({
        "type": "step.failed",
        "step_id": kw.get("step_id", "?"),
        "error": kw.get("error", ""),
        "ts": time.time(),
    })


async def _on_step_retry(**kw):
    await broadcast({
        "type": "step.retry",
        "step_id": kw.get("step_id", "?"),
        "attempt": kw.get("attempt", "?"),
        "ts": time.time(),
    })


# ── Content cleaning — strip raw tool JSON / internal markers ─────

_JUNK_PATTERNS = [
    re.compile(r'^\s*\[\+\].*$', re.MULTILINE),                       # [+] internal steps
    re.compile(r'^\s*\[-\].*$', re.MULTILINE),                        # [-] internal steps
    re.compile(r'\{"\s*tool_calls\s*"\s*:\s*\[.*?\]\s*\}', re.DOTALL),  # raw {"tool_calls":[...]}
    re.compile(r'```json\s*\{.*?"tool_calls".*?\}\s*```', re.DOTALL),   # ```json ... tool_calls```
]


def _clean_content(text: str) -> str:
    """Remove raw tool-call JSON and internal markers from LLM output."""
    if not text:
        return text
    cleaned = text
    for pat in _JUNK_PATTERNS:
        cleaned = pat.sub('', cleaned)
    # Collapse excessive blank lines left behind
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned).strip()
    return cleaned


def _snapshot_conversation_for_client(session_id: str) -> list[dict]:
    """Transcript for WebSocket snapshot and /api/conversation (includes persisted images)."""
    s = _ensure_session(session_id)
    tr = s["transcript"]
    out: list[dict] = []
    for m in tr[-80:]:
        if m.get("type") == "image" and m.get("url"):
            out.append({
                "role": m.get("role", "assistant"),
                "type": "image",
                "url": m["url"],
            })
            continue
        if m.get("type") == "progress":
            out.append({
                "role": m.get("role", "assistant"),
                "type": "progress",
                "content": (m.get("content") or "")[:4000],
            })
            continue
        content = m.get("content") or ""
        if m.get("role") == "assistant":
            content = _clean_content(content)
        out.append({"role": m["role"], "content": content[:4000]})
    return out


def _safe_args(args: Any) -> dict:
    """Sanitize tool arguments for display (truncate long values)."""
    if not isinstance(args, dict):
        return {"raw": str(args)[:200]}
    safe = {}
    for k, v in args.items():
        s = str(v)
        safe[k] = s[:200] + "…" if len(s) > 200 else s
    return safe


async def _on_chat_user(**kw):
    """Forward user messages from CLI/WhatsApp to dashboard clients."""
    if _chat_lock.locked():
        return  # Dashboard initiated this message, already handled
    content = kw.get("content", "")
    if content:
        sid = EXTERNAL_CHAT_SESSION_ID
        _flush_progress_entry(sid)
        _append_ui_transcript(sid, {"role": "user", "content": content})
        await broadcast({
            "type": "chat.user",
            "content": content,
            "session_id": sid,
            "ts": time.time(),
        })


async def _on_chat_assistant(**kw):
    """Forward assistant responses from CLI/WhatsApp to dashboard clients."""
    _set_status("idle")
    if _chat_lock.locked():
        return  # Dashboard initiated this message, already handled
    content = _clean_content(kw.get("content", ""))
    if content:
        sid = EXTERNAL_CHAT_SESSION_ID
        _flush_progress_entry(sid)
        _append_ui_transcript(sid, {"role": "assistant", "content": content})
        await broadcast({
            "type": "chat.assistant",
            "content": content,
            "session_id": sid,
            "ts": time.time(),
        })


async def _on_chat_outgoing_image(**kw):
    """Show tool-queued images (chat_image_sender) in the dashboard chat."""
    path = kw.get("path") or ""
    if not path:
        return
    url = _copy_image_for_dashboard_chat(Path(path))
    if not url:
        logger.warning("chat.outgoing_image rejected (unsafe or missing): %s", path[:120])
        return
    sid = _pending_dashboard_session_id or EXTERNAL_CHAT_SESSION_ID
    _flush_progress_entry(sid)
    _append_ui_transcript(sid, {"role": "assistant", "type": "image", "url": url})
    await broadcast({
        "type": "chat.image",
        "url": url,
        "session_id": sid,
        "ts": time.time(),
    })


async def _on_stream_start(**kw):
    # Dashboard chat renders only completed assistant replies.
    return


async def _on_stream_token(**kw):
    # Dashboard chat renders only completed assistant replies.
    return


async def _on_stream_complete(**kw):
    # Dashboard chat renders only completed assistant replies.
    return


async def _on_notification(**kw):
    await broadcast({
        "type": "notification",
        **kw,
        "ts": time.time(),
    })


async def _on_model_route(**kw):
    await broadcast({
        "type": "model.route",
        **kw,
        "ts": time.time(),
    })


def bridge_event_bus(event_bus):
    """Subscribe dashboard listeners to the Jarvis EventBus."""
    event_bus.on("llm.start", _on_llm_start)
    event_bus.on("llm.complete", _on_llm_complete)
    event_bus.on("tool.start", _on_tool_start)
    event_bus.on("tool.complete", _on_tool_complete)
    event_bus.on("task.progress", _on_task_progress)
    event_bus.on("plan.deciding", _on_plan_deciding)
    event_bus.on("plan.created", _on_plan_created)
    event_bus.on("plan.start", _on_plan_start)
    event_bus.on("step.start", _on_step_start)
    event_bus.on("step.complete", _on_step_complete)
    event_bus.on("step.failed", _on_step_failed)
    event_bus.on("step.retry", _on_step_retry)
    event_bus.on("chat.user", _on_chat_user)
    event_bus.on("chat.assistant", _on_chat_assistant)
    event_bus.on("chat.outgoing_image", _on_chat_outgoing_image)
    event_bus.on("stream.start", _on_stream_start)
    event_bus.on("stream.token", _on_stream_token)
    event_bus.on("stream.complete", _on_stream_complete)
    event_bus.on("notification", _on_notification)
    event_bus.on("model.route", _on_model_route)


def bridge_tts(tts):
    """Pass the TTS instance so dashboard responses are spoken through speakers."""
    global _tts
    _tts = tts


def bridge_orchestrator(orchestrator):
    """Keep a reference to orchestrator; wire default session to orchestrator.conversation.

    After hot-reload the orchestrator restores its own conversation from disk,
    but the dashboard also has sessions in SQLite.  Prefer the dashboard session
    if it has more messages (it's the authoritative store); otherwise seed it
    from the orchestrator's restored state.
    """
    global _orchestrator
    _orchestrator = orchestrator
    d = _ensure_session(DEFAULT_SESSION_ID)
    oc = orchestrator.conversation
    session_conv = d["conv"]
    if oc is not session_conv:
        # Pick whichever has more context
        if len(session_conv) >= len(oc):
            orchestrator.conversation = session_conv
        else:
            session_conv.clear()
            session_conv.extend(oc)
            orchestrator.conversation = session_conv


def bridge_components(
    orchestrator=None,
    memory_manager=None,
    awareness=None,
    metrics=None,
    notifications=None,
    automation_engine=None,
    registry=None,
    skill_store=None,
    model_router=None,
):
    bridge_dashboard_components(
        orchestrator=orchestrator,
        memory_manager=memory_manager,
        awareness=awareness,
        metrics=metrics,
        notifications=notifications,
        automation_engine=automation_engine,
        registry=registry,
        skill_store=skill_store,
        model_router=model_router,
    )


# ── HTTP routes ──────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = DASHBOARD_DIR / "index.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/mobile", response_class=HTMLResponse)
async def mobile():
    html_path = DASHBOARD_DIR / "mobile.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/pages/{page_name}", response_class=HTMLResponse)
async def dashboard_page(page_name: str):
    if not re.fullmatch(r"[a-zA-Z0-9_.-]+\.html", page_name):
        return HTMLResponse("Bad request", status_code=400)
    html_path = DASHBOARD_DIR / "pages" / page_name
    if not html_path.is_file():
        return HTMLResponse("Not found", status_code=404)
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/manifest.json")
async def manifest():
    return FileResponse(DASHBOARD_DIR / "manifest.json", media_type="application/manifest+json")


@app.get("/sw.js")
async def service_worker():
    return FileResponse(DASHBOARD_DIR / "sw.js", media_type="application/javascript")


_enriched_cache: dict = {}
_enriched_cache_ts: float = 0
_ENRICHED_CACHE_TTL = 30  # seconds


async def _get_enriched_status() -> dict:
    """Query skills for live widget data (lights, music, tv, home, schedule). Cached 30s."""
    global _enriched_cache, _enriched_cache_ts
    now = time.time()
    if now - _enriched_cache_ts < _ENRICHED_CACHE_TTL and _enriched_cache:
        return _enriched_cache

    extra: dict = {}
    if not _orchestrator or not hasattr(_orchestrator, "registry"):
        return extra
    reg = _orchestrator.registry

    # Lights — smart_home list_devices
    try:
        sh = reg.get("smart_home")
        if sh:
            result = await sh.execute("list_devices")
            devices = result.get("devices") or []
            on_count = sum(1 for d in devices if d.get("state") == "on")
            total = len(devices)
            extra["lights"] = f"{on_count} דולקים" if on_count else f"הכל כבוי ({total})"
    except Exception:
        pass

    # Music — spotify current
    try:
        sp = reg.get("spotify")
        if sp:
            result = await sp.execute("current")
            if result.get("status") in ("playing", "paused"):
                icon = "▶" if result["status"] == "playing" else "⏸"
                extra["music"] = f"{icon} {result.get('track', '?')} — {result.get('artist', '?')}"
            else:
                extra["music"] = "לא מנגן"
    except Exception:
        pass

    # TV — apple_tv status
    try:
        atv = reg.get("apple_tv")
        if atv:
            result = await atv.execute("status")
            if result.get("error"):
                extra["tv"] = "כבוי"
            else:
                state = result.get("state", "idle")
                app_name = result.get("app", "")
                extra["tv"] = f"{state} {app_name}".strip() if state != "idle" else "פעיל"
    except Exception:
        pass

    # Home — presence scan
    try:
        pr = reg.get("presence")
        if pr:
            result = await pr.execute("scan")
            home_list = result.get("home", [])
            if home_list:
                names = ", ".join(p.get("owner", p.get("name", "?")) for p in home_list)
                extra["home"] = names
            else:
                extra["home"] = "אף אחד"
    except Exception:
        pass

    # Schedule — next scheduled task
    try:
        sched = reg.get("scheduler")
        if sched:
            result = await sched.execute("list")
            schedules = result.get("schedules") or []
            enabled = [s for s in schedules if s.get("enabled")]
            if enabled:
                from datetime import datetime as _dt
                now_hm = _dt.now()
                best = None
                for s in enabled:
                    h, m = s.get("hour", 0), s.get("minute", 0)
                    if h > now_hm.hour or (h == now_hm.hour and m > now_hm.minute):
                        if best is None or (h, m) < (best.get("hour", 24), best.get("minute", 0)):
                            best = s
                if best is None:
                    best = enabled[0]
                extra["schedule"] = f"{best['hour']:02d}:{best['minute']:02d} {best.get('name', '')}"
    except Exception:
        pass

    _enriched_cache = extra
    _enriched_cache_ts = now
    return extra


@app.get("/api/status")
async def api_status():
    enriched = await _get_enriched_status()
    return {**_current_status, **enriched}


@app.get("/api/events")
async def api_events():
    return _event_log[-50:]


@app.get("/api/dashboard-chat-image/{filename}")
async def api_dashboard_chat_image(filename: str):
    """Serve a copy stored for chat history (survives page refresh)."""
    if not _DASHBOARD_CHAT_FILENAME_RE.match(filename):
        return HTMLResponse("Bad request", status_code=400)
    path = (_CHATA_MEDIA_DIR / filename).resolve()
    try:
        path.relative_to(_CHATA_MEDIA_DIR.resolve())
    except ValueError:
        return HTMLResponse("Not found", status_code=404)
    if not path.is_file():
        return HTMLResponse("Not found", status_code=404)
    media = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
    }.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path, media_type=media)


@app.get("/api/image/{token}")
async def api_serve_chat_image(token: str):
    """Serve a tool-queued image by short-lived token (path must stay under project root)."""
    entry = _image_tokens.get(token)
    if not entry:
        return HTMLResponse("Not found", status_code=404)
    path, ts = entry
    if time.time() - ts > _IMAGE_TOKEN_TTL_SEC:
        del _image_tokens[token]
        return HTMLResponse("Expired", status_code=404)
    if not path.is_file() or not _is_safe_image_path(path):
        return HTMLResponse("Gone", status_code=404)
    media = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
    }.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path, media_type=media)


@app.get("/api/conversation")
async def api_conversation(session_id: str = Query(default=DEFAULT_SESSION_ID)):
    sid = _normalize_session_id(session_id)
    return _snapshot_conversation_for_client(sid)


class NewSessionRequest(BaseModel):
    title: str | None = None


@app.get("/api/chat-sessions")
async def api_chat_sessions():
    """List chat threads (newest activity first)."""
    items = []
    for sid, s in _chat_sessions.items():
        items.append({
            "id": sid,
            "title": s.get("title", sid),
            "updated_at": s.get("updated_at", 0),
            "message_count": len(s.get("transcript") or []),
        })
    items.sort(key=lambda x: x["updated_at"], reverse=True)
    return {"sessions": items}


@app.post("/api/chat-sessions")
async def api_chat_sessions_create(req: NewSessionRequest | None = None):
    """Start a new empty thread."""
    nid = _new_session_id()
    _ensure_session(nid)
    if req and req.title and req.title.strip():
        title = req.title.strip()[:80]
        _chat_sessions[nid]["title"] = title
        if _session_store:
            _session_store.update_title(nid, title)
    return {"id": nid, "title": _chat_sessions[nid]["title"]}


class ForkBranchRequest(BaseModel):
    session_id: str | None = None
    at_index: int | None = None
    label: str | None = None


@app.get("/api/branches")
async def api_branches():
    if not _orchestrator:
        return {"branches": []}
    return {
        "branches": _orchestrator.list_branches(),
        "current": _orchestrator.branch_manager.current_branch,
    }


@app.post("/api/branches/fork")
async def api_branch_fork(req: ForkBranchRequest):
    if not _orchestrator:
        return {"error": "Orchestrator not connected"}
    sid = _normalize_session_id(req.session_id)
    session = _ensure_session(sid)
    branch = _orchestrator.branch_manager.fork(
        session["conv"],
        at_index=req.at_index,
        label=(req.label or "").strip(),
    )
    return {"branch": branch.to_dict()}


@app.post("/api/branches/{branch_id}/open")
async def api_branch_open(branch_id: str):
    if not _orchestrator:
        return {"error": "Orchestrator not connected"}
    messages = _orchestrator.branch_manager.switch(branch_id)
    if messages is None:
        return {"error": "Unknown branch"}

    nid = _new_session_id()
    session = _ensure_session(nid)
    session["conv"] = list(messages)
    session["transcript"] = [
        {"role": msg.get("role", "assistant"), "content": msg.get("content", "")}
        for msg in messages
        if msg.get("content")
    ]
    branch_meta = next(
        (item for item in _orchestrator.list_branches() if item.get("branch_id") == branch_id),
        None,
    )
    if branch_meta and branch_meta.get("label"):
        session["title"] = branch_meta["label"][:80]
        if _session_store:
            _session_store.update_title(nid, session["title"])
    _touch_session(nid)
    return {"session_id": nid, "branch_id": branch_id, "title": session["title"]}


@app.delete("/api/branches/{branch_id}")
async def api_branch_delete(branch_id: str):
    if not _orchestrator:
        return {"error": "Orchestrator not connected"}
    return {"ok": _orchestrator.branch_manager.delete_branch(branch_id)}


@app.delete("/api/chat-sessions/{session_id}")
async def api_chat_sessions_delete(session_id: str):
    """Remove a thread. The default session cannot be deleted."""
    raw = (session_id or "").strip()
    if not _SESSION_ID_RE.match(raw):
        return {"error": "Invalid session id", "ok": False}
    sid = raw
    if sid == DEFAULT_SESSION_ID:
        return {"error": "Cannot delete the default session", "ok": False}
    if sid not in _chat_sessions:
        return {"error": "Unknown session", "ok": False}
    # Summarize the session into episodic memory before deleting
    conv = _chat_sessions[sid].get("conv", [])
    if _orchestrator and _orchestrator.memory_manager and conv:
        try:
            await _orchestrator.memory_manager.end_session(conv)
        except Exception as exc:
            logger.warning("Episodic session summary on delete failed: %s", exc)
    # If orchestrator still points at this conv list, repoint to default
    session_data = _chat_sessions.get(sid)
    if session_data and _orchestrator and _orchestrator.conversation is session_data.get("conv"):
        d = _ensure_session(DEFAULT_SESSION_ID)
        _orchestrator.conversation = d["conv"]
    _chat_sessions.pop(sid, None)
    if _session_store:
        try:
            _session_store.delete_session(sid)
        except Exception:
            pass
    await broadcast({
        "type": "chat.session_deleted",
        "session_id": sid,
        "ts": time.time(),
    })
    return {"ok": True}


@app.get("/api/metrics")
async def api_metrics():
    """Return current metrics from live orchestrator (or fallback to disk)."""
    if _orchestrator and hasattr(_orchestrator, "metrics"):
        return await _orchestrator.metrics.get_summary()
    metrics_path = Path(__file__).resolve().parent.parent / "data" / "metrics.json"
    if metrics_path.exists():
        return json.loads(metrics_path.read_text(encoding="utf-8"))
    return {}


def _get_provider_info() -> dict:
    """Return current provider name and model."""
    if not _orchestrator:
        return {"name": "—", "model": "—"}
    try:
        p = _orchestrator.provider
        name = getattr(p, "name", "unknown")
        model = getattr(p, "model", "unknown")
        # Friendly display name
        display = name
        settings = getattr(_orchestrator, "settings", None)
        if settings and getattr(settings, "llm_provider", "").lower() == "lm_studio":
            display = "LM Studio"
        elif "codex" in name.lower() or "openai" in name.lower():
            display = "GPT"
        elif "ollama" in name.lower():
            display = "Ollama"
        elif "anthropic" in name.lower() or "claude" in name.lower():
            display = "Claude"
        return {"name": display, "model": model, "raw_name": name}
    except Exception:
        return {"name": "—", "model": "—"}


# Available providers for switching
_AVAILABLE_PROVIDERS = ["codex", "openai", "lm_studio", "ollama", "anthropic"]


@app.get("/api/provider")
async def api_provider():
    return _get_provider_info()


class ProviderSwitchRequest(BaseModel):
    provider: str


@app.post("/api/provider")
async def api_switch_provider(req: ProviderSwitchRequest):
    """Switch the active LLM provider."""
    if not _orchestrator:
        return {"error": "Orchestrator not connected"}

    name = req.provider.strip().lower()
    if name not in _AVAILABLE_PROVIDERS:
        return {"error": f"Unknown provider: {name}. Available: {_AVAILABLE_PROVIDERS}"}

    try:
        _orchestrator.settings.llm_provider = name
        _orchestrator._provider = None  # force re-init on next call
        # Reconfigure model router so all task routes use the new provider
        if _orchestrator.model_router:
            _orchestrator.model_router.settings = _orchestrator.settings
            _orchestrator.model_router._configure_defaults()
        # Trigger provider init to validate
        p = _orchestrator.provider
        info = _get_provider_info()
        await broadcast({"type": "provider.changed", "provider": info, "ts": time.time()})
        return {"ok": True, "provider": info}
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/providers")
async def api_list_providers():
    """List available providers and which is active."""
    current = _get_provider_info()
    providers = []
    for p in _AVAILABLE_PROVIDERS:
        display = {
            "codex": "GPT (Codex OAuth)",
            "openai": "GPT (API Key)",
            "lm_studio": "LM Studio (Local)",
            "ollama": "Ollama (Local)",
            "anthropic": "Claude",
        }
        providers.append({"id": p, "label": display.get(p, p), "active": False})
    # Mark active
    settings = getattr(_orchestrator, "settings", None) if _orchestrator else None
    active_id = getattr(settings, "llm_provider", "").lower() if settings else ""
    for p in providers:
        if p["id"] == active_id:
            p["active"] = True
    return {"current": current, "providers": providers}


_DEFAULT_QUICK_ACTIONS = [
    {"label": "💡 הדלק אור", "message": "הדלק אור בסלון"},
    {"label": "🔌 כבה הכל", "message": "כבה את כל האורות"},
    {"label": "🎵 נגן מוזיקה", "message": "נגן מוזיקה"},
    {"label": "☀️ שגרת בוקר", "message": "הפעל שגרת בוקר"},
    {"label": "📋 מה ביומן", "message": "מה ביומן היום?"},
    {"label": "📺 כבה טלוויזיה", "message": "כבה את הטלוויזיה"},
    {"label": "🏠 מי בבית?", "message": "מי בבית?"},
]

_quick_actions_path = PROJECT_ROOT / "config" / "quick_actions.json"


def _load_quick_actions() -> list[dict]:
    if _quick_actions_path.exists():
        try:
            data = json.loads(_quick_actions_path.read_text(encoding="utf-8"))
            if isinstance(data, list) and data:
                return data
        except Exception:
            pass
    return _DEFAULT_QUICK_ACTIONS


@app.get("/api/quick-actions")
async def api_quick_actions():
    return {"actions": _load_quick_actions()}


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


@app.post("/api/chat")
async def api_chat(req: ChatRequest):
    """Send a message to Jarvis and return the response."""
    global _pending_dashboard_session_id
    if not _orchestrator:
        return {"error": "Orchestrator not connected"}

    text = req.message.strip()
    if not text:
        return {"error": "Empty message"}

    sid = _normalize_session_id(req.session_id)
    _ensure_session(sid)
    _maybe_title_from_first_message(sid, text)

    _flush_progress_entry(sid)
    _append_ui_transcript(sid, {"role": "user", "content": text})
    await broadcast({
        "type": "chat.user",
        "content": text,
        "session_id": sid,
        "ts": time.time(),
    })

    async with _chat_lock:
        _orchestrator.conversation = _chat_sessions[sid]["conv"]
        _pending_dashboard_session_id = sid
        try:
            response = await _orchestrator.handle(text)
        except Exception as e:
            logger.exception("Dashboard chat error")
            if hasattr(_orchestrator, "_actionable_error_text"):
                response = _orchestrator._actionable_error_text(e)
            else:
                response = f"Error: {e}"
        finally:
            _pending_dashboard_session_id = None

    response = _clean_content(response)
    _flush_progress_entry(sid)
    _append_ui_transcript(sid, {"role": "assistant", "content": response})
    await broadcast({
        "type": "chat.assistant",
        "content": response,
        "session_id": sid,
        "ts": time.time(),
    })

    if _tts:
        asyncio.create_task(_tts.speak(response))

    return {"response": response, "session_id": sid}


# --------------- TTS audio endpoint ---------------

@app.post("/api/tts")
async def api_tts(req: ChatRequest):
    """Generate ElevenLabs TTS audio and return as WAV."""
    import os
    text = (req.message or "").strip()
    if not text:
        return {"error": "empty text"}

    api_key = os.getenv("JARVIS_ELEVENLABS_API_KEY", "")
    voice_id = os.getenv("JARVIS_ELEVENLABS_VOICE_ID", "6sFKzaJr574YWVu4UuJF")
    model_id = os.getenv("JARVIS_ELEVENLABS_MODEL", "eleven_v3")
    if not api_key:
        return {"error": "no api key"}

    import requests as http_req
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=pcm_22050"
    headers = {"xi-api-key": api_key, "Content-Type": "application/json"}
    payload = {
        "text": text,
        "model_id": model_id,
        "voice_settings": {"stability": 0.50, "similarity_boost": 0.75},
    }
    try:
        resp = http_req.post(url, json=payload, headers=headers, timeout=30, stream=True)
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("TTS API error: %s", exc)
        return {"error": str(exc)}

    pcm_bytes = b"".join(resp.iter_content(chunk_size=4096))
    if not pcm_bytes:
        return {"error": "empty audio"}

    # Wrap raw PCM in a WAV header so the browser can play it
    import struct, io
    sample_rate = 22050
    num_samples = len(pcm_bytes) // 2
    wav_buf = io.BytesIO()
    wav_buf.write(b"RIFF")
    wav_buf.write(struct.pack("<I", 36 + len(pcm_bytes)))
    wav_buf.write(b"WAVE")
    wav_buf.write(b"fmt ")
    wav_buf.write(struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16))
    wav_buf.write(b"data")
    wav_buf.write(struct.pack("<I", len(pcm_bytes)))
    wav_buf.write(pcm_bytes)

    from starlette.responses import Response
    return Response(content=wav_buf.getvalue(), media_type="audio/wav")


_abort_event = asyncio.Event()


@app.post("/api/chat/abort")
async def api_chat_abort():
    """Signal the current chat request to stop."""
    sid = _pending_dashboard_session_id
    if sid:
        _flush_progress_entry(sid)
        await broadcast({
            "type": "task.progress.complete",
            "session_id": sid,
            "ts": time.time(),
        })
    _abort_event.set()
    return {"ok": True}


# ── WebSocket ────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    _clients.add(ws)
    logger.info("Dashboard client connected (%d total)", len(_clients))

    qsid = ws.query_params.get("session") or DEFAULT_SESSION_ID
    watch_sid = _normalize_session_id(qsid)
    _ensure_session(watch_sid)

    # Send current state snapshot on connect
    try:
        conv = _snapshot_conversation_for_client(watch_sid)
        provider_info = _get_provider_info()
        await ws.send_text(json.dumps({
            "type": "snapshot",
            "status": _current_status,
            "recent_events": _event_log[-30:],
            "conversation": conv,
            "session_id": watch_sid,
            "provider": provider_info,
        }, ensure_ascii=False, default=str))
    except Exception:
        pass

    try:
        while True:
            # Client may switch thread: {"type":"watch_session","session_id":"..."}
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(msg, dict) and msg.get("type") == "watch_session":
                nsid = _normalize_session_id(msg.get("session_id"))
                _ensure_session(nsid)
                watch_sid = nsid
                conv = _snapshot_conversation_for_client(watch_sid)
                await ws.send_text(json.dumps({
                    "type": "snapshot",
                    "status": _current_status,
                    "conversation": conv,
                    "session_id": watch_sid,
                    "ts": time.time(),
                }, ensure_ascii=False, default=str))
    except WebSocketDisconnect:
        pass
    finally:
        _clients.discard(ws)
        logger.info("Dashboard client disconnected (%d remaining)", len(_clients))


# ── Server runner ────────────────────────────────────────────────────

async def run_dashboard(host: str = "127.0.0.1", port: int = 8550):
    """Start the dashboard server (call from main.py as asyncio task)."""
    import uvicorn
    config = uvicorn.Config(
        app, host=host, port=port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)
    await server.serve()
