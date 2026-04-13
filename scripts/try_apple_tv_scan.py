"""One-off: discover Apple TV at JARVIS_APPLE_TV_HOST (or argv[1])."""
from __future__ import annotations

import asyncio
import os
import sys

from dotenv import load_dotenv

load_dotenv()


async def main() -> None:
    from pyatv import scan

    host = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("JARVIS_APPLE_TV_HOST", "")).strip()
    if not host:
        print("Set JARVIS_APPLE_TV_HOST or pass IP as argv")
        sys.exit(1)

    loop = asyncio.get_running_loop()
    print(f"scanning {host!r} ...")
    atvs = await scan(loop, hosts=[host], timeout=15)
    print(f"found {len(atvs)} device(s)")
    for c in atvs:
        print(" ---")
        print(c)
        for s in c.services:
            print(f"  service: {s}")


if __name__ == "__main__":
    asyncio.run(main())
