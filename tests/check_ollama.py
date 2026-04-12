import httpx, json

try:
    r = httpx.get("http://localhost:11434/api/tags", timeout=5)
    models = r.json().get("models", [])
    print(f"Ollama running - {len(models)} models:")
    for m in models:
        size_mb = m.get("size", 0) // 1024 // 1024
        print(f"  {m['name']:35s} {size_mb}MB")

    # Quick test: chat with qwen3:8b
    print("\nTesting qwen3:8b chat...")
    r2 = httpx.post("http://localhost:11434/api/chat", json={
        "model": "qwen3:8b",
        "messages": [{"role": "user", "content": "Say hello in Hebrew. One word."}],
        "stream": False,
    }, timeout=30)
    print(f"Response: {r2.json()['message']['content'][:100]}")

    # Test tool calling
    print("\nTesting tool calling...")
    r3 = httpx.post("http://localhost:11434/api/chat", json={
        "model": "qwen3:8b",
        "messages": [{"role": "user", "content": "Turn on the bedroom light"}],
        "tools": [{
            "type": "function",
            "function": {
                "name": "turn_on_light",
                "description": "Turn on light in a room",
                "parameters": {
                    "type": "object",
                    "properties": {"room": {"type": "string"}},
                    "required": ["room"],
                },
            },
        }],
        "stream": False,
    }, timeout=30)
    msg = r3.json()["message"]
    if msg.get("tool_calls"):
        for tc in msg["tool_calls"]:
            print(f"Tool call: {tc['function']['name']}({json.dumps(tc['function']['arguments'])})")
        print("Tool calling works!")
    else:
        print(f"No tool calls. Content: {msg.get('content', '')[:200]}")

except Exception as e:
    print(f"Error: {e}")
