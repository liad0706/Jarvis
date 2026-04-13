"""Quick test: verify Codex OAuth token works with OpenAI API."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


async def test_token():
    from core.codex_auth import get_valid_token, _load_token, _load_codex_cli_token

    print("=== Token Sources ===")

    jarvis_token = Path("data/codex_token.json")
    print(f"Jarvis token file exists: {jarvis_token.exists()}")

    cli_token = _load_codex_cli_token()
    print(f"Codex CLI token found: {cli_token is not None}")

    token_data = _load_token()
    if not token_data:
        print("ERROR: No token found anywhere!")
        return False

    access_token = token_data.get("access_token", "")
    print(f"Access token length: {len(access_token)}")
    print(f"Has refresh token: {bool(token_data.get('refresh_token'))}")

    print("\n=== Getting Valid Token ===")
    token = await get_valid_token()
    if not token:
        print("ERROR: get_valid_token() returned None")
        return False
    print(f"Got valid token (length={len(token)})")

    print("\n=== Testing OpenAI API Call ===")
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=token)
        response = await client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": "Say 'hello' in Hebrew. One word only."}],
            max_tokens=10,
        )
        reply = response.choices[0].message.content
        print(f"API response: {reply}")
        print("SUCCESS - Token works!")
        return True
    except Exception as e:
        print(f"API call failed: {e}")
        return False


async def test_tool_calling():
    print("\n=== Testing Tool Calling ===")
    from core.codex_auth import get_valid_token
    token = await get_valid_token()
    if not token:
        print("No token, skipping")
        return False

    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=token)

    tools = [{
        "type": "function",
        "function": {
            "name": "turn_on_light",
            "description": "Turn on a light in a room",
            "parameters": {
                "type": "object",
                "properties": {
                    "room": {"type": "string", "description": "Room name"},
                },
                "required": ["room"],
            },
        },
    }]

    try:
        response = await client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": "Turn on the light in the bedroom"}],
            tools=tools,
        )
        msg = response.choices[0].message
        if msg.tool_calls:
            for tc in msg.tool_calls:
                print(f"Tool call: {tc.function.name}({tc.function.arguments})")
            print("SUCCESS - Tool calling works!")
            return True
        else:
            print(f"No tool calls. Response: {msg.content}")
            return False
    except Exception as e:
        print(f"Tool calling failed: {e}")
        return False


if __name__ == "__main__":
    ok1 = asyncio.run(test_token())
    if ok1:
        ok2 = asyncio.run(test_tool_calling())
    print(f"\n{'All tests passed!' if ok1 and ok2 else 'Some tests failed.'}")
