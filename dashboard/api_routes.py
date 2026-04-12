"""Extended API routes for dashboard pages."""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

router = APIRouter(prefix="/api", tags=["dashboard-api"])

_orchestrator = None
_memory_manager = None
_awareness = None
_metrics = None
_notifications = None
_automation_engine = None
_registry = None
_skill_store = None
_model_router = None


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
    global _orchestrator, _memory_manager, _awareness, _metrics
    global _notifications, _automation_engine, _registry, _skill_store, _model_router
    _orchestrator = orchestrator
    _memory_manager = memory_manager
    _awareness = awareness
    _metrics = metrics
    _notifications = notifications
    _automation_engine = automation_engine
    _registry = registry
    _skill_store = skill_store
    _model_router = model_router


def _require_registry():
    if not _registry:
        raise HTTPException(503, "Registry not available")
    return _registry


def _require_skill(skill_name: str):
    skill = _require_registry().get(skill_name)
    if not skill:
        raise HTTPException(404, f"Skill not available: {skill_name}")
    return skill


def _normalize_device(device: dict[str, Any]) -> dict[str, Any]:
    attributes: dict[str, Any] = {}
    brightness = device.get("brightness")
    if brightness is not None:
        if isinstance(brightness, (int, float)) and brightness > 100:
            brightness = round((float(brightness) / 255.0) * 100)
        attributes["brightness"] = int(brightness)
    volume = device.get("volume")
    if volume is not None:
        attributes["volume"] = int(volume)

    return {
        "id": device.get("entity_id") or device.get("id") or device.get("name"),
        "entity_id": device.get("entity_id"),
        "name": device.get("name") or device.get("entity_id") or "Unknown device",
        "type": device.get("type", "unknown"),
        "state": device.get("state", "unknown"),
        "attributes": attributes,
        "source": device.get("source", ""),
    }


@router.get("/devices")
async def list_devices():
    registry = _require_registry()
    skill = registry.get("smart_home")
    if not skill:
        return {"devices": []}
    try:
        result = await skill.execute("list_devices", {})
        devices = [_normalize_device(device) for device in result.get("devices", [])]
        return {"devices": devices, "source": result.get("source", "")}
    except Exception as exc:
        return {"devices": [], "error": str(exc)}


@router.post("/devices/{device_id}/toggle")
async def toggle_device(device_id: str):
    skill = _require_registry().get("smart_home")
    if not skill:
        raise HTTPException(404, "Smart home skill not found")
    return await skill.execute("toggle", {"device": device_id})


class DeviceSetRequest(BaseModel):
    attribute: str
    value: Any


@router.post("/devices/{device_id}/set")
async def set_device(device_id: str, req: DeviceSetRequest):
    skill = _require_registry().get("smart_home")
    if not skill:
        raise HTTPException(404, "Smart home skill not found")

    attribute = req.attribute.strip().lower()
    if attribute == "brightness":
        return await skill.execute("set_brightness", {"device": device_id, "level": int(req.value)})
    if attribute == "color" and isinstance(req.value, list) and len(req.value) == 3:
        r, g, b = req.value
        return await skill.execute("set_color", {"device": device_id, "r": r, "g": g, "b": b})
    raise HTTPException(400, f"Unsupported device attribute: {req.attribute}")


@router.get("/memory/facts")
async def get_facts():
    if not _memory_manager:
        return {"facts": {}}
    facts = await _memory_manager.get_all_facts()
    return {"facts": facts}


@router.get("/memory/episodes")
async def get_episodes(limit: int = 50):
    if not _memory_manager:
        return {"episodes": []}
    episodes = await _memory_manager.get_episodes(limit=limit)
    return {"episodes": episodes}


@router.delete("/memory/episodes/{memory_id}")
async def delete_episode(memory_id: int):
    if not _memory_manager:
        raise HTTPException(404, "Memory not available")
    deleted = await _memory_manager.memory.delete_episodic_memory(memory_id)
    if not deleted:
        raise HTTPException(404, "Episode not found")
    return {"status": "deleted", "memory_id": memory_id}


@router.get("/memory/patterns")
async def get_patterns():
    if not _awareness or not _awareness.pattern_learner:
        return {"patterns": []}
    patterns = _awareness.pattern_learner.get_patterns(min_confidence=0.0)
    return {"patterns": patterns}


@router.get("/memory/feedback")
async def get_feedback(limit: int = 100):
    if not _awareness or not _awareness.feedback_loop:
        return {"feedback": []}
    feedback = _awareness.feedback_loop.get_all(limit=limit)
    return {"feedback": feedback}


@router.delete("/memory/facts/{key}")
async def delete_fact(key: str):
    if not _memory_manager:
        raise HTTPException(404, "Memory not available")
    deleted = await _memory_manager.memory.remove_fact(key)
    if not deleted:
        raise HTTPException(404, "Fact not found")
    return {"status": "deleted", "key": key}


