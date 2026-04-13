"""Multi-provider LLM abstraction — supports Ollama, OpenAI (Codex/GPT), and Anthropic (Claude).

Usage:
    provider = get_provider(settings)
    response_text, tool_calls = await provider.chat(messages, tools)
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, AsyncIterator

from config.settings import ollama_runtime_options

logger = logging.getLogger(__name__)


@dataclass
class ToolCall:
    """Normalised tool call across providers."""
    name: str
    arguments: dict
    id: str | None = None


@dataclass
class LLMResponse:
    """Normalised LLM response."""
    content: str
    tool_calls: list[ToolCall]
    raw: Any = None  # original provider response


class BaseLLMProvider(ABC):
    """Abstract LLM provider — all providers implement this interface."""

    name: str = "base"

    @abstractmethod
    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> LLMResponse:
        ...

    def format_tool_result(self, result: dict, tool_call_id: str | None = None) -> dict:
        """Format a tool result message for this provider."""
        return {"role": "tool", "content": json.dumps(result, ensure_ascii=False)}

    def bind_event_bus(self, event_bus: Any | None) -> "BaseLLMProvider":
        """Allow providers to emit progress/status events while they work."""
        return self


def _ollama_stream_message_delta(chunk: Any) -> str:
    """Visible text fragment from one Ollama /api/chat stream chunk (dict or ChatResponse).

    Thinking-only models often stream to ``message.thinking`` while ``content`` stays empty;
    without reading ``thinking`` the UI receives no tokens and shows a blank reply.
    """
    msg: Any = None
    if isinstance(chunk, dict):
        msg = chunk.get("message")
    elif hasattr(chunk, "get"):
        msg = chunk.get("message")
    else:
        msg = getattr(chunk, "message", None)
    if msg is None:
        return ""
    if isinstance(msg, dict):
        content = msg.get("content") or ""
        thinking = msg.get("thinking") or ""
    elif hasattr(msg, "get"):
        content = msg.get("content") or ""
        thinking = msg.get("thinking") or ""
    else:
        content = getattr(msg, "content", None) or ""
        thinking = getattr(msg, "thinking", None) or ""
    return content or thinking


# ─────────────────────── Ollama ───────────────────────


class OllamaProvider(BaseLLMProvider):
    name = "ollama"

    def __init__(
        self,
        host: str,
        model: str,
        extra_options: dict[str, Any] | None = None,
        keep_alive: str | None = None,
    ):
        self.host = host
        self.model = model
        self._extra_options = dict(extra_options or {})
        self._keep_alive = (keep_alive or "").strip() or None
        self._client = None

    def _ollama_options(self) -> dict[str, Any] | None:
        opts = dict(self._extra_options)
        if "qwen" in self.model.lower():
            opts["temperature"] = 0.35
        return opts if opts else None

    def _ollama_chat_kwargs(self, base: dict[str, Any]) -> dict[str, Any]:
        o = self._ollama_options()
        if o:
            base["options"] = o
        if self._keep_alive:
            base["keep_alive"] = self._keep_alive
        return base

    @property
    def client(self):
        if self._client is None:
            import ollama
            self._client = ollama.AsyncClient(host=self.host)
        return self._client

    async def chat(self, messages: list[dict], tools: list[dict] | None = None) -> LLMResponse:
        import ollama
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "tools": tools if tools else None,
        }
        kwargs = self._ollama_chat_kwargs(kwargs)
        try:
            response = await self.client.chat(**kwargs)
        except Exception as e:
            err = str(e).lower()
            if "not found" in err or "404" in str(e):
                raise RuntimeError(
                    f"Ollama model '{self.model}' not found at {self.host} (404). "
                    f"Same machine often has two Ollamas (Windows tray vs WSL): `ollama list` in PowerShell "
                    f"may differ from what Jarvis sees. Try: ollama pull {self.model} in PowerShell, "
                    f"or set JARVIS_OLLAMA_HOST in .env to the server that actually has the model. "
                    f"Text fallback: JARVIS_OLLAMA_MODEL=qwen2.5:7b"
                ) from e
            raise
        msg = response.message
        tool_calls = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                args = tc.function.arguments or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                tool_calls.append(ToolCall(
                    name=tc.function.name,
                    arguments=args,
                    id=getattr(tc, "id", None),
                ))
        return LLMResponse(content=msg.content or "", tool_calls=tool_calls, raw=response)

    @property
    def supports_streaming(self) -> bool:
        return True

    async def stream_chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> AsyncIterator[str]:
        if tools:
            raise NotImplementedError("Cannot stream when tool definitions are attached")

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
        }
        kwargs = self._ollama_chat_kwargs(kwargs)

        stream = await self.client.chat(**kwargs)
        async for chunk in stream:
            piece = _ollama_stream_message_delta(chunk)
            if piece:
                yield piece


# ─────────────────────── OpenAI (GPT / Codex) ───────────────────────


class OpenAIProvider(BaseLLMProvider):
    name = "openai"

    def __init__(self, api_key: str, model: str = "gpt-4o-mini", base_url: str | None = None):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from openai import AsyncOpenAI
            kwargs = {"api_key": self.api_key}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = AsyncOpenAI(**kwargs)
        return self._client

    def _convert_tools(self, tools: list[dict] | None) -> list[dict] | None:
        """Convert Ollama tool format to OpenAI tool format."""
        if not tools:
            return None
        openai_tools = []
        for t in tools:
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": t["function"]["name"],
                    "description": t["function"].get("description", ""),
                    "parameters": t["function"].get("parameters", {"type": "object", "properties": {}}),
                },
            })
        return openai_tools

    async def chat(self, messages: list[dict], tools: list[dict] | None = None) -> LLMResponse:
        openai_tools = self._convert_tools(tools)

        # Clean messages: OpenAI doesn't support 'tool_calls' in message dicts the same way
        clean_msgs = []
        for m in messages:
            # Preserve multimodal user content (e.g. text + image_url after camera snapshot)
            if m["role"] == "user" and isinstance(m.get("content"), list):
                msg = {"role": "user", "content": m["content"]}
            else:
                msg = {"role": m["role"], "content": m.get("content", "")}
            if m.get("tool_calls") and m["role"] == "assistant":
                # Convert tool_calls to OpenAI format
                msg["tool_calls"] = []
                for i, tc in enumerate(m["tool_calls"]):
                    if hasattr(tc, "function"):
                        # Ollama ToolCall object
                        msg["tool_calls"].append({
                            "id": f"call_{i}",
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": json.dumps(tc.function.arguments or {}),
                            },
                        })
                    elif isinstance(tc, dict):
                        msg["tool_calls"].append(tc)
            if m["role"] == "tool":
                msg["tool_call_id"] = m.get("tool_call_id", "call_0")
            clean_msgs.append(msg)

        kwargs = {
            "model": self.model,
            "messages": clean_msgs,
        }
        if openai_tools:
            kwargs["tools"] = openai_tools

        response = await self.client.chat.completions.create(**kwargs)
        choice = response.choices[0]
        msg = choice.message

        tool_calls = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                args = tc.function.arguments
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                tool_calls.append(
                    ToolCall(name=tc.function.name, arguments=args, id=getattr(tc, "id", None))
                )

        return LLMResponse(content=msg.content or "", tool_calls=tool_calls, raw=response)

    def format_tool_result(self, result: dict, tool_call_id: str | None = None) -> dict:
        return {
            "role": "tool",
            "tool_call_id": tool_call_id or "call_0",
            "content": json.dumps(result, ensure_ascii=False),
        }

    @property
    def supports_streaming(self) -> bool:
        return True

    async def stream_chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> AsyncIterator[str]:
        if tools:
            raise NotImplementedError("Cannot stream when tool definitions are attached")

        clean_msgs = []
        for m in messages:
            if m["role"] == "user" and isinstance(m.get("content"), list):
                msg = {"role": "user", "content": m["content"]}
            else:
                msg = {"role": m["role"], "content": m.get("content", "")}
            if m["role"] == "tool":
                msg["tool_call_id"] = m.get("tool_call_id", "call_0")
            clean_msgs.append(msg)

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=clean_msgs,
            stream=True,
        )

        async for chunk in response:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                yield delta.content


# ─────────────────────── Anthropic (Claude) ───────────────────────


class AnthropicProvider(BaseLLMProvider):
    name = "anthropic"

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-20250514"):
        self.api_key = api_key
        self.model = model
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from anthropic import AsyncAnthropic
            self._client = AsyncAnthropic(api_key=self.api_key)
        return self._client

    def _convert_tools(self, tools: list[dict] | None) -> list[dict] | None:
        if not tools:
            return None
        claude_tools = []
        for t in tools:
            claude_tools.append({
                "name": t["function"]["name"],
                "description": t["function"].get("description", ""),
                "input_schema": t["function"].get("parameters", {"type": "object", "properties": {}}),
            })
        return claude_tools

    def _convert_messages(self, messages: list[dict]) -> tuple[str, list[dict]]:
        """Extract system prompt and convert messages to Anthropic format."""
        system = ""
        claude_msgs = []
        for m in messages:
            if m["role"] == "system":
                system = m.get("content", "")
            elif m["role"] == "user":
                claude_msgs.append({"role": "user", "content": m.get("content", "")})
            elif m["role"] == "assistant":
                content = []
                if m.get("content"):
                    content.append({"type": "text", "text": m["content"]})
                if m.get("tool_calls"):
                    for tc in m["tool_calls"]:
                        if hasattr(tc, "function"):
                            content.append({
                                "type": "tool_use",
                                "id": f"toolu_{tc.function.name}",
                                "name": tc.function.name,
                                "input": tc.function.arguments or {},
                            })
                claude_msgs.append({"role": "assistant", "content": content or m.get("content", "")})
            elif m["role"] == "tool":
                tool_content = m.get("content", "")
                claude_msgs.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": m.get("tool_use_id", "toolu_unknown"),
                        "content": tool_content,
                    }],
                })
        return system, claude_msgs

    async def chat(self, messages: list[dict], tools: list[dict] | None = None) -> LLMResponse:
        system, claude_msgs = self._convert_messages(messages)
        claude_tools = self._convert_tools(tools)

        kwargs = {
            "model": self.model,
            "max_tokens": 4096,
            "messages": claude_msgs,
        }
        if system:
            kwargs["system"] = system
        if claude_tools:
            kwargs["tools"] = claude_tools

        response = await self.client.messages.create(**kwargs)

        text = ""
        tool_calls = []
        for block in response.content:
            if block.type == "text":
                text += block.text
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(name=block.name, arguments=block.input or {}, id=getattr(block, "id", None))
                )

        return LLMResponse(content=text, tool_calls=tool_calls, raw=response)


# ─────────────────────── Codex CLI (subprocess) ───────────────────────


class CodexCLIProvider(BaseLLMProvider):
    """Use the official codex-cli as a subprocess — works with ChatGPT subscription OAuth."""

    name = "codex-cli"

    def __init__(self, model: str = "gpt-5.4"):
        self.model = model
        self._event_bus = None

    def _messages_to_prompt(self, messages: list[dict]) -> str:
        """Convert chat messages into a single text prompt for codex exec."""
        parts = []
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if role == "system":
                parts.append(f"[System Instructions]\n{content}\n")
            elif role == "user":
                parts.append(f"[User]\n{content}\n")
            elif role == "assistant":
                parts.append(f"[Assistant]\n{content}\n")
            elif role == "tool":
                parts.append(f"[Tool Result]\n{content}\n")
        return "\n".join(parts)

    def _build_tool_instructions(self, tools: list[dict] | None) -> str:
        if not tools:
            return ""
        lines = [
            "\n[Available Tools — respond with ONLY a JSON block to call a tool]",
            "When you need to use a tool, respond with EXACTLY this JSON format and nothing else:",
            '{"tool_calls": [{"name": "tool_name", "arguments": {"arg": "value"}}]}',
            "\nAvailable tools:",
        ]
        for t in tools:
            fn = t.get("function", t)
            name = fn.get("name", "?")
            desc = fn.get("description", "")
            params = fn.get("parameters", {})
            props = params.get("properties", {})
            param_list = ", ".join(f'{k}: {v.get("type", "any")}' for k, v in props.items())
            lines.append(f"  - {name}({param_list}): {desc}")
        lines.append("\nIf no tool is needed, respond with plain text.")
        return "\n".join(lines)

    def bind_event_bus(self, event_bus: Any | None) -> "CodexCLIProvider":
        self._event_bus = event_bus
        return self

    async def _emit_progress(self, summary: str) -> None:
        text = (summary or "").strip()
        if not text or self._event_bus is None:
            return
        try:
            await self._event_bus.emit("task.progress", summary=text)
        except Exception:
            logger.debug("Failed to emit Codex progress", exc_info=True)

    async def _spawn_process(self):
        import asyncio
        import shutil
        import subprocess
        import sys

        codex_path = shutil.which("codex") or "codex"
        cmd = [
            codex_path,
            "exec",
            "--skip-git-repo-check",
            "--json",
            "--ephemeral",
            "-m",
            self.model,
            "-",
        ]
        kwargs = {
            "stdin": asyncio.subprocess.PIPE,
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
            "limit": 4 * 1024 * 1024,  # 4 MB — Codex JSON events can exceed the 64 KB default
        }
        if sys.platform == "win32":
            return await asyncio.create_subprocess_shell(
                subprocess.list2cmdline(cmd),
                **kwargs,
            )
        return await asyncio.create_subprocess_exec(*cmd, **kwargs)

    async def _handle_codex_event(self, event: dict[str, Any], pending_message: str) -> str:
        if event.get("type") != "item.completed":
            return pending_message
        item = event.get("item", {}) or {}
        if item.get("type") != "agent_message":
            return pending_message
        text = (item.get("text") or "").strip()
        if not text:
            return pending_message
        if pending_message:
            await self._emit_progress(pending_message)
        return text

    # Codex CLI's internal text splitter fails with "Separator is not found,
    # and chunk exceed the limit" when the stdin prompt is too large.
    # Cap at ~100k chars (~25k tokens) to stay safely within its limits.
    _MAX_PROMPT_CHARS = 100_000

    def _truncate_prompt(self, prompt: str) -> str:
        """Truncate prompt to fit Codex CLI's internal chunk limit."""
        if len(prompt) <= self._MAX_PROMPT_CHARS:
            return prompt
        logger.warning(
            "Codex CLI prompt too large (%d chars), truncating to %d",
            len(prompt), self._MAX_PROMPT_CHARS,
        )
        # Keep the beginning (system instructions) and end (recent messages + tools).
        # Drop the middle (older conversation history).
        head_size = self._MAX_PROMPT_CHARS // 4
        tail_size = self._MAX_PROMPT_CHARS - head_size
        return (
            prompt[:head_size]
            + "\n\n[... conversation trimmed for length ...]\n\n"
            + prompt[-tail_size:]
        )

    async def chat(self, messages: list[dict], tools: list[dict] | None = None) -> LLMResponse:
        import asyncio

        tool_instr = self._build_tool_instructions(tools)
        prompt = self._messages_to_prompt(messages)
        if tool_instr:
            prompt += "\n" + tool_instr

        prompt = self._truncate_prompt(prompt)

        proc = await self._spawn_process()
        assert proc.stdin is not None
        assert proc.stdout is not None
        assert proc.stderr is not None

        proc.stdin.write(prompt.encode("utf-8", errors="replace"))
        await proc.stdin.drain()
        proc.stdin.close()

        raw_lines: list[str] = []
        pending_message = ""

        while True:
            line_bytes = await proc.stdout.readline()
            if not line_bytes:
                break
            line = line_bytes.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            raw_lines.append(line)
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            pending_message = await self._handle_codex_event(event, pending_message)

        stderr_text = (await proc.stderr.read()).decode("utf-8", errors="replace").strip()
        return_code = await proc.wait()

        full_text = pending_message.strip()
        if not full_text and stderr_text:
            logger.warning("Codex CLI stderr: %s", stderr_text[:300])
            full_text = f"׳©׳’׳™׳׳” ׳‘-Codex CLI: {stderr_text[:200]}"
        elif return_code != 0 and not full_text:
            full_text = f"׳©׳’׳™׳׳” ׳‘-Codex CLI (exit code {return_code})"

        # Check if the model wants to call tools (embedded JSON)
        tool_calls = self._extract_tool_calls(full_text)
        if tool_calls:
            full_text = ""

        raw_output = "\n".join(raw_lines)
        if stderr_text:
            raw_output = f"{raw_output}\n[stderr]\n{stderr_text}".strip()

        return LLMResponse(content=full_text, tool_calls=tool_calls, raw=raw_output)

    def _extract_tool_calls(self, text: str) -> list[ToolCall]:
        """Try to extract JSON tool calls from the response text."""
        import re
        # Look for JSON block with tool_calls
        patterns = [
            r'\{[^{}]*"tool_calls"\s*:\s*\[.*?\]\s*\}',
            r'```json\s*(\{.*?"tool_calls".*?\})\s*```',
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group(0) if "```" not in pattern else match.group(1))
                    calls = []
                    for tc in data.get("tool_calls", []):
                        calls.append(ToolCall(
                            name=tc.get("name", ""),
                            arguments=tc.get("arguments", {}),
                        ))
                    if calls:
                        return calls
                except (json.JSONDecodeError, KeyError):
                    continue
        return []


