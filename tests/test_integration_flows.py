import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from core.automation import Automation, AutomationAction, AutomationEngine
from core.conversation_branch import BranchManager
from core.event_bus import EventBus
from core.notifications import NotificationManager
from core.skill_base import BaseSkill, SkillRegistry
from dashboard import server


def _reset_dashboard_state():
    server._session_store = None
    server._orchestrator = None
    server._chat_sessions.clear()
    server._pending_progress_by_session.clear()
    server._clients.clear()
    server._event_log.clear()
    server._current_status.update({"state": "idle", "detail": "", "ts": time.time()})


class TrackingSkill(BaseSkill):
    name = "tracking"
    description = "Tracks automation calls during integration tests"

    def __init__(self):
        self.calls = []

    async def execute(self, action: str, params: dict | None = None) -> dict:
        method = getattr(self, f"do_{action}", None)
        if not method:
            return {"error": f"Unknown action: {action}"}
        return await method(**(params or {}))

    async def do_record(self, mode: str = "") -> dict:
        self.calls.append({"mode": mode})
        return {"status": "ok", "mode": mode}


class FakeBranchOrchestrator:
    def __init__(self):
        self.provider = SimpleNamespace(name="ollama", model="qwen2.5:1.5b")
        self._circuit = SimpleNamespace(_state="closed", _failure_count=0)
        self.memory_manager = None
        self.conversation = [
            {"role": "user", "content": "Original question"},
            {"role": "assistant", "content": "Original answer"},
        ]
        self.branch_manager = BranchManager()

    def list_branches(self):
        return self.branch_manager.list_branches()


class FakeStreamingOrchestrator:
    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
        self.provider = SimpleNamespace(name="ollama", model="qwen2.5:1.5b")
        self._circuit = SimpleNamespace(_state="closed", _failure_count=0)
        self.memory_manager = None
        self.conversation = []
        self.branch_manager = BranchManager()
        self.last_response_streamed = False

    async def handle(self, text: str) -> str:
        self.conversation.append({"role": "user", "content": text})
        self.last_response_streamed = True
        await self.event_bus.emit("stream.start")
        await self.event_bus.emit("stream.token", token="Hello ")
        await self.event_bus.emit("stream.token", token="world")
        await self.event_bus.emit("stream.complete", full_text="Hello world")
        await self.event_bus.emit(
            "notification",
            id="n_integration",
            title="Heads up",
            message="Hello world",
            level="info",
            source="integration",
            action_url="",
            timestamp=time.time(),
            read=False,
        )
        self.conversation.append({"role": "assistant", "content": "Hello world"})
        return "Hello world"


class FakeProgressOrchestrator(FakeStreamingOrchestrator):
    async def handle(self, text: str) -> str:
        self.conversation.append({"role": "user", "content": text})
        await self.event_bus.emit("task.progress", summary="Checking Spotify devices")
        await self.event_bus.emit("task.progress", summary="Moving playback to Game Room")
        self.conversation.append({"role": "assistant", "content": "Done"})
        return "Done"


@pytest.mark.asyncio
async def test_automation_chain_runs_actions_and_notifications(tmp_path, monkeypatch):
    monkeypatch.setattr("core.automation.AUTOMATIONS_FILE", tmp_path / "automations.json")

    event_bus = EventBus()
    notifications = NotificationManager(event_bus=event_bus)
    notifications._desktop_enabled = False

    registry = SkillRegistry()
    tracking = TrackingSkill()
    registry.register(tracking)

    engine = AutomationEngine(event_bus=event_bus, registry=registry, notifications=notifications)
    engine.add_automation(
        Automation(
            name="Welcome Home",
            trigger_event="presence.home",
            actions=[
                AutomationAction(
                    skill_name="tracking",
                    action_name="record",
                    params={"mode": "arrive"},
                )
            ],
            cooldown_seconds=0,
        )
    )

    await event_bus.emit("presence.home", source="integration-test")
    await asyncio.sleep(0.05)

    assert tracking.calls == [{"mode": "arrive"}]
    history = notifications.get_history(limit=1)
    assert history[0]["source"] == "automation"
    assert history[0]["title"]


