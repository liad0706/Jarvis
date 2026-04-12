"""Print apple_tv pairing_status (loads .env from project root)."""
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
    from skills.apple_tv import AppleTVSkill

    out = await AppleTVSkill().execute("pairing_status", {})
    for k, v in out.items():
        print(f"{k}: {v}")


if __name__ == "__main__":
    asyncio.run(main())