# ─────────────────────── Factory ───────────────────────

_CLAUDE_CLI_AUTH_HINT = (
    " Hint: Run `claude` in a terminal and sign in again, or set JARVIS_ANTHROPIC_API_KEY "
    "(https://console.anthropic.com/) and JARVIS_ANTHROPIC_MODEL to a full API model id "
    "(e.g. claude-sonnet-4-20250514)."
)


def _claude_cli_needs_auth_hint(*parts: str) -> bool:
    blob = "\n".join(p for p in parts if p)
    if not blob:
        return False
    if "authentication_error" in blob:
        return True
    if "Invalid authentication credentials" in blob:
        return True
    if "Failed to authenticate" in blob and "401" in blob:
        return True
    return False


class ClaudeCLIProvider(BaseLLMProvider):
    """Use Claude Code CLI as a subprocess — works with existing Claude Max subscription.

    No API key needed — uses the same auth as ``claude`` CLI.
    Similar pattern to CodexCLIProvider but for Anthropic Claude.
    """

    name = "claude-cli"

    def __init__(self, model: str = "claude-sonnet-4-20250514"):
        self.model = model

    def _build_prompt(self, messages: list[dict], tools: list[dict] | None) -> str:
        """Build full prompt: system → tools → conversation."""
        parts = []

        # 1. System instructions first
        for m in messages:
            if m.get("role") == "system":
                parts.append(m.get("content", ""))

        # 2. Tool definitions (compact, part of instructions)
        if tools:
            tool_lines = [
                "You have tools. To use one, respond ONLY with this JSON (no other text):",
                '{"tool_calls": [{"name": "TOOL_NAME", "arguments": {"key": "value"}}]}',
                "Tools:",
            ]
            for t in tools:
                fn = t.get("function", t)
                name = fn.get("name", "?")
                desc = fn.get("description", "")
                params = fn.get("parameters", {})
                props = params.get("properties", {})
                param_list = ", ".join(f'{k}:{v.get("type", "?")}' for k, v in props.items())
                tool_lines.append(f"  {name}({param_list}) — {desc}")
            tool_lines.append("If no tool needed, respond in Hebrew.")
            parts.append("\n".join(tool_lines))

        # 3. Conversation history
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if role == "system":
                continue  # already added
            elif role == "user":
                parts.append(f"User: {content}")
            elif role == "assistant":
                parts.append(f"Assistant: {content}")
            elif role == "tool":
                parts.append(f"Tool Result: {content}")

        return "\n\n".join(parts)

    async def chat(self, messages: list[dict], tools: list[dict] | None = None) -> LLMResponse:
        import asyncio
        import subprocess

        prompt = self._build_prompt(messages, tools)

        import shutil
        claude_path = shutil.which("claude") or "claude"
        cmd = [
            claude_path, "-p",
            "--output-format", "json",
            "--model", self.model,
            "--no-session-persistence",
        ]

        def _run():
            return subprocess.run(
                cmd,
                input=prompt,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=90,
                shell=False,
            )

        result = await asyncio.to_thread(_run)

        output = result.stdout.strip()
        full_text = ""

        # Parse JSON response from claude CLI
        try:
            data = json.loads(output)
            full_text = data.get("result", "")
        except json.JSONDecodeError:
            # Fallback: treat raw output as text
            full_text = output

        if not full_text and result.stderr:
            logger.warning("Claude CLI stderr: %s", result.stderr[:300])
            full_text = f"שגיאה ב-Claude CLI: {result.stderr[:200]}"

        if full_text and _claude_cli_needs_auth_hint(output, result.stderr or "", full_text):
            full_text = full_text.rstrip() + _CLAUDE_CLI_AUTH_HINT

        # Check if the model wants to call tools (embedded JSON)
        tool_calls = self._extract_tool_calls(full_text)
        if tool_calls:
            full_text = ""

        return LLMResponse(content=full_text, tool_calls=tool_calls, raw=output)

    def _extract_tool_calls(self, text: str) -> list[ToolCall]:
        """Try to extract JSON tool calls from the response text."""
        import re
        patterns = [
            r'\{[^{}]*"tool_calls"\s*:\s*\[.*?\]\s*\}',
            r'```json\s*(\{.*?"tool_calls".*?\})\s*```',
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group(0) if "```" not in pattern else match.group(1))
                    calls = []
                    for tc in data.get("tool_calls", []):
                        calls.append(ToolCall(
                            name=tc.get("name", ""),
                            arguments=tc.get("arguments", {}),
                        ))
                    if calls:
                        return calls
                except (json.JSONDecodeError, KeyError):
                    continue
        return []


