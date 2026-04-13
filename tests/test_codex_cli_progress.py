import json

import pytest

from core.orchestrator import Orchestrator
from core.providers import CodexCLIProvider
from core.skill_base import SkillRegistry


class _FakePipe:
    def __init__(self, lines=None, data=b""):
        self._lines = [line.encode("utf-8") for line in (lines or [])]
        self._data = data
        self.written = b""
        self.closed = False

    async def readline(self):
        if self._lines:
            return self._lines.pop(0)
        return b""

    async def read(self):
        data = self._data
        self._data = b""
        return data

    def write(self, data):
        self.written += data

    async def drain(self):
        return None

    def close(self):
        self.closed = True


class _FakeProcess:
    def __init__(self, stdout_lines, stderr_text="", returncode=0):
        self.stdin = _FakePipe()
        self.stdout = _FakePipe(lines=stdout_lines)
        self.stderr = _FakePipe(data=stderr_text.encode("utf-8"))
        self.returncode = returncode

    async def wait(self):
        return self.returncode


@pytest.mark.asyncio
async def test_codex_cli_emits_progress_and_keeps_only_last_message(monkeypatch, event_bus):
    events = [
        {"type": "thread.started", "thread_id": "t1"},
        {"type": "turn.started"},
        {"type": "item.completed", "item": {"id": "1", "type": "agent_message", "text": "בודק את הכלים הרלוונטיים עכשיו."}},
        {"type": "item.completed", "item": {"id": "2", "type": "agent_message", "text": "פותח את קבצי השליטה כדי לאתר את הפעולה הנכונה."}},
        {"type": "item.completed", "item": {"id": "3", "type": "agent_message", "text": "הפעולה מוכנה."}},
        {"type": "turn.completed"},
    ]
    lines = [json.dumps(event, ensure_ascii=False) + "\n" for event in events]
    fake_proc = _FakeProcess(lines)

    async def fake_spawn(self):
        return fake_proc

    monkeypatch.setattr(CodexCLIProvider, "_spawn_process", fake_spawn)

    progress = []

    async def on_progress(**kwargs):
        progress.append(kwargs["summary"])

    event_bus.on("task.progress", on_progress)

    provider = CodexCLIProvider(model="gpt-5.4").bind_event_bus(event_bus)
    response = await provider.chat([{"role": "user", "content": "test"}])

    assert progress == [
        "בודק את הכלים הרלוונטיים עכשיו.",
        "פותח את קבצי השליטה כדי לאתר את הפעולה הנכונה.",
    ]
    assert response.content == "הפעולה מוכנה."
    assert "בודק את הכלים" not in response.content
    assert fake_proc.stdin.closed is True
    assert b"[User]" in fake_proc.stdin.written


@pytest.mark.asyncio
async def test_codex_cli_single_agent_message_stays_final(monkeypatch, event_bus):
    events = [
        {"type": "thread.started", "thread_id": "t1"},
        {"type": "turn.started"},
        {"type": "item.completed", "item": {"id": "1", "type": "agent_message", "text": "תשובה אחת בלבד."}},
        {"type": "turn.completed"},
    ]
    lines = [json.dumps(event, ensure_ascii=False) + "\n" for event in events]

    async def fake_spawn(self):
        return _FakeProcess(lines)

    monkeypatch.setattr(CodexCLIProvider, "_spawn_process", fake_spawn)

    progress = []

    async def on_progress(**kwargs):
        progress.append(kwargs["summary"])

    event_bus.on("task.progress", on_progress)

    provider = CodexCLIProvider(model="gpt-5.4").bind_event_bus(event_bus)
    response = await provider.chat([{"role": "user", "content": "test"}])

    assert progress == []
    assert response.content == "תשובה אחת בלבד."


@pytest.mark.asyncio
async def test_orchestrator_binds_event_bus_to_provider(memory):
    class BoundProvider:
        name = "codex-cli"
        model = "gpt-5.4"

        def __init__(self):
            self.bound_bus = None

        def bind_event_bus(self, event_bus):
            self.bound_bus = event_bus
            return self

        async def chat(self, messages, tools=None):
            raise AssertionError("chat should not run in this test")

    provider = BoundProvider()
    orchestrator = Orchestrator(SkillRegistry(), memory)

    bound = orchestrator._bind_provider(provider)

    assert bound is provider
    assert provider.bound_bus is orchestrator.event_bus
