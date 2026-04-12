"""Test smart home skill through the orchestrator."""

import asyncio
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


async def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    from core.bootstrap import bootstrap, shutdown

    print("Bootstrapping Jarvis...")
    ctx = await bootstrap()
    print(f"Provider: {ctx.orchestrator.provider.name}")

    skills = [s.name for s in ctx.orchestrator.registry.all_skills()]
    print(f"Skills: {skills}")
    assert "smart_home" in skills, "smart_home skill not registered!"

    print("\n=== Test: Ask to turn on the light ===")
    reply = await ctx.orchestrator.process("תדליק לי את האור בחדר")
    print(f"Reply: {reply[:500]}")

    await shutdown(ctx)
    print("\nDone!")


if __name__ == "__main__":
    asyncio.run(main())
