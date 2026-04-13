"""State machine for multi-step task execution with checkpointing and recovery."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, TYPE_CHECKING

from core.event_bus import EventBus
from core.planner import Planner, StepStatus, TaskPlan, TaskStep
from core.retry import RetryExhausted, RetryPolicy

if TYPE_CHECKING:
    from core.orchestrator import Orchestrator

logger = logging.getLogger(__name__)

CHECKPOINT_DIR = Path(__file__).resolve().parent.parent / "data" / "checkpoints"


class TaskStateMachine:
    def __init__(
        self,
        plan: TaskPlan,
        planner: Planner,
        event_bus: EventBus | None = None,
        retry_policy: RetryPolicy | None = None,
    ):
        self.plan = plan
        self.planner = planner
        self.event_bus = event_bus or EventBus()
        self.retry_policy = retry_policy or RetryPolicy(max_retries=2, backoff_base=1.5)
        self.results: dict[str, Any] = {}
        self.current_step_idx = 0

    async def run(self, orchestrator: Orchestrator) -> str:
        """Execute all steps in the plan, returning a final summary."""
        total = len(self.plan.steps)
        await self.event_bus.emit("plan.start", goal=self.plan.goal, total_steps=total)

        for idx, step in enumerate(self.plan.steps):
            self.current_step_idx = idx

            if not self._dependencies_met(step):
                step.status = StepStatus.SKIPPED
                logger.warning("Skipping step %s: unmet dependencies", step.id)
                continue

            step.status = StepStatus.RUNNING
            await self.event_bus.emit(
                "step.start",
                step_id=step.id,
                description=step.description,
                current=idx + 1,
                total=total,
            )

            try:
                result = await self.retry_policy.execute_with_retry(
                    self._execute_step, orchestrator, step,
                    on_retry=lambda attempt, err: self.event_bus.emit(
                        "step.retry", step_id=step.id, attempt=attempt, error=str(err)
                    ),
                )
                step.status = StepStatus.COMPLETED
                step.result = result
                self.results[step.id] = result
                self.save_checkpoint()
                await self.event_bus.emit(
                    "step.complete", step_id=step.id, current=idx + 1, total=total
                )

            except RetryExhausted as e:
                step.status = StepStatus.FAILED
                step.error = str(e.last_error)
                logger.error("Step %s failed after retries: %s", step.id, e.last_error)
                await self.event_bus.emit(
                    "step.failed", step_id=step.id, error=step.error
                )

                if step.fallback:
                    logger.info("Attempting replan for step %s", step.id)
                    new_plan = await self.planner.replan(self.plan, step, step.error)
                    remaining = [s for s in new_plan.steps if s.status == StepStatus.PENDING]
                    if remaining:
                        self.plan.steps = (
                            self.plan.steps[: idx + 1] + remaining
                        )
                        continue

                self.save_checkpoint()

            except Exception as e:
                step.status = StepStatus.FAILED
                step.error = str(e)
                logger.exception("Unexpected error in step %s", step.id)
                self.save_checkpoint()

        self.clear_checkpoint()
        return self._build_summary()

    async def _execute_step(self, orchestrator: Orchestrator, step: TaskStep) -> dict:
        """Execute a single step via the orchestrator."""
        step_prompt = step.description
        if step.tool_hint:
            step_prompt += f" (use tool: {step.tool_hint})"

        context_parts = []
        for dep_id in step.depends_on:
            if dep_id in self.results:
                context_parts.append(
                    f"Result of '{dep_id}': {json.dumps(self.results[dep_id], ensure_ascii=False, default=str)[:300]}"
                )

        if context_parts:
            step_prompt += "\n\nContext from previous steps:\n" + "\n".join(context_parts)

        response = await orchestrator.process(step_prompt)
        return {"response": response}

    def _dependencies_met(self, step: TaskStep) -> bool:
        for dep_id in step.depends_on:
            dep = next((s for s in self.plan.steps if s.id == dep_id), None)
            if dep is None or dep.status != StepStatus.COMPLETED:
                return False
        return True

    def _build_summary(self) -> str:
        parts = []
        for step in self.plan.steps:
            status_icon = {
                StepStatus.COMPLETED: "+",
                StepStatus.FAILED: "X",
                StepStatus.SKIPPED: "-",
            }.get(step.status, "?")
            result_text = ""
            if step.id in self.results:
                resp = self.results[step.id].get("response", "")
                result_text = f": {resp[:200]}" if resp else ""
            parts.append(f"[{status_icon}] {step.description}{result_text}")
        return "\n".join(parts)

    # --- Checkpointing ---

    def save_checkpoint(self):
        CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
        data = {
            "plan": self.plan.to_dict(),
            "results": {k: _safe_serialize(v) for k, v in self.results.items()},
            "current_step_idx": self.current_step_idx,
            "saved_at": time.time(),
        }
        path = CHECKPOINT_DIR / "latest.json"
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    def clear_checkpoint(self):
        path = CHECKPOINT_DIR / "latest.json"
        if path.exists():
            path.unlink()

    @classmethod
    def from_checkpoint(
        cls,
        planner: Planner,
        event_bus: EventBus | None = None,
    ) -> TaskStateMachine | None:
        path = CHECKPOINT_DIR / "latest.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            plan = TaskPlan.from_json(
                goal=data["plan"]["goal"],
                steps_json=data["plan"]["steps"],
            )
            for step_data, step_obj in zip(data["plan"]["steps"], plan.steps):
                step_obj.status = StepStatus(step_data.get("status", "pending"))
                step_obj.error = step_data.get("error")

            sm = cls(plan=plan, planner=planner, event_bus=event_bus)
            sm.results = data.get("results", {})
            sm.current_step_idx = data.get("current_step_idx", 0)
            logger.info("Restored checkpoint: %s at step %d", plan.goal[:60], sm.current_step_idx)
            return sm
        except Exception as e:
            logger.warning("Failed to restore checkpoint: %s", e)
            return None


def _safe_serialize(obj: Any) -> Any:
    try:
        json.dumps(obj)
        return obj
    except (TypeError, ValueError):
        return str(obj)
