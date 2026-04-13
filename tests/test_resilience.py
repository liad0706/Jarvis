"""Tests for circuit breaker, rate limiter, and timeout manager."""

import asyncio

import pytest

from core.resilience import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
    RateLimiter,
    TimeoutError,
    TimeoutManager,
)


@pytest.mark.asyncio
class TestCircuitBreaker:
    async def test_starts_closed(self):
        cb = CircuitBreaker(name="test")
        assert cb.state == CircuitState.CLOSED

    async def test_opens_after_threshold(self):
        cb = CircuitBreaker(name="test", failure_threshold=3)

        async def failing():
            raise RuntimeError("fail")

        for _ in range(3):
            with pytest.raises(RuntimeError):
                await cb.call(failing)

        assert cb.state == CircuitState.OPEN

    async def test_open_rejects_calls(self):
        cb = CircuitBreaker(name="test", failure_threshold=1)

        async def failing():
            raise RuntimeError("fail")

        with pytest.raises(RuntimeError):
            await cb.call(failing)

        with pytest.raises(CircuitOpenError):
            await cb.call(failing)

    async def test_recovers_after_success(self):
        cb = CircuitBreaker(name="test", failure_threshold=2, reset_timeout=0.1)

        async def failing():
            raise RuntimeError("fail")

        async def succeeding():
            return "ok"

        with pytest.raises(RuntimeError):
            await cb.call(failing)
        with pytest.raises(RuntimeError):
            await cb.call(failing)

        assert cb.state == CircuitState.OPEN

        await asyncio.sleep(0.15)
        assert cb.state == CircuitState.HALF_OPEN

        result = await cb.call(succeeding)
        assert result == "ok"
        assert cb.state == CircuitState.CLOSED

    async def test_reset(self):
        cb = CircuitBreaker(name="test", failure_threshold=1)

        async def failing():
            raise RuntimeError()

        with pytest.raises(RuntimeError):
            await cb.call(failing)

        cb.reset()
        assert cb.state == CircuitState.CLOSED


@pytest.mark.asyncio
class TestRateLimiter:
    async def test_allows_within_limit(self):
        rl = RateLimiter(max_calls=10, period=1.0)
        for _ in range(5):
            await rl.acquire()

    async def test_slows_when_exhausted(self):
        rl = RateLimiter(max_calls=2, period=1.0)
        await rl.acquire()
        await rl.acquire()
        # Third should wait but not raise
        await rl.acquire()


@pytest.mark.asyncio
class TestTimeoutManager:
    async def test_succeeds_within_timeout(self):
        tm = TimeoutManager(overrides={"fast": 2.0})

        async def quick():
            return 42

        result = await tm.with_timeout("fast", quick())
        assert result == 42

    async def test_raises_on_timeout(self):
        tm = TimeoutManager(overrides={"fast": 0.05})

        async def slow():
            await asyncio.sleep(1)

        with pytest.raises(TimeoutError):
            await tm.with_timeout("fast", slow())

    async def test_default_timeout(self):
        tm = TimeoutManager()
        assert tm.get("llm_call") == 120.0
        assert tm.get("unknown") == 30.0