@router.get("/memory/search")
async def search_memory(q: str = Query(...)):
    if not _memory_manager:
        return {"query": q, "results": []}

    relevant = await _memory_manager.get_relevant_history(q, top_k=10)
    facts = await _memory_manager.get_all_facts()
    matching_facts = {
        key: value
        for key, value in facts.items()
        if q.lower() in key.lower() or q.lower() in str(value).lower()
    }
    return {
        "query": q,
        "relevant_history": relevant,
        "matching_facts": matching_facts,
    }


@router.get("/calendar/events")
async def get_calendar_events(from_date: str = "", to_date: str = ""):
    if not _awareness or not _awareness.calendar:
        return {"events": []}

    events = _awareness.calendar.get_all_events()
    if from_date:
        events = [event for event in events if event.get("date", "") >= from_date]
    if to_date:
        events = [event for event in events if event.get("date", "") <= to_date]
    return {"events": events}


@router.get("/calendar/week")
async def get_calendar_week(start_date: str = ""):
    if not _awareness or not _awareness.calendar:
        return {"events": []}
    return {"events": _awareness.calendar.get_week(start_date or None)}


class CalendarEventRequest(BaseModel):
    title: str
    date: str
    time: str
    end_time: str = ""
    category: str = "personal"
    recurring: str = "once"
    recurring_days: list[int] | None = None
    reminder_minutes: int = 30


@router.post("/calendar/events")
async def add_calendar_event(req: CalendarEventRequest):
    if not _awareness or not _awareness.calendar:
        raise HTTPException(404, "Calendar not available")
    event = _awareness.calendar.add_event(
        title=req.title,
        date=req.date,
        time=req.time,
        end_time=req.end_time or None,
        category=req.category,
        recurring=req.recurring,
        recurring_days=req.recurring_days,
        reminder_minutes=req.reminder_minutes,
    )
    return {"status": "added", "event": event}


@router.delete("/calendar/events/{event_id}")
async def delete_calendar_event(event_id: str):
    if not _awareness or not _awareness.calendar:
        raise HTTPException(404, "Calendar not available")
    removed = _awareness.calendar.remove_event(event_id)
    if not removed:
        raise HTTPException(404, "Event not found")
    return {"status": "deleted", "event_id": event_id}


class DocumentIngestRequest(BaseModel):
    file_path: str


class DocumentAskRequest(BaseModel):
    question: str


class DocumentRemoveRequest(BaseModel):
    file_path: str


@router.get("/documents")
async def list_documents():
    skill = _require_skill("document_rag")
    return await skill.execute("list_documents", {})


@router.post("/documents/ingest")
async def ingest_document(req: DocumentIngestRequest):
    skill = _require_skill("document_rag")
    result = await skill.execute("ingest_file", {"file_path": req.file_path})
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


@router.post("/documents/query")
async def query_documents(req: DocumentAskRequest):
    skill = _require_skill("document_rag")
    result = await skill.execute("ask", {"question": req.question})
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


@router.post("/documents/remove")
async def remove_document(req: DocumentRemoveRequest):
    skill = _require_skill("document_rag")
    result = await skill.execute("remove_document", {"file_path": req.file_path})
    if "error" in result:
        raise HTTPException(404, result["error"])
    return result


@router.get("/health")
async def get_health():
    health: dict[str, Any] = {
        "status": "ok",
        "timestamp": time.time(),
        "llm": {},
        "devices": {},
        "memory": {},
        "circuit_breakers": {},
        "metrics": {},
        "notifications": {},
        "activity": {},
    }

    if _orchestrator:
        provider = _orchestrator.provider
        health["llm"] = {
            "provider": provider.name,
            "model": getattr(provider, "model", "unknown"),
            "routes": _model_router.get_all_routes() if _model_router else {},
        }
        health["circuit_breakers"]["llm"] = {
            "state": _orchestrator._circuit._state,
            "failures": _orchestrator._circuit._failure_count,
        }
        if getattr(_orchestrator, "activity_manager", None):
            health["activity"] = {
                "count": _orchestrator.activity_manager.count,
                "recent": _orchestrator.activity_manager.recent(limit=10),
            }

    if _registry and _registry.get("smart_home"):
        try:
            result = await _registry.get("smart_home").execute("list_devices", {})
            devices = result.get("devices", [])
            health["devices"] = {
                "count": len(devices),
                "on": sum(1 for device in devices if device.get("state") == "on"),
                "off": sum(1 for device in devices if device.get("state") == "off"),
            }
        except Exception as exc:
            health["devices"] = {"error": str(exc)}

    if _memory_manager:
        facts = await _memory_manager.get_all_facts()
        episodes = await _memory_manager.get_episodes(limit=200)
        health["memory"] = {
            "facts_count": len(facts),
            "episodes_count": len(episodes),
            "conversation_length": len(_orchestrator.conversation) if _orchestrator else 0,
        }

    if _metrics:
        health["metrics"] = await _metrics.get_summary()

    if _notifications:
        health["notifications"] = {
            "unread_count": _notifications.unread_count(),
            "recent": _notifications.get_history(limit=5),
        }

    return health


@router.get("/health/metrics")
async def get_metrics():
    if not _metrics:
        return {"metrics": {}}
    return {"metrics": await _metrics.get_summary()}