class CodexOAuthProvider(OpenAIProvider):
    """OpenAI Codex via ChatGPT subscription OAuth — no API key needed."""

    name = "codex-oauth"

    def __init__(self, model: str = "gpt-4o-mini"):
        # Dummy key — will be replaced with OAuth token dynamically
        super().__init__(api_key="oauth-pending", model=model)
        self._token_cache: str | None = None

    @property
    def client(self):
        """Always create client with fresh token."""
        # Lazy import to avoid circular deps
        import asyncio
        from core.codex_auth import get_valid_token

        # Get token synchronously if we're in an event loop
        try:
            loop = asyncio.get_running_loop()
            # We're in async context — token will be fetched in chat()
            pass
        except RuntimeError:
            pass

        if self._client is None:
            from openai import AsyncOpenAI
            self._client = AsyncOpenAI(api_key="pending")
        return self._client

    async def chat(self, messages: list[dict], tools: list[dict] | None = None) -> LLMResponse:
        from core.codex_auth import get_valid_token, refresh_token, _load_token

        token = await get_valid_token()
        if not token:
            raise RuntimeError(
                "Codex OAuth token expired or missing. "
                "Run Jarvis and use 'login codex' to reconnect."
            )

        from openai import AsyncOpenAI
        self._client = AsyncOpenAI(api_key=token)
        try:
            return await super().chat(messages, tools)
        except Exception as e:
            if "401" in str(e) or "token" in str(e).lower():
                logger.warning("Token rejected, attempting refresh...")
                token_data = _load_token()
                refresh_tok = token_data.get("refresh_token", "") if token_data else ""
                if refresh_tok:
                    new_data = await refresh_token(refresh_tok)
                    if new_data:
                        self._client = AsyncOpenAI(api_key=new_data["access_token"])
                        return await super().chat(messages, tools)
            raise


