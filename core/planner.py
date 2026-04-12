"""Task planner — LLM-based decomposition of complex requests into executable steps."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum

import ollama

from config import get_settings
from config.settings import ollama_runtime_options
from core.event_bus import EventBus

logger = logging.getLogger(__name__)

COMPLEXITY_PROMPT = """You are a task complexity classifier for an AI assistant.

IMPORTANT RULES:
- Questions, greetings, opinions, explanations, conversations = ALWAYS "simple" (even if long)
- Single actions (turn on light, play music, take screenshot) = "simple"
- ONLY say "complex" if the request clearly needs 3+ DIFFERENT tool calls in sequence that depend on each other

Examples:
- "מה קורה?" → simple (conversation)
- "הוספתי לך משהו חדש, ראית?" → simple (conversation)
- "תדליק את האור" → simple (one tool call)
- "תחפש מודל 3D, תוריד אותו, ותפתח בCreality" → complex (3 dependent steps)
- "מה דעתך על הקוד שכתבתי?" → simple (conversation)
- "explain how this works" → simple (conversation)

Available tools:
{tools_summary}

User request: {user_input}

Reply with ONLY one word: "simple" or "complex"."""

PLANNING_PROMPT = """You are a task planner for an AI assistant called Jarvis.
Break down the user's request into sequential steps that can be executed with the available tools.

Available tools:
{tools_summary}

User request: {user_input}

Return a JSON array of steps. Each step has:
- "id": short identifier like "step_1"
- "description": what this step does
- "tool_hint": which tool to use (format: "skill_action", e.g. "models_search") or null if it's a thinking/response step
- "depends_on": array of step IDs this depends on (empty array if none)
- "fallback": alternative approach description if this step fails, or null

Return ONLY the JSON array, no markdown fences, no explanation.

Example:
[
  {{"id": "step_1", "description": "Search for 3D model", "tool_hint": "models_search", "depends_on": [], "fallback": "Ask user for direct URL"}},
  {{"id": "step_2", "description": "Download the best result", "tool_hint": "models_download", "depends_on": ["step_1"], "fallback": null}}
]"""

REPLAN_PROMPT = """A step in the plan failed. Adjust the remaining plan.

Original plan:
{plan_json}

Failed step: {failed_step}
Error: {error}

Provide an updated plan (JSON array of remaining steps) that works around this failure.
Return ONLY the JSON array."""


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class TaskStep:
    id: str
    description: str
    tool_hint: str | None = None
    depends_on: list[str] = field(default_factory=list)
    fallback: str | None = None
    status: StepStatus = StepStatus.PENDING
    result: dict | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "description": self.description,
            "tool_hint": self.tool_hint,
            "depends_on": self.depends_on,
            "fallback": self.fallback,
            "status": self.status.value,
            "error": self.error,
        }


@dataclass
class TaskPlan:
    goal: str
    steps: list[TaskStep]
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "goal": self.goal,
            "steps": [s.to_dict() for s in self.steps],
            "created_at": self.created_at,
        }

    @staticmethod
    def from_json(goal: str, steps_json: list[dict]) -> TaskPlan:
        steps = []
        for s in steps_json:
            steps.append(TaskStep(
                id=s.get("id", f"step_{len(steps)+1}"),
                description=s.get("description", ""),
                tool_hint=s.get("tool_hint"),
                depends_on=s.get("depends_on", []),
                fallback=s.get("fallback"),
            ))
        return TaskPlan(goal=goal, steps=steps)


class Planner:
    def __init__(self, event_bus: EventBus | None = None):
        self.settings = get_settings()
        self.event_bus = event_bus or EventBus()
        self.__client = None

    @property
    def _client(self):
        if self.__client is None:
            self.__client = ollama.AsyncClient(host=self.settings.ollama_host)
        return self.__client

    async def should_plan(self, user_input: str, tools_summary: str) -> bool:
        """Ask the LLM if this request needs multi-step planning."""
        prompt = COMPLEXITY_PROMPT.format(
            tools_summary=tools_summary,
            user_input=user_input,
        )
        try:
            kw = {
                "model": self.settings.ollama_model,
                "messages": [{"role": "user", "content": prompt + "\n/no_think"}],
            }
            oopts = ollama_runtime_options(self.settings)
            if oopts:
                kw["options"] = oopts
            response = await self._client.chat(**kw)
            answer = response.message.content.strip().lower()
            return "complex" in answer
        except Exception as e:
            logger.warning("Complexity check failed, defaulting to simple: %s", e)
            return False

    async def create_plan(self, user_input: str, tools_summary: str) -> TaskPlan:
        """Decompose a complex request into a TaskPlan."""
        prompt = PLANNING_PROMPT.format(
            tools_summary=tools_summary,
            user_input=user_input,
        )
        try:
            kw = {
                "model": self.settings.ollama_model,
                "messages": [
                    {"role": "system", "content": "You are a task planner. Return ONLY valid JSON."},
                    {"role": "user", "content": prompt},
                ],
            }
            oopts = ollama_runtime_options(self.settings)
            if oopts:
                kw["options"] = oopts
            response = await self._client.chat(**kw)
            raw = response.message.content.strip()
            raw = _strip_json_fences(raw)
            steps_data = json.loads(raw)
            plan = TaskPlan.from_json(goal=user_input, steps_json=steps_data)
            await self.event_bus.emit("plan.created", plan=plan.to_dict())
            logger.info("Created plan with %d steps for: %s", len(plan.steps), user_input[:80])
            return plan
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning("Failed to parse plan, creating single-step fallback: %s", e)
            return TaskPlan(
                goal=user_input,
                steps=[TaskStep(id="step_1", description=user_input)],
            )

    async def replan(self, plan: TaskPlan, failed_step: TaskStep, error: str) -> TaskPlan:
        """Adjust plan after a step failure."""
        prompt = REPLAN_PROMPT.format(
            plan_json=json.dumps(plan.to_dict(), indent=2),
            failed_step=json.dumps(failed_step.to_dict(), indent=2),
            error=error,
        )
        try:
            kw = {
                "model": self.settings.ollama_model,
                "messages": [
                    {"role": "system", "content": "You are a task planner. Return ONLY valid JSON."},
                    {"role": "user", "content": prompt},
                ],
            }
            oopts = ollama_runtime_options(self.settings)
            if oopts:
                kw["options"] = oopts
            response = await self._client.chat(**kw)
            raw = _strip_json_fences(response.message.content.strip())
            steps_data = json.loads(raw)
            new_plan = TaskPlan.from_json(goal=plan.goal, steps_json=steps_data)
            logger.info("Replanned with %d steps after failure", len(new_plan.steps))
            return new_plan
        except Exception as e:
            logger.warning("Replanning failed: %s", e)
            return plan


def _strip_json_fences(text: str) -> str:
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines)
    return text
