"""Smart retry with exponential backoff and fallback support."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)


class RetryExhausted(Exception):
    """All retry attempts (and fallback) failed."""

    def __init__(self, last_error: Exception, attempts: int):
        self.last_error = last_error
        self.attempts = attempts
        super().__init__(f"Failed after {attempts} attempts: {last_error}")


class RetryPolicy:
    def __init__(
        self,
        max_retries: int = 3,
        backoff_base: float = 2.0,
        max_backoff: float = 30.0,
        retryable_exceptions: tuple = (Exception,),
    ):
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.max_backoff = max_backoff
        self.retryable_exceptions = retryable_exceptions

    async def execute_with_retry(
        self,
        func: Callable[..., Coroutine[Any, Any, Any]],
        *args,
        fallback: Callable[..., Coroutine[Any, Any, Any]] | None = None,
        on_retry: Callable[[int, Exception], Coroutine[Any, Any, None]] | None = None,
        **kwargs,
    ) -> Any:
        """Execute func with retries. On exhaustion, try fallback if provided."""
        last_error: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            try:
                return await func(*args, **kwargs)
            except self.retryable_exceptions as e:
                last_error = e
                if attempt < self.max_retries:
                    delay = min(self.backoff_base ** attempt, self.max_backoff)
                    logger.warning(
                        "Attempt %d/%d failed: %s. Retrying in %.1fs",
                        attempt, self.max_retries, e, delay,
                    )
                    if on_retry:
                        await on_retry(attempt, e)
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        "Attempt %d/%d failed: %s. No more retries.",
                        attempt, self.max_retries, e,
                    )

        if fallback is not None:
            logger.info("Trying fallback after %d failed attempts", self.max_retries)
            try:
                return await fallback(*args, **kwargs)
            except Exception as fb_error:
                logger.error("Fallback also failed: %s", fb_error)
                raise RetryExhausted(fb_error, self.max_retries + 1) from fb_error

        raise RetryExhausted(last_error, self.max_retries)
