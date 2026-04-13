"""MCP (Model Context Protocol) support — expose Jarvis skills as MCP tools
and consume external MCP servers.

MCP is the standard protocol for connecting LLMs to tools. This module lets:
1. External clients (Claude Code, Cursor, etc.) call Jarvis skills via MCP
2. Jarvis consume tools from external MCP servers (stdio/HTTP)

Protocol: JSON-RPC 2.0 over stdio or HTTP/SSE.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

from core.skill_base import SkillRegistry

logger = logging.getLogger(__name__)

MCP_PROTOCOL_VERSION = "2025-11-25"


# ── Data types ───────────────────────────────────────────────────────

@dataclass
class MCPTool:
    """A tool exposed or consumed via MCP."""
    name: str
    description: str
    input_schema: dict = field(default_factory=dict)
    server_name: str = ""


@dataclass
class MCPToolResult:
    content: str
    is_error: bool = False


# ── MCP Server (expose Jarvis skills) ───────────────────────────────

class MCPServer:
    """Expose Jarvis skills as MCP tools over stdio (JSON-RPC 2.0).

    Usage:
        server = MCPServer(registry)
        await server.run_stdio()  # reads stdin, writes stdout
    """

    def __init__(self, registry: SkillRegistry):
        self.registry = registry
        self._running = False

    def _build_tool_list(self) -> list[dict]:
        """Convert skill tools to MCP tool format."""
        tools = []
        for skill in self.registry.all_skills():
            for tool_def in skill.as_tools():
                func = tool_def["function"]
                tools.append({
                    "name": func["name"],
                    "description": func.get("description", ""),
                    "inputSchema": func.get("parameters", {
                        "type": "object",
                        "properties": {},
                    }),
                })
        return tools

    async def _handle_request(self, request: dict) -> dict:
        """Handle a single JSON-RPC request."""
        method = request.get("method", "")
        req_id = request.get("id")
        params = request.get("params", {})

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "serverInfo": {"name": "jarvis", "version": "1.0.0"},
                    "capabilities": {
                        "tools": {"listChanged": False},
                    },
                },
            }

        elif method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": self._build_tool_list()},
            }

        elif method == "tools/call":
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})
            resolved = self.registry.resolve_tool_call(tool_name)

            if not resolved:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}],
                        "isError": True,
                    },
                }

            skill, action = resolved
            try:
                result = await skill.execute(action, arguments)
                text = json.dumps(result, ensure_ascii=False, default=str)
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": text}],
                        "isError": False,
                    },
                }
            except Exception as e:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": f"Error: {e}"}],
                        "isError": True,
                    },
                }

        elif method == "ping":
            return {"jsonrpc": "2.0", "id": req_id, "result": {}}

        else:
            # Notifications (no id) are ignored
            if req_id is not None:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                }
            return {}

    async def run_stdio(self):
        """Run the MCP server over stdio (for Claude Code, Cursor, etc.)."""
        self._running = True
        logger.info("MCP Server: listening on stdio")
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await asyncio.get_event_loop().connect_read_pipe(lambda: protocol, sys.stdin)

        while self._running:
            try:
                line = await reader.readline()
                if not line:
                    break
                line = line.decode("utf-8").strip()
                if not line:
                    continue
                request = json.loads(line)
                response = await self._handle_request(request)
                if response:
                    out = json.dumps(response, ensure_ascii=False) + "\n"
                    sys.stdout.write(out)
                    sys.stdout.flush()
            except json.JSONDecodeError as e:
                logger.warning("MCP: invalid JSON — %s", e)
            except Exception as e:
                logger.error("MCP: error — %s", e)

    def stop(self):
        self._running = False


# ── MCP Client (consume external MCP servers) ───────────────────────

class MCPClient:
    """Connect to an external MCP server and discover/call its tools.

    Supports stdio transport (spawns subprocess) and HTTP+SSE.
    """

    def __init__(self, name: str = "external"):
        self.name = name
        self._process: asyncio.subprocess.Process | None = None
        self._tools: list[MCPTool] = []
        self._req_id = 0

    async def connect_stdio(self, command: list[str], env: dict | None = None) -> bool:
        """Connect to an MCP server by spawning it as a subprocess."""
        import os
        merged_env = {**os.environ, **(env or {})}
        try:
            self._process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=merged_env,
            )
            # Initialize
            init_resp = await self._send_request("initialize", {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "clientInfo": {"name": "jarvis", "version": "1.0.0"},
                "capabilities": {},
            })
            if not init_resp:
                return False
            # Send initialized notification
            await self._send_notification("notifications/initialized", {})
            # List tools
            tools_resp = await self._send_request("tools/list", {})
            if tools_resp and "tools" in tools_resp:
                self._tools = [
                    MCPTool(
                        name=t["name"],
                        description=t.get("description", ""),
                        input_schema=t.get("inputSchema", {}),
                        server_name=self.name,
                    )
                    for t in tools_resp["tools"]
                ]
            logger.info("MCP Client: connected to %s, %d tools", self.name, len(self._tools))
            return True
        except Exception as e:
            logger.error("MCP Client: connection failed — %s", e)
            return False

    async def _send_request(self, method: str, params: dict) -> dict | None:
        if not self._process or not self._process.stdin or not self._process.stdout:
            return None
        self._req_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": self._req_id,
            "method": method,
            "params": params,
        }
        line = json.dumps(request, ensure_ascii=False) + "\n"
        self._process.stdin.write(line.encode("utf-8"))
        await self._process.stdin.drain()
        # Read response
        try:
            resp_line = await asyncio.wait_for(
                self._process.stdout.readline(), timeout=30.0,
            )
            if resp_line:
                resp = json.loads(resp_line.decode("utf-8"))
                return resp.get("result", resp.get("error"))
        except asyncio.TimeoutError:
            logger.warning("MCP Client: timeout waiting for response")
        return None

    async def _send_notification(self, method: str, params: dict):
        if not self._process or not self._process.stdin:
            return
        notification = {"jsonrpc": "2.0", "method": method, "params": params}
        line = json.dumps(notification, ensure_ascii=False) + "\n"
        self._process.stdin.write(line.encode("utf-8"))
        await self._process.stdin.drain()

    @property
    def tools(self) -> list[MCPTool]:
        return self._tools

    async def call_tool(self, tool_name: str, arguments: dict) -> MCPToolResult:
        """Call a tool on the connected MCP server."""
        resp = await self._send_request("tools/call", {
            "name": tool_name,
            "arguments": arguments,
        })
        if resp is None:
            return MCPToolResult(content="No response from MCP server", is_error=True)
        if isinstance(resp, dict):
            content_list = resp.get("content", [])
            text = "\n".join(c.get("text", "") for c in content_list if c.get("type") == "text")
            return MCPToolResult(content=text, is_error=resp.get("isError", False))
        return MCPToolResult(content=str(resp))

    async def disconnect(self):
        if self._process:
            self._process.terminate()
            await self._process.wait()
            logger.info("MCP Client: disconnected from %s", self.name)


# ── MCP Manager (manage multiple external servers) ──────────────────

class MCPManager:
    """Manages connections to multiple MCP servers."""

    def __init__(self):
        self._clients: dict[str, MCPClient] = {}

    async def add_server(self, name: str, command: list[str], env: dict | None = None) -> bool:
        client = MCPClient(name=name)
        if await client.connect_stdio(command, env):
            self._clients[name] = client
            return True
        return False

    def get_all_tools(self) -> list[MCPTool]:
        tools = []
        for client in self._clients.values():
            tools.extend(client.tools)
        return tools

    async def call_tool(self, tool_name: str, arguments: dict) -> MCPToolResult:
        for client in self._clients.values():
            for t in client.tools:
                if t.name == tool_name:
                    return await client.call_tool(tool_name, arguments)
        return MCPToolResult(content=f"Tool not found: {tool_name}", is_error=True)

    async def shutdown(self):
        for client in self._clients.values():
            await client.disconnect()
        self._clients.clear()
