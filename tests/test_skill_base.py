"""Tests for BaseSkill and SkillRegistry."""

import pytest

from core.skill_base import BaseSkill, SkillRegistry
from tests.conftest import DummySkill


class StringAnnotatedSkill(BaseSkill):
    """Exercise postponed/string annotations in tool schemas and execution."""

    name = "string_annotated"
    description = "Skill with string annotations"

    async def do_count(self, question_index: "int", enabled: "bool" = False) -> dict:
        """Return the parsed values."""
        return {
            "question_index": question_index,
            "enabled": enabled,
        }


class TestBaseSkill:
    def test_get_actions(self, dummy_skill):
        actions = dummy_skill.get_actions()
        assert "greet" in actions
        assert "add" in actions

    def test_as_tools(self, dummy_skill):
        tools = dummy_skill.as_tools()
        assert len(tools) >= 2

        # Check tool format
        greet_tool = next(t for t in tools if "greet" in t["function"]["name"])
        assert greet_tool["type"] == "function"
        assert "dummy_greet" == greet_tool["function"]["name"]
        assert "parameters" in greet_tool["function"]
        assert greet_tool["function"]["parameters"]["type"] == "object"

    def test_tool_parameters(self, dummy_skill):
        tools = dummy_skill.as_tools()

        add_tool = next(t for t in tools if "add" in t["function"]["name"])
        params = add_tool["function"]["parameters"]
        assert "a" in params["properties"]
        assert "b" in params["properties"]
        assert "a" in params["required"]
        assert "b" in params["required"]

    def test_tool_optional_params(self, dummy_skill):
        tools = dummy_skill.as_tools()
        greet_tool = next(t for t in tools if "greet" in t["function"]["name"])
        params = greet_tool["function"]["parameters"]
        # 'name' has a default so should NOT be required
        assert "name" not in params["required"]

    @pytest.mark.asyncio
    async def test_execute_greet(self, dummy_skill):
        result = await dummy_skill.execute("greet", {"name": "Test"})
        assert result == {"message": "Hello, Test!"}

    @pytest.mark.asyncio
    async def test_execute_greet_default(self, dummy_skill):
        result = await dummy_skill.execute("greet")
        assert result == {"message": "Hello, World!"}

    @pytest.mark.asyncio
    async def test_execute_add(self, dummy_skill):
        result = await dummy_skill.execute("add", {"a": 3, "b": 5})
        assert result == {"result": 8}

    @pytest.mark.asyncio
    async def test_execute_unknown_action(self, dummy_skill):
        result = await dummy_skill.execute("nonexistent")
        assert "error" in result

    def test_as_tools_resolves_string_annotations(self):
        tools = StringAnnotatedSkill().as_tools()
        count_tool = next(t for t in tools if t["function"]["name"] == "string_annotated_count")
        params = count_tool["function"]["parameters"]["properties"]

        assert params["question_index"]["type"] == "integer"
        assert params["enabled"]["type"] == "boolean"

    @pytest.mark.asyncio
    async def test_execute_coerces_string_annotations(self):
        result = await StringAnnotatedSkill().execute(
            "count",
            {"question_index": "2", "enabled": "false"},
        )

        assert result == {"question_index": 2, "enabled": False}


class TestSkillRegistry:
    def test_register_and_get(self, registry, dummy_skill):
        registry.register(dummy_skill)
        assert registry.get("dummy") is dummy_skill

    def test_get_nonexistent(self, registry):
        assert registry.get("nonexistent") is None

    def test_all_skills(self, registry, dummy_skill):
        registry.register(dummy_skill)
        skills = registry.all_skills()
        assert len(skills) == 1
        assert skills[0] is dummy_skill

    def test_get_all_tools(self, registry, dummy_skill):
        registry.register(dummy_skill)
        tools = registry.get_all_tools()
        assert len(tools) >= 2
        names = [t["function"]["name"] for t in tools]
        assert "dummy_greet" in names
        assert "dummy_add" in names

    def test_resolve_tool_call(self, registry, dummy_skill):
        registry.register(dummy_skill)

        result = registry.resolve_tool_call("dummy_greet")
        assert result is not None
        skill, action = result
        assert skill is dummy_skill
        assert action == "greet"

    def test_resolve_tool_call_unknown(self, registry, dummy_skill):
        registry.register(dummy_skill)
        result = registry.resolve_tool_call("unknown_action")
        assert result is None

    def test_multiple_skills(self, registry):

        class SkillA(BaseSkill):
            name = "alpha"
            description = "Alpha"
            async def execute(self, action, params=None):
                return {}
            async def do_run(self):
                """Run alpha."""
                return {}

        class SkillB(BaseSkill):
            name = "beta"
            description = "Beta"
            async def execute(self, action, params=None):
                return {}
            async def do_go(self):
                """Go beta."""
                return {}

        registry.register(SkillA())
        registry.register(SkillB())

        assert len(registry.all_skills()) == 2
        tools = registry.get_all_tools()
        names = [t["function"]["name"] for t in tools]
        assert "alpha_run" in names
        assert "beta_go" in names

        assert registry.resolve_tool_call("alpha_run") is not None
        assert registry.resolve_tool_call("beta_go") is not None
