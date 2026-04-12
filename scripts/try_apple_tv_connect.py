"""Try connect to Apple TV (uses credentials in data/apple_tv.conf if present)."""
from __future__ import annotations

import asyncio
import os
import sys

from dotenv import load_dotenv

load_dotenv()


async def main() -> None:
    from pathlib import Path

    from pyatv import connect, scan
    from pyatv.storage.file_storage import FileStorage

    host = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("JARVIS_APPLE_TV_HOST", "")).strip()
    if not host:
        print("need host")
        sys.exit(1)

    root = Path(__file__).resolve().parents[1]
    cred = root / "data" / "apple_tv.conf"
    loop = asyncio.get_running_loop()
    storage = FileStorage(cred.as_posix(), loop)
    await storage.load()

    atvs = await scan(loop, hosts=[host], storage=storage, timeout=15)
    if not atvs:
        print("no device")
        sys.exit(2)
    conf = atvs[0]
    print("connecting to", conf.name, "...")
    atv = await connect(conf, loop, storage=storage)
    try:
        print("connected. metadata:", await atv.metadata.playing())
    finally:
        pending = atv.close()
        if pending:
            await asyncio.gather(*pending)
        await storage.save()


if __name__ == "__main__":
    asyncio.run(main())
