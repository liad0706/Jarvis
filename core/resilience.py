"""Resilience patterns — circuit breaker, rate limiter, timeout manager."""

from __future__ import annotations

import asyncio
import logging
import time
from enum import Enum
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Circuit Breaker
# ---------------------------------------------------------------------------

class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    """Raised when the circuit breaker is open and rejecting calls."""
    pass


class CircuitBreaker:
    """Trips open after consecutive failures, auto-resets after a cooldown."""

    def __init__(
        self,
        name: str = "default",
        failure_threshold: int = 5,
        reset_timeout: float = 60.0,
        half_open_max: int = 1,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.half_open_max = half_open_max

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time = 0.0
        self._half_open_calls = 0

    @property
    def state(self) -> CircuitState:
        if self._state == CircuitState.OPEN:
            if time.time() - self._last_failure_time >= self.reset_timeout:
                self._state = CircuitState.HALF_OPEN
                self._half_open_calls = 0
                logger.info("Circuit '%s' transitioning to HALF_OPEN", self.name)
        return self._state

    async def call(
        self,
        func: Callable[..., Coroutine[Any, Any, Any]],
        *args,
        **kwargs,
    ) -> Any:
        current_state = self.state

        if current_state == CircuitState.OPEN:
            raise CircuitOpenError(
                f"Circuit '{self.name}' is OPEN. Retry after {self.reset_timeout}s."
            )

        if current_state == CircuitState.HALF_OPEN:
            if self._half_open_calls >= self.half_open_max:
                raise CircuitOpenError(
                    f"Circuit '{self.name}' is HALF_OPEN and max test calls reached."
                )
            self._half_open_calls += 1

        try:
            result = await func(*args, **kwargs)
            self._on_success()
            return result
        except Exception as e:
            self._on_failure()
            raise

    def _on_success(self):
        if self._state in (CircuitState.HALF_OPEN, CircuitState.CLOSED):
            self._failure_count = 0
            if self._state == CircuitState.HALF_OPEN:
                logger.info("Circuit '%s' recovered, closing", self.name)
            self._state = CircuitState.CLOSED

    def _on_failure(self):
        self._failure_count += 1
        self._last_failure_time = time.time()
        if self._failure_count >= self.failure_threshold:
            self._state = CircuitState.OPEN
            logger.warning(
                "Circuit '%s' OPEN after %d failures",
                self.name, self._failure_count,
            )
        elif self._state == CircuitState.HALF_OPEN:
            self._state = CircuitState.OPEN
            logger.warning("Circuit '%s' back to OPEN after half-open failure", self.name)

    def reset(self):
        self._state = CircuitState.CLOSED
        self._failure_count = 0


# ---------------------------------------------------------------------------
# Rate Limiter (token bucket)
# ---------------------------------------------------------------------------

class RateLimiter:
    """Async token-bucket rate limiter."""

    def __init__(self, max_calls: int = 30, period: float = 60.0):
        self.max_calls = max_calls
        self.period = period
        self._tokens = float(max_calls)
        self._last_refill = time.time()
        self._lock = asyncio.Lock()

    async def acquire(self):
        async with self._lock:
            now = time.time()
            elapsed = now - self._last_refill
            self._tokens = min(
                self.max_calls,
                self._tokens + elapsed * (self.max_calls / self.period),
            )
            self._last_refill = now

            if self._tokens < 1.0:
                wait = (1.0 - self._tokens) * (self.period / self.max_calls)
                logger.debug("Rate limited, waiting %.2fs", wait)
                await asyncio.sleep(wait)
                self._tokens = 0.0
            else:
                self._tokens -= 1.0


# ---------------------------------------------------------------------------
# Timeout Manager
# ---------------------------------------------------------------------------

class TimeoutError(Exception):
    """Raised when an operation exceeds its timeout."""
    pass


class TimeoutManager:
    """Centralized timeout defaults by category."""

    DEFAULT_TIMEOUTS = {
        "llm_call": 120.0,
        "skill_execution": 30.0,
        "sandbox": 30.0,
        "network": 15.0,
        "embedding": 30.0,
    }

    def __init__(self, overrides: dict[str, float] | None = None):
        self.timeouts = dict(self.DEFAULT_TIMEOUTS)
        if overrides:
            self.timeouts.update(overrides)

    def get(self, category: str) -> float:
        return self.timeouts.get(category, 30.0)

    async def with_timeout(
        self,
        category: str,
        coro: Coroutine[Any, Any, Any],
    ) -> Any:
        timeout = self.get(category)
        try:
            return await asyncio.wait_for(coro, timeout=timeout)
        except asyncio.TimeoutError:
            raise TimeoutError(
                f"Operation '{category}' timed out after {timeout}s"
            ) from None
