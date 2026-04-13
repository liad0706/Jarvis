"""Async-friendly stdin reads.

On Windows, ``asyncio.to_thread(input, ...)`` often raises EOFError immediately
because the console is tied to the main thread. ``aioconsole.ainput`` avoids that.
"""

from __future__ import annotations

import asyncio
import sys

try:
    from aioconsole import ainput as _ainput
except ImportError:
    _ainput = None


async def async_input(prompt: str = "") -> str:
    """Read one line from stdin without breaking the asyncio event loop."""
    if _ainput is not None:
        return await _ainput(prompt)
    if sys.platform == "win32":
        return input(prompt)
    return await asyncio.to_thread(input, prompt)
