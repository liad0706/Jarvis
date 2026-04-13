"""Test Apple TV power_off: (1) skill direct (2) optional full LLM round via Jarvis."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Project root
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from dotenv import load_dotenv

load_dotenv()


async def direct_skill() -> dict:
    from skills.apple_tv import AppleTVSkill

    skill = AppleTVSkill()
    return await skill.execute("power_off", {})


async def via_llm() -> str:
    from config.settings import get_settings
    from core.bootstrap import bootstrap, shutdown

    get_settings.cache_clear()
    ctx = await bootstrap()
    try:
        return await ctx.orchestrator.process("כבה את הטלוויזיה בחדר המשחקים")
    finally:
        await shutdown(ctx)


async def main() -> None:
    print("=== 1) apple_tv power_off (skill ישיר) ===")
    r = await direct_skill()
    print(r)

    if "--no-llm" in sys.argv:
        return

    print("\n=== 2) orchestrator + Ollama (משפט בעברית) ===")
    try:
        text = await via_llm()
        print(text)
    except Exception as e:
        print(f"LLM path failed: {e}")


if __name__ == "__main__":
    asyncio.run(main())
