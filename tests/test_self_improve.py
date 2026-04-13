"""Test: ask Jarvis to create a skill by himself."""

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
    print(f"Skills before: {[s.name for s in ctx.orchestrator.registry.all_skills()]}")

    print("\n=== Asking Jarvis to turn on a light (he should build what he needs) ===")
    reply = await ctx.orchestrator.process(
        "תדליק לי את האור בחדר. "
        "אם אין לך כלי מתאים, תיצור אחד בעצמך עם self_improve. "
        "יש לי Home Assistant על localhost:8123."
    )
    print(f"\nReply: {reply[:500]}")
    print(f"\nSkills after: {[s.name for s in ctx.orchestrator.registry.all_skills()]}")

    await shutdown(ctx)

if __name__ == "__main__":
    asyncio.run(main())
