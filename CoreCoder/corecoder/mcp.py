"""MCP client integration.

The MCP SDK stays behind this module's runtime boundary so ordinary CoreCoder
use does not require the optional dependency.
"""

import asyncio
import json
import os
import re
import threading
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeoutError
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .tools.base import Tool

CONNECT_TIMEOUT_SECONDS = 30
TOOL_TIMEOUT_SECONDS = 120
MAX_MCP_RESULT_CHARS = 15_000


class McpError(RuntimeError):
    """Base error for MCP configuration and runtime failures."""


class McpConfigError(McpError):
    """Raised when an MCP configuration is invalid."""


class McpDependencyError(McpError):
    """Raised only when MCP is requested without its optional dependency."""


class McpRuntimeError(McpError):
    """Raised when an MCP server cannot be used."""


@dataclass(frozen=True)
class McpServerConfig:
    """Validated stdio or Streamable HTTP MCP Server definition."""

    key: str
    command: str | None
    args: list[str]
    env: dict[str, str]
    url: str | None = None


_ENV_REFERENCE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_MODEL_TOOL_NAME = re.compile(r"[A-Za-z0-9_-]+$")


def load_mcp_config(path: str | Path) -> list[McpServerConfig]:
    """Load a strict MCP configuration from an explicit JSON file."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise McpConfigError(f"Unable to read MCP configuration: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise McpConfigError(f"Invalid MCP configuration JSON: {exc.msg}") from exc

    if not isinstance(raw, dict) or set(raw) != {"mcpServers"}:
        raise McpConfigError("MCP configuration must contain only the 'mcpServers' object.")
    servers = raw["mcpServers"]
    if not isinstance(servers, dict):
        raise McpConfigError("'mcpServers' must be an object.")

    parsed: list[McpServerConfig] = []
    for key, server in servers.items():
        if not isinstance(key, str) or not key:
            raise McpConfigError("MCP Server names must be non-empty strings.")
        parsed.append(_parse_server(key, server))
    return parsed


def _parse_server(key: str, raw: Any) -> McpServerConfig:
    if not isinstance(raw, dict) or set(raw) - {"command", "args", "env", "url"}:
        raise McpConfigError(f"MCP Server '{key}' has unsupported fields.")

    if "url" in raw:
        if set(raw) != {"url"}:
            raise McpConfigError(f"MCP Server '{key}' must use either stdio fields or 'url'.")
        url = raw["url"]
        if not isinstance(url, str) or not url.strip():
            raise McpConfigError(f"MCP Server '{key}' requires a non-empty string 'url'.")
        parsed = urlparse(url)
        try:
            has_host = bool(parsed.hostname)
        except ValueError:
            has_host = False
        if parsed.scheme not in {"http", "https"} or not has_host or parsed.fragment:
            raise McpConfigError(f"MCP Server '{key}' requires an absolute HTTP(S) 'url'.")
        return McpServerConfig(key=key, command=None, args=[], env={}, url=url)

    command = raw.get("command")
    if not isinstance(command, str) or not command.strip():
        raise McpConfigError(f"MCP Server '{key}' requires a non-empty string 'command'.")

    args = raw.get("args", [])
    if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
        raise McpConfigError(f"MCP Server '{key}' field 'args' must be an array of strings.")

    env = raw.get("env", {})
    if not isinstance(env, dict) or not all(isinstance(name, str) and isinstance(value, str) for name, value in env.items()):
        raise McpConfigError(f"MCP Server '{key}' field 'env' must map strings to strings.")

    return McpServerConfig(
        key=key,
        command=command,
        args=args,
        env={name: _expand_environment(value) for name, value in env.items()},
    )


def _expand_environment(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        variable = match.group(1)
        if variable not in os.environ:
            raise McpConfigError(f"MCP environment variable '{variable}' is not set.")
        return os.environ[variable]

    return _ENV_REFERENCE.sub(replace, value)


class McpTool(Tool):
    """Synchronously adapts an MCP tool for the Agent tool interface."""

    concurrency_safe = False

    def __init__(self, runtime: "McpRuntime", server_key: str, tool: Any, model_name: str):
        self._runtime = runtime
        self._server_key = server_key
        self._remote_name = tool.name
        self.name = model_name
        self.description = tool.description or f"MCP tool '{tool.name}' from '{server_key}'."
        self.parameters = getattr(tool, "input_schema", None)
        if self.parameters is None:
            self.parameters = tool.inputSchema

    def execute(self, **kwargs) -> str:
        return self._runtime.call_tool(self.name, kwargs)


@dataclass(frozen=True)
class McpStartupReport:
    """Display-safe connection outcome for one configured server."""

    server_key: str
    status: str
    tool_count: int
    error: str | None = None


class McpRuntime:
    """Owns all stdio MCP sessions and their one background event loop."""

    def __init__(
        self,
        servers: McpServerConfig | list[McpServerConfig],
        *,
        connect_timeout_seconds: float = CONNECT_TIMEOUT_SECONDS,
        tool_timeout_seconds: float = TOOL_TIMEOUT_SECONDS,
    ):
        self.servers = [servers] if isinstance(servers, McpServerConfig) else list(servers)
        self.connect_timeout_seconds = connect_timeout_seconds
        self.tool_timeout_seconds = tool_timeout_seconds
        self.tools: list[McpTool] = []
        self.startup_reports: list[McpStartupReport] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._started = threading.Event()
        self._stack: AsyncExitStack | None = None
        self._session_group: Any = None
        self._tool_snapshot_stale = threading.Event()
        self._closed = False

    def start(self) -> list[McpTool]:
        """Start the configured server, discover its tools, and return adapters."""
        if self._closed:
            raise McpRuntimeError("MCP runtime is closed.")
        if self._loop is not None:
            return list(self.tools)

        self._thread = threading.Thread(target=self._run_loop, name="mcp-runtime", daemon=True)
        self._thread.start()
        if not self._started.wait(self.connect_timeout_seconds):
            self.close()
            raise McpRuntimeError("Timed out starting the MCP runtime.")

        try:
            remote_tools, reports = self._submit(self._connect()).result(timeout=self.connect_timeout_seconds)
        except FutureTimeoutError as exc:
            self.close()
            raise McpRuntimeError("Timed out connecting configured MCP Servers.") from exc
        except Exception as exc:
            self.close()
            if isinstance(exc, McpError):
                raise
            raise McpRuntimeError(f"Unable to initialize MCP Servers: {type(exc).__name__}.") from exc

        self.tools = remote_tools
        self.startup_reports = reports
        return list(self.tools)

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """Call one discovered MCP tool without retries."""
        if self._closed or self._session_group is None:
            return "Error: MCP Server is unavailable. Restart CoreCoder to reconnect."
        try:
            result = self._submit(self._session_group.call_tool(name, arguments)).result(timeout=self.tool_timeout_seconds)
        except FutureTimeoutError:
            return f"Error: MCP tool '{name}' timed out after {self.tool_timeout_seconds:g} seconds."
        except Exception as exc:  # noqa: BLE001 - preserve MCP transport failures as tool results.
            return f"Error: MCP protocol or connection failure calling '{name}': {exc}"
        return _render_tool_result(result)

    def close(self) -> None:
        """Close the MCP session and stop its event loop."""
        if self._closed:
            return
        self._closed = True
        if self._loop is None:
            return
        try:
            self._submit(self._close_async()).result(timeout=5)
        except Exception:  # noqa: BLE001, S110 - cleanup must continue after a failed graceful close.
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=5)

    def mark_tool_snapshot_stale(self) -> None:
        """Queue one restart notice without mutating the current tool snapshot."""
        self._tool_snapshot_stale.set()

    def consume_tool_snapshot_notice(self) -> str | None:
        """Return and clear the queued restart notice, if any."""
        if not self._tool_snapshot_stale.is_set():
            return None
        self._tool_snapshot_stale.clear()
        return "MCP tools changed. Restart CoreCoder to refresh the tool snapshot."

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        self._started.set()
        loop.run_forever()
        loop.close()

    async def _connect(self) -> tuple[list[McpTool], list[McpStartupReport]]:
        try:
            from mcp import ClientSession
            from mcp.client.session_group import ClientSessionGroup
            from mcp.client.stdio import StdioServerParameters, stdio_client
            from mcp.client.streamable_http import streamable_http_client
            from mcp.types import Implementation, ToolListChangedNotification
        except ModuleNotFoundError as exc:
            raise McpDependencyError("MCP support requires 'pip install corecoder[mcp]'.") from exc

        self._stack = AsyncExitStack()
        self._session_group = ClientSessionGroup(
            exit_stack=self._stack,
            component_name_hook=lambda name, server: f"mcp__{server.name}__{name}",
        )
        tools: list[McpTool] = []
        reports: list[McpStartupReport] = []

        for server in self.servers:
            server_stack = AsyncExitStack()
            try:
                if server.url is not None:
                    read_stream, write_stream = await server_stack.enter_async_context(streamable_http_client(server.url))
                else:
                    assert server.command is not None
                    parameters = StdioServerParameters(
                        command=server.command,
                        args=server.args,
                        env={**os.environ, **server.env},
                        cwd=os.getcwd(),
                    )
                    read_stream, write_stream = await server_stack.enter_async_context(stdio_client(parameters))
                async def message_handler(message) -> None:
                    if isinstance(message, ToolListChangedNotification):
                        self.mark_tool_snapshot_stale()

                session = await server_stack.enter_async_context(
                    ClientSession(read_stream, write_stream, message_handler=message_handler)
                )
                await session.initialize()
                discovered = list((await session.list_tools()).tools)
                adapters = self._validated_tools(server, discovered)
                await self._session_group.connect_with_session(
                    Implementation(name=server.key, version=""),
                    session,
                )
            except Exception as exc:  # noqa: BLE001 - isolate failures from untrusted external servers.
                await server_stack.aclose()
                reports.append(McpStartupReport(
                    server.key,
                    "failed",
                    0,
                    f"Connection or registration failed ({type(exc).__name__}).",
                ))
                continue

            self._stack.push_async_callback(server_stack.aclose)
            tools.extend(adapters)
            if adapters:
                reports.append(McpStartupReport(server.key, "connected", len(adapters)))
            else:
                reports.append(McpStartupReport(server.key, "connected", 0, "No tools were exposed."))

        if not tools:
            raise McpRuntimeError("All configured MCP Servers failed or exposed no usable tools.")
        return tools, reports

    def _validated_tools(self, server: McpServerConfig, discovered: list[Any]) -> list[McpTool]:
        adapters: list[McpTool] = []
        names: set[str] = set()
        for tool in discovered:
            remote_name = getattr(tool, "name", None)
            model_name = f"mcp__{server.key}__{remote_name}"
            if not isinstance(remote_name, str) or not _MODEL_TOOL_NAME.fullmatch(model_name):
                raise McpRuntimeError(f"MCP Server '{server.key}' exposed an invalid tool name.")
            if len(model_name) > 64:
                raise McpRuntimeError(f"MCP Server '{server.key}' exposed an overlong tool name.")
            if model_name in names:
                raise McpRuntimeError(f"MCP Server '{server.key}' exposed duplicate tool names.")

            schema = getattr(tool, "input_schema", None)
            if schema is None:
                schema = getattr(tool, "inputSchema", None)
            if not _valid_tool_schema(schema):
                raise McpRuntimeError(f"MCP Server '{server.key}' exposed an invalid tool schema.")
            names.add(model_name)
            adapters.append(McpTool(self, server.key, tool, model_name))
        return adapters

    async def _close_async(self) -> None:
        if self._stack is not None:
            await self._stack.aclose()
        self._session_group = None

    def _submit(self, coroutine) -> Future:
        if self._loop is None:
            raise McpRuntimeError("MCP runtime has not started.")
        return asyncio.run_coroutine_threadsafe(coroutine, self._loop)


def _render_tool_result(result: Any) -> str:
    """Convert the supported MCP result types into a stable tool-result string."""
    parts: list[str] = []
    for content in getattr(result, "content", []):
        text = getattr(content, "text", None)
        if isinstance(text, str):
            parts.append(text)
        else:
            return f"Error: unsupported MCP result content type '{type(content).__name__}'."

    structured = getattr(result, "structured_content", None)
    if structured is not None:
        parts.append(json.dumps(structured, ensure_ascii=False, sort_keys=True, default=str))
    if not parts:
        return ""
    return _truncate_result("\n".join(parts))


def _truncate_result(result: str) -> str:
    if len(result) <= MAX_MCP_RESULT_CHARS:
        return result
    marker = f"\n... [MCP output truncated; original length: {len(result)} characters] ...\n"
    retained = (MAX_MCP_RESULT_CHARS - len(marker)) // 2
    return f"{result[:retained]}{marker}{result[-retained:]}"


def _valid_tool_schema(schema: Any) -> bool:
    if not isinstance(schema, dict) or schema.get("type") != "object":
        return False
    properties = schema.get("properties", {})
    required = schema.get("required", [])
    return isinstance(properties, dict) and isinstance(required, list) and all(isinstance(name, str) for name in required)
