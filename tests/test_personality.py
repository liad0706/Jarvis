"""Tests for personality and prompt building."""

import pytest

from core.personality import build_system_prompt, build_skills_summary, text_contains_hebrew
from tests.conftest import DummySkill


class TestPersonality:
    def test_build_system_prompt_with_facts(self):
        prompt = build_system_prompt(
            skills_summary="- test skill",
            facts={"name": "User", "barber": "ישי פרץ"},
        )
        assert "Jarvis" in prompt
        assert "- test skill" in prompt
        assert "name: User" in prompt
        assert "barber: ישי פרץ" in prompt

    def test_build_system_prompt_no_facts(self):
        prompt = build_system_prompt(skills_summary="- skill", facts={})
        assert "None yet" in prompt

    def test_build_system_prompt_contains_date(self):
        prompt = build_system_prompt(skills_summary="", facts={})
        # Should contain current date in YYYY-MM-DD format
        import re
        assert re.search(r"\d{4}-\d{2}-\d{2}", prompt)

    def test_hebrew_locale_appends_strict_rules(self):
        prompt = build_system_prompt(
            skills_summary="- x", facts={}, user_message_for_locale="תדליק את האור"
        )
        assert "Hebrew-only output" in prompt
        assert "Franglish" in prompt
        assert text_contains_hebrew("שלום")
        assert not text_contains_hebrew("hello")

    def test_build_skills_summary(self):
        skill = DummySkill()
        summary = build_skills_summary([skill])
        assert "dummy" in summary
        assert "greet" in summary
        assert "add" in summary
        assert "A dummy skill for testing" in summary

    def test_build_skills_summary_empty(self):
        summary = build_skills_summary([])
        assert summary == ""
