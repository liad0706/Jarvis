"""Run one user message through Jarvis (bootstrap + orchestrator.process). Usage:
  py scripts/jarvis_one_message.py "ההודעה שלך"
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from dotenv import load_dotenv

load_dotenv()


async def main() -> None:
    from config.settings import get_settings
    from core.bootstrap import bootstrap, shutdown

    msg = (
        sys.argv[1]
        if len(sys.argv) > 1
        else "כבה את הממיר של אפל (Apple TV) בחדר המשחקים"
    )
    get_settings.cache_clear()
    ctx = await bootstrap()
    try:
        print("You:", msg)
        reply = await ctx.orchestrator.process(msg)
        print("Jarvis:", reply)
    finally:
        await shutdown(ctx)


if __name__ == "__main__":
    asyncio.run(main())
