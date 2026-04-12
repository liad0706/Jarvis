"""ReAct Agent — Reasoning + Acting loop for complex multi-step tasks.

Instead of single-shot tool calling, the ReAct agent iterates:
  1. THINK — reason about what to do next
  2. ACT — call a tool / skill
  3. OBSERVE — read the result
  4. Repeat until FINISH

This produces much better results for multi-step tasks compared to
the default single-pass orchestrator.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from core.providers import get_provider, LLMResponse, ToolCall
from core.skill_base import SkillRegistry

logger = logging.getLogger(__name__)

REACT_SYSTEM_PROMPT = """\
You are Jarvis, an AI assistant using the ReAct framework.
You solve tasks by iterating through Thought → Action → Observation cycles.

IMPORTANT RULES:
1. Always start with a Thought about what to do.
2. Then pick an Action (one of the available tools) with its Input.
3. After observing the result, think again about what to do next.
4. When you have the final answer, respond with:
   Thought: I now have the answer.
   Final Answer: <your answer here>

FORMAT:
Thought: <your reasoning>
Action: <tool_name>
Action Input: <json parameters>

OR when done:
Thought: <your reasoning>
Final Answer: <your response to the user>

Available tools:
{tools_description}
"""

MAX_REACT_STEPS = 10


@dataclass
class ReActStep:
    """One step in the ReAct loop."""
    step_num: int
    thought: str = ""
    action: str = ""
    action_input: dict = field(default_factory=dict)
    observation: str = ""
    timestamp: float = field(default_factory=time.time)


@dataclass
class ReActTrace:
    """Full execution trace of a ReAct run."""
    query: str
    steps: list[ReActStep] = field(default_factory=list)
    final_answer: str = ""
    total_time: float = 0.0
    success: bool = True


class ReActAgent:
    """Execute a query using the ReAct reasoning loop."""

    def __init__(
        self,
        registry: SkillRegistry,
        settings=None,
        max_steps: int = MAX_REACT_STEPS,
        permission_gate=None,
        audit_log=None,
    ):
        self.registry = registry
        self.settings = settings
        self.max_steps = max_steps
        self.permission_gate = permission_gate
        self.audit_log = audit_log
        self._provider = None

    def _get_provider(self):
        if self._provider is None:
            from config import get_settings
            s = self.settings or get_settings()
            self._provider = get_provider(s)
        return self._provider

    def _build_tools_description(self) -> str:
        """Build a text description of all available tools."""
        lines = []
        for skill in self.registry.all_skills():
            for tool_def in skill.as_tools():
                func = tool_def["function"]
                params = func.get("parameters", {}).get("properties", {})
                param_desc = ", ".join(
                    f"{k}: {v.get('type', 'string')}" for k, v in params.items()
                )
                lines.append(f"- {func['name']}({param_desc}): {func['description']}")
        return "\n".join(lines)

    def _parse_react_response(self, text: str) -> dict:
        """Parse LLM output into thought/action/final_answer."""
        result = {"thought": "", "action": "", "action_input": {}, "final_answer": ""}

        lines = text.strip().split("\n")
        current_key = None
        current_lines: list[str] = []

        for line in lines:
            lower = line.strip().lower()
            if lower.startswith("thought:"):
                if current_key:
                    result[current_key] = "\n".join(current_lines).strip()
                current_key = "thought"
                current_lines = [line.split(":", 1)[1].strip()]
            elif lower.startswith("action:") and "input" not in lower:
                if current_key:
                    result[current_key] = "\n".join(current_lines).strip()
                current_key = "action"
                current_lines = [line.split(":", 1)[1].strip()]
            elif lower.startswith("action input:"):
                if current_key:
                    result[current_key] = "\n".join(current_lines).strip()
                current_key = "action_input_raw"
                current_lines = [line.split(":", 1)[1].strip()]
            elif lower.startswith("final answer:"):
                if current_key:
                    result[current_key] = "\n".join(current_lines).strip()
                current_key = "final_answer"
                current_lines = [line.split(":", 1)[1].strip()]
            else:
                current_lines.append(line)

        if current_key:
            result[current_key] = "\n".join(current_lines).strip()

        # Parse action input JSON
        raw_input = result.pop("action_input_raw", "")
        if raw_input:
            try:
                result["action_input"] = json.loads(raw_input)
            except json.JSONDecodeError:
                result["action_input"] = {"raw": raw_input}

        return result

    async def _execute_action(self, tool_name: str, params: dict) -> str:
        """Execute a tool/skill action and return the result as string."""
        resolved = self.registry.resolve_tool_call(tool_name)
        if not resolved:
            return f"Error: Unknown tool '{tool_name}'. Check available tools."

        skill, action = resolved

        # Permission check
        if self.permission_gate:
            risk = skill.RISK_MAP.get(action, "READ")
            allowed = await self.permission_gate.check(
                skill_name=skill.name, action=action, risk_level=risk
            )
            if not allowed:
                return f"Permission denied for {skill.name}.{action} (risk: {risk})"

        try:
            result = await skill.execute(action, params)
            if isinstance(result, dict):
                return json.dumps(result, ensure_ascii=False, default=str)
            return str(result)
        except Exception as e:
            logger.warning("ReAct action failed: %s.%s — %s", skill.name, action, e)
            return f"Error executing {tool_name}: {e}"

    async def run(
        self,
        query: str,
        context: list[dict] | None = None,
        on_step: Any = None,
    ) -> ReActTrace:
        """Execute the ReAct loop for a query.

        Args:
            query: User's question/request.
            context: Optional conversation history.
            on_step: Optional async callback(step: ReActStep) for real-time updates.

        Returns:
            ReActTrace with all steps and the final answer.
        """
        start = time.time()
        trace = ReActTrace(query=query)
        provider = self._get_provider()

        tools_desc = self._build_tools_description()
        system = REACT_SYSTEM_PROMPT.format(tools_description=tools_desc)

        messages = [{"role": "system", "content": system}]
        if context:
            messages.extend(context[-10:])  # last 10 for context
        messages.append({"role": "user", "content": query})

        for step_num in range(1, self.max_steps + 1):
            step = ReActStep(step_num=step_num)

            # Get LLM response
            try:
                response: LLMResponse = await provider.chat(
                    messages=messages,
                    tools=[],  # ReAct uses text-based tool calling
                )
                llm_text = response.content or ""
            except Exception as e:
                logger.error("ReAct LLM call failed at step %d: %s", step_num, e)
                step.thought = f"LLM error: {e}"
                trace.steps.append(step)
                trace.success = False
                break

            parsed = self._parse_react_response(llm_text)
            step.thought = parsed.get("thought", "")
            step.action = parsed.get("action", "")
            step.action_input = parsed.get("action_input", {})

            # Check for final answer
            if parsed.get("final_answer"):
                trace.final_answer = parsed["final_answer"]
                trace.steps.append(step)
                if on_step:
                    await on_step(step)
                break

            # Execute action
            if step.action:
                observation = await self._execute_action(step.action, step.action_input)
                step.observation = observation

                # Add to conversation for next iteration
                messages.append({"role": "assistant", "content": llm_text})
                messages.append({
                    "role": "user",
                    "content": f"Observation: {observation}\n\nContinue with your next Thought.",
                })
            else:
                # No action and no final answer — force conclusion
                step.observation = "No action specified. Please provide a Final Answer."
                messages.append({"role": "assistant", "content": llm_text})
                messages.append({
                    "role": "user",
                    "content": "You didn't specify an Action or Final Answer. Please provide your Final Answer now.",
                })

            trace.steps.append(step)
            if on_step:
                await on_step(step)

        # If we exhausted steps without a final answer
        if not trace.final_answer:
            trace.final_answer = "I wasn't able to complete the task within the step limit. Here's what I found so far:\n"
            for s in trace.steps:
                if s.observation:
                    trace.final_answer += f"\n- Step {s.step_num}: {s.observation[:200]}"
            trace.success = False

        trace.total_time = time.time() - start
        logger.info(
            "ReAct completed: %d steps, %.1fs, success=%s",
            len(trace.steps), trace.total_time, trace.success,
        )
        return trace
