"""Tests for retry logic."""

import pytest

from core.retry import RetryExhausted, RetryPolicy


@pytest.mark.asyncio
class TestRetryPolicy:
    async def test_succeeds_first_try(self):
        policy = RetryPolicy(max_retries=3, backoff_base=0.01)

        async def ok():
            return "success"

        result = await policy.execute_with_retry(ok)
        assert result == "success"

    async def test_retries_then_succeeds(self):
        policy = RetryPolicy(max_retries=3, backoff_base=0.01)
        attempts = 0

        async def flaky():
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise RuntimeError("not yet")
            return "ok"

        result = await policy.execute_with_retry(flaky)
        assert result == "ok"
        assert attempts == 3

    async def test_exhausted_raises(self):
        policy = RetryPolicy(max_retries=2, backoff_base=0.01)

        async def always_fail():
            raise RuntimeError("fail")

        with pytest.raises(RetryExhausted) as exc_info:
            await policy.execute_with_retry(always_fail)

        assert exc_info.value.attempts == 2

    async def test_fallback_used_on_exhaustion(self):
        policy = RetryPolicy(max_retries=1, backoff_base=0.01)

        async def failing():
            raise RuntimeError("fail")

        async def backup():
            return "fallback_result"

        result = await policy.execute_with_retry(failing, fallback=backup)
        assert result == "fallback_result"

    async def test_fallback_also_fails(self):
        policy = RetryPolicy(max_retries=1, backoff_base=0.01)

        async def failing():
            raise RuntimeError("main fail")

        async def bad_fallback():
            raise RuntimeError("fallback fail")

        with pytest.raises(RetryExhausted):
            await policy.execute_with_retry(failing, fallback=bad_fallback)

    async def test_on_retry_callback(self):
        policy = RetryPolicy(max_retries=3, backoff_base=0.01)
        retry_log = []
        attempt_count = 0

        async def flaky():
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count < 3:
                raise RuntimeError("not yet")
            return "done"

        async def log_retry(attempt, error):
            retry_log.append((attempt, str(error)))

        await policy.execute_with_retry(flaky, on_retry=log_retry)
        assert len(retry_log) == 2