def test_branch_api_forks_and_opens_new_session(tmp_path, monkeypatch):
    monkeypatch.setattr("core.conversation_branch.BRANCHES_DIR", tmp_path / "branches")
    _reset_dashboard_state()

    orchestrator = FakeBranchOrchestrator()
    server.bridge_orchestrator(orchestrator)

    client = TestClient(server.app)

    fork = client.post("/api/branches/fork", json={"session_id": "default", "label": "Alternate plan"})
    assert fork.status_code == 200
    branch_id = fork.json()["branch"]["branch_id"]

    listed = client.get("/api/branches")
    assert listed.status_code == 200
    assert any(branch["branch_id"] == branch_id for branch in listed.json()["branches"])

    opened = client.post(f"/api/branches/{branch_id}/open")
    assert opened.status_code == 200
    opened_payload = opened.json()
    assert opened_payload["title"] == "Alternate plan"
    assert opened_payload["session_id"] != "default"

    transcript = client.get("/api/conversation", params={"session_id": opened_payload["session_id"]})
    assert transcript.status_code == 200
    assert transcript.json()[0]["content"] == "Original question"


def test_dashboard_websocket_broadcasts_full_chat_and_notifications(tmp_path, monkeypatch):
    monkeypatch.setattr("core.conversation_branch.BRANCHES_DIR", tmp_path / "branches_ws")
    _reset_dashboard_state()

    event_bus = EventBus()
    orchestrator = FakeStreamingOrchestrator(event_bus)
    server.bridge_event_bus(event_bus)
    server.bridge_orchestrator(orchestrator)

    client = TestClient(server.app)
    with client.websocket_connect("/ws") as ws:
        snapshot = ws.receive_json()
        assert snapshot["type"] == "snapshot"

        chat = client.post("/api/chat", json={"message": "Ping", "session_id": "default"})
        assert chat.status_code == 200
        assert chat.json()["response"] == "Hello world"

        seen = set()
        assistant_messages = []
        for _ in range(3):
            payload = ws.receive_json()
            seen.add(payload["type"])
            if payload["type"] == "chat.assistant":
                assistant_messages.append(payload["content"])

        assert "chat.user" in seen
        assert "chat.assistant" in seen
        assert "notification" in seen
        assert assistant_messages == ["Hello world"]


def test_dashboard_chat_persists_progress_summary_between_user_and_reply(tmp_path, monkeypatch):
    monkeypatch.setattr("core.conversation_branch.BRANCHES_DIR", tmp_path / "branches_progress")
    _reset_dashboard_state()

    event_bus = EventBus()
    orchestrator = FakeProgressOrchestrator(event_bus)
    server.bridge_event_bus(event_bus)
    server.bridge_orchestrator(orchestrator)

    client = TestClient(server.app)
    with client.websocket_connect("/ws") as ws:
        snapshot = ws.receive_json()
        assert snapshot["type"] == "snapshot"

        chat = client.post("/api/chat", json={"message": "Move the music", "session_id": "default"})
        assert chat.status_code == 200
        assert chat.json()["response"] == "Done"

        task_progress = []
        for _ in range(4):
            payload = ws.receive_json()
            if payload["type"] == "task.progress":
                task_progress.append(payload)

        assert [item["summary"] for item in task_progress] == [
            "Checking Spotify devices",
            "Moving playback to Game Room",
        ]
        assert all(item["session_id"] == "default" for item in task_progress)

    transcript = client.get("/api/conversation", params={"session_id": "default"})
    assert transcript.status_code == 200
    assert transcript.json() == [
        {"role": "user", "content": "Move the music"},
        {
            "role": "assistant",
            "type": "progress",
            "content": "- Checking Spotify devices\n- Moving playback to Game Room",
        },
        {"role": "assistant", "content": "Done"},
    ]
