"""Tests for the task planner."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.event_bus import EventBus
from core.planner import Planner, TaskPlan, TaskStep, StepStatus


def _mock_ollama_response(content: str):
    msg = MagicMock()
    msg.content = content
    resp = MagicMock()
    resp.message = msg
    return resp


@pytest.mark.asyncio
class TestPlanner:
    async def test_should_plan_simple(self):
        planner = Planner()
        mock_resp = _mock_ollama_response("simple")
        with patch.object(planner._client, "chat", new_callable=AsyncMock, return_value=mock_resp):
            result = await planner.should_plan("play music", "tools: spotify")
        assert result is False

    async def test_should_plan_complex(self):
        planner = Planner()
        mock_resp = _mock_ollama_response("complex")
        with patch.object(planner._client, "chat", new_callable=AsyncMock, return_value=mock_resp):
            result = await planner.should_plan("search model, download, print", "tools: models, creality")
        assert result is True

    async def test_create_plan(self):
        planner = Planner(event_bus=EventBus())
        steps_json = json.dumps([
            {"id": "s1", "description": "Search model", "tool_hint": "models_search", "depends_on": [], "fallback": None},
            {"id": "s2", "description": "Download", "tool_hint": "models_download", "depends_on": ["s1"], "fallback": "Ask for URL"},
        ])
        mock_resp = _mock_ollama_response(steps_json)
        with patch.object(planner._client, "chat", new_callable=AsyncMock, return_value=mock_resp):
            plan = await planner.create_plan("search and download a benchy", "tools: models")

        assert len(plan.steps) == 2
        assert plan.steps[0].id == "s1"
        assert plan.steps[1].depends_on == ["s1"]

    async def test_create_plan_bad_json_fallback(self):
        planner = Planner()
        mock_resp = _mock_ollama_response("not valid json at all!")
        with patch.object(planner._client, "chat", new_callable=AsyncMock, return_value=mock_resp):
            plan = await planner.create_plan("do stuff", "tools: none")

        assert len(plan.steps) == 1
        assert plan.steps[0].description == "do stuff"


class TestTaskPlan:
    def test_from_json(self):
        data = [
            {"id": "a", "description": "step a"},
            {"id": "b", "description": "step b", "depends_on": ["a"]},
        ]
        plan = TaskPlan.from_json("goal", data)
        assert plan.goal == "goal"
        assert len(plan.steps) == 2
        assert plan.steps[1].depends_on == ["a"]

    def test_to_dict_roundtrip(self):
        plan = TaskPlan(
            goal="test",
            steps=[TaskStep(id="s1", description="do something")],
        )
        d = plan.to_dict()
        assert d["goal"] == "test"
        assert len(d["steps"]) == 1
