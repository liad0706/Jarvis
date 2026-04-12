import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.feedback_loop import FeedbackLoop
from core.proactive_engine import ProactiveEngine


class FakeInnerMemory:
    async def get_episodic_memories(self, memory_type="intention", limit=5):
        return []

    async def get_all_facts(self):
        return {"user": "dido"}


class FakeClient:
    def __init__(self, payload: dict):
        self.payload = payload

    async def chat(self, model, messages):
        return SimpleNamespace(
            message=SimpleNamespace(content=json.dumps(self.payload, ensure_ascii=False))
        )


class FakeMemoryManager:
    def __init__(self, payload: dict):
        self.settings = SimpleNamespace(ollama_model="qwen2.5:7b")
        self._client = FakeClient(payload)
        self.memory = FakeInnerMemory()


class FakeAwareness:
    def __init__(self, feedback_loop):
        self.feedback_loop = feedback_loop
        self.action_journal = None

    async def snapshot(self, include_discoveries=False):
        return {}

    def format_for_prompt(self, snap):
        return "Lights are on"


class FakeNotifications:
    def __init__(self):
        self.items = []

    async def notify(self, title, message, source="", **kwargs):
        self.items.append({"title": title, "message": message, "source": source})


@pytest.mark.asyncio
async def test_proactive_engine_suppresses_repeated_reason_and_records_feedback(tmp_path):
    feedback = FeedbackLoop(filepath=str(tmp_path / "feedback.json"))
    awareness = FakeAwareness(feedback)
    notifications = FakeNotifications()
    payload = {
        "should_notify": True,
        "reason_code": "LIGHTS_LATE",
        "details": "האור בסלון דולק",
        "priority": "medium",
    }
    engine = ProactiveEngine(
        FakeMemoryManager(payload),
        awareness=awareness,
        notifications=notifications,
    )

    await engine.check()

    assert len(notifications.items) == 1
    entries = feedback.get_all()
    assert len(entries) == 1
    assert entries[0]["action_type"] == "proactive_suggestion"
    assert entries[0]["reaction"] is None

    engine._unanswered_count = 0
    engine._last_any_send = 0

    await engine.check()

    assert len(notifications.items) == 1
    assert len(feedback.get_all()) == 1


@pytest.mark.asyncio
async def test_proactive_engine_marks_pending_suggestion_positive_on_reply(tmp_path):
    feedback = FeedbackLoop(filepath=str(tmp_path / "feedback.json"))
    awareness = FakeAwareness(feedback)
    notifications = FakeNotifications()
    payload = {
        "should_notify": True,
        "reason_code": "LIGHTS_LATE",
        "details": "האור בסלון דולק",
        "priority": "medium",
    }
    engine = ProactiveEngine(
        FakeMemoryManager(payload),
        awareness=awareness,
        notifications=notifications,
    )

    await engine.check()
    engine.user_responded()

    entries = feedback.get_all()
    assert len(entries) == 1
    assert entries[0]["reaction"] == "positive"
    assert entries[0]["reaction_signal"] == "user_replied"