def _settings_ollama_extra_options(settings) -> dict[str, Any]:
    return ollama_runtime_options(settings)


def make_ollama_provider(settings) -> OllamaProvider:
    """Build OllamaProvider from Settings (host, model, speed-related options)."""
    ex = _settings_ollama_extra_options(settings)
    ka = (getattr(settings, "ollama_keep_alive", "") or "").strip()
    return OllamaProvider(
        host=settings.ollama_host,
        model=settings.ollama_model,
        extra_options=ex if ex else None,
        keep_alive=ka or None,
    )


def get_provider(settings) -> BaseLLMProvider:
    """Create the appropriate provider based on settings."""
    raw_provider = getattr(settings, "llm_provider", "ollama").lower()
    lm_studio_mode = raw_provider == "lm_studio"
    provider_name = "openai" if lm_studio_mode else raw_provider

    if provider_name == "codex":
        model = getattr(settings, "openai_model", "gpt-4o-mini")
        api_key = getattr(settings, "openai_api_key", "")

        if api_key:
            logger.info("Using OpenAI Codex with API key (model=%s)", model)
            return OpenAIProvider(api_key=api_key, model=model)

        # Prefer OAuth (native tool calling) over CLI subprocess
        from core.codex_auth import is_logged_in
        if is_logged_in():
            logger.info("Using Codex OAuth (model=%s)", model)
            return CodexOAuthProvider(model=model)

        # Fallback: CLI subprocess (no native tool calling)
        import shutil
        if shutil.which("codex"):
            cli_model = getattr(settings, "codex_cli_model", None) or model
            logger.info("Using Codex CLI provider (model=%s)", cli_model)
            return CodexCLIProvider(model=cli_model)

        logger.warning("Codex not available — use 'login codex' or install codex CLI")
        return CodexOAuthProvider(model=model)

    elif provider_name == "openai":
        api_key = (getattr(settings, "openai_api_key", "") or "").strip()
        base_url = (getattr(settings, "openai_base_url", "") or "").strip() or None
        if lm_studio_mode and not base_url:
            base_url = "http://127.0.0.1:1234/v1"
        if not api_key and not base_url:
            logger.warning("OpenAI API key not set (no base URL), falling back to Ollama")
            return make_ollama_provider(settings)
        if not api_key:
            api_key = "lm-studio"
        model = getattr(settings, "openai_model", "gpt-4o-mini")
        log_label = "LM Studio" if lm_studio_mode else "OpenAI-compatible"
        logger.info("Using %s API (model=%s, base_url=%s)", log_label, model, base_url or "default")
        return OpenAIProvider(api_key=api_key, model=model, base_url=base_url)

    elif provider_name == "anthropic" or provider_name == "claude":
        api_key = getattr(settings, "anthropic_api_key", "")
        model = getattr(settings, "anthropic_model", "claude-sonnet-4-20250514")

        # If API key exists, use native Anthropic API (fastest, best tool support)
        if api_key:
            logger.info("Using Anthropic provider (model=%s)", model)
            return AnthropicProvider(api_key=api_key, model=model)

        # No API key → try Claude CLI (uses existing Claude Max subscription)
        import shutil
        if shutil.which("claude"):
            logger.info("Using Claude CLI provider — no API key needed (model=%s)", model)
            return ClaudeCLIProvider(model=model)

        logger.warning("Anthropic API key not set and claude CLI not found, falling back to Ollama")
        return make_ollama_provider(settings)

    else:
        logger.info("Using Ollama provider (model=%s)", settings.ollama_model)
        return make_ollama_provider(settings)