@router.get("/notifications")
async def get_notifications(unread_only: bool = False, limit: int = 50):
    if not _notifications:
        return {"notifications": [], "unread_count": 0}
    return {
        "notifications": _notifications.get_history(unread_only=unread_only, limit=limit),
        "unread_count": _notifications.unread_count(),
    }


@router.post("/notifications/{notification_id}/read")
async def mark_notification_read(notification_id: str):
    if not _notifications:
        raise HTTPException(404, "Notifications not available")
    _notifications.mark_read(notification_id)
    return {"status": "ok"}


@router.post("/notifications/read-all")
async def mark_all_read():
    if not _notifications:
        raise HTTPException(404, "Notifications not available")
    _notifications.mark_all_read()
    return {"status": "ok"}


@router.post("/system/restart")
async def restart_jarvis(reason: str = "", resume_message: str = ""):
    skill = _require_skill("restart")
    result = await skill.execute(
        "restart",
        {
            "reason": reason or "Dashboard requested restart",
            "resume_message": resume_message or "Jarvis restarted with the latest code changes.",
        },
    )
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


@router.get("/automations")
async def list_automations():
    if not _automation_engine:
        return {"automations": []}
    return {"automations": _automation_engine.list_automations()}


@router.get("/automations/events")
async def list_automation_events():
    return {
        "events": [
            "chat.user",
            "chat.assistant",
            "notification",
            "tool.complete",
            "plan.start",
            "step.complete",
            "stream.complete",
            "whatsapp.message",
            "presence.changed",
            "device.state_changed",
        ]
    }


class AutomationRequest(BaseModel):
    name: str
    trigger_event: str
    actions: list[dict]
    conditions: dict[str, Any] = {}
    cooldown_seconds: float = 60
    description: str = ""


@router.post("/automations")
async def add_automation(req: AutomationRequest):
    if not _automation_engine:
        raise HTTPException(404, "Automation engine not available")
    from core.automation import Automation, AutomationAction

    automation = Automation(
        name=req.name,
        trigger_event=req.trigger_event,
        actions=[AutomationAction.from_dict(action) for action in req.actions],
        conditions=req.conditions,
        cooldown_seconds=req.cooldown_seconds,
        description=req.description,
    )
    _automation_engine.add_automation(automation)
    return {"status": "added", "name": req.name}


@router.delete("/automations/{name}")
async def delete_automation(name: str):
    if not _automation_engine:
        raise HTTPException(404, "Automation engine not available")
    removed = _automation_engine.remove_automation(name)
    if not removed:
        raise HTTPException(404, f"Automation '{name}' not found")
    return {"status": "deleted"}


@router.get("/skills")
async def list_skills():
    if not _skill_store:
        return {"skills": []}
    return {"skills": _skill_store.get_all()}


class SkillExportRequest(BaseModel):
    output_dir: str = "data/exports"


@router.post("/skills/{skill_name}/enable")
async def enable_skill(skill_name: str):
    if not _skill_store:
        raise HTTPException(404, "Skill store not available")
    if not _skill_store.enable_skill(skill_name):
        raise HTTPException(404, "Skill not found")
    return {"status": "enabled", "skill": skill_name}


@router.post("/skills/{skill_name}/disable")
async def disable_skill(skill_name: str):
    if not _skill_store:
        raise HTTPException(404, "Skill store not available")
    dependents = _skill_store.dependents_of(skill_name)
    if dependents:
        raise HTTPException(409, f"Skill is required by: {', '.join(dependents)}")
    if not _skill_store.disable_skill(skill_name):
        raise HTTPException(404, "Skill not found")
    return {"status": "disabled", "skill": skill_name}


@router.post("/skills/{skill_name}/export")
async def export_skill(skill_name: str, req: SkillExportRequest):
    if not _skill_store:
        raise HTTPException(404, "Skill store not available")
    archive_path = _skill_store.export_skill_archive(skill_name, req.output_dir)
    if not archive_path:
        raise HTTPException(404, "Skill cannot be exported")
    return {"status": "exported", "archive_path": archive_path}


class SkillImportRequest(BaseModel):
    archive_path: str


@router.post("/skills/import")
async def import_skill(req: SkillImportRequest):
    if not _skill_store:
        raise HTTPException(404, "Skill store not available")
    if not _skill_store.import_skill_archive(req.archive_path):
        raise HTTPException(400, "Failed to import skill archive")
    return {"status": "imported"}


@router.get("/model-routes")
async def list_model_routes():
    if not _model_router:
        return {"routes": {}}
    return {"routes": _model_router.get_all_routes()}


class ModelRouteRequest(BaseModel):
    provider: str
    model: str | None = None


@router.post("/model-routes/{task_type}")
async def update_model_route(task_type: str, req: ModelRouteRequest):
    if not _model_router:
        raise HTTPException(404, "Model router not available")
    _model_router.set_route(task_type, req.provider, req.model)
    return {"status": "updated", "routes": _model_router.get_all_routes()}
