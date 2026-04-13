"""End-to-end test: send a message through the orchestrator and verify tool calls work."""

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
    print(f"Skills: {[s.name for s in ctx.orchestrator.registry.all_skills()]}")

    print("\n=== Test 1: Simple greeting ===")
    reply = await ctx.orchestrator.process("היי, מה המצב?")
    print(f"Reply: {reply[:200]}")

    print("\n=== Test 2: Tool call (list files) ===")
    ctx.orchestrator.conversation = []
    reply = await ctx.orchestrator.process("תראה לי את הקבצים על שולחן העבודה")
    print(f"Reply: {reply[:300]}")

    print("\n=== Test 3: Search files ===")
    ctx.orchestrator.conversation = []
    reply = await ctx.orchestrator.process("תחפש קובץ שקשור לאור או light בתיקיית הפרויקט")
    print(f"Reply: {reply[:300]}")

    await shutdown(ctx)
    print("\nDone!")


if __name__ == "__main__":
    asyncio.run(main())
