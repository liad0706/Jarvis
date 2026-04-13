from core.orchestrator import Orchestrator


class StreamingProvider:
    name = "streaming-provider"
    model = "stream-model"
    supports_streaming = True

    async def stream_chat(self, messages, tools=None):
        assert tools is None
        for token in ("Hello", " ", "world"):
            yield token

    async def chat(self, messages, tools=None):
        raise AssertionError("chat() should not be used when streaming is available")

    def format_tool_result(self, result, tool_call_id=None):
        return {"role": "tool", "content": str(result)}


async def test_orchestrator_streams_general_responses(memory, registry, event_bus):
    orchestrator = Orchestrator(registry, memory, event_bus=event_bus)

    provider = StreamingProvider()
    orchestrator._route_provider_for_task = lambda task_type: (provider, {"description": "general"})  # type: ignore[method-assign]
    orchestrator.model_router.classify_task = lambda user_input, has_images=False, tool_names=None: "general"  # type: ignore[method-assign]

    seen_tokens = []
    completed = []

    async def on_token(**kwargs):
        seen_tokens.append(kwargs["token"])

    async def on_complete(**kwargs):
        completed.append(kwargs["full_text"])

    event_bus.on("stream.token", on_token)
    event_bus.on("stream.complete", on_complete)

    result = await orchestrator.process("Tell me something interesting")

    assert result == "Hello world"
    assert seen_tokens == ["Hello", " ", "world"]
    assert completed == ["Hello world"]
    assert orchestrator.last_response_streamed is True
