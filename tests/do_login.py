"""Run Jarvis OAuth login to get a token with correct scopes (model.request)."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.codex_auth import login_interactive

async def main():
    print("Starting Jarvis OAuth login...")
    print("A browser window will open - log in with your ChatGPT account.")
    result = await login_interactive()
    if result:
        print(f"\nToken saved! Has scopes for model.request.")
        print(f"Access token length: {len(result.get('access_token', ''))}")
    else:
        print("\nLogin failed.")

asyncio.run(main())
