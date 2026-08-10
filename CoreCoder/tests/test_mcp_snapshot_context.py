from types import SimpleNamespace
from typing import ClassVar

import pytest

from corecoder import cli
from corecoder.agent import Agent
from corecoder.llm import LLM
from corecoder.mcp import McpRuntime, McpServerConfig
from corecoder.tools.base import Tool


class LargeTool(Tool):
    name = "large"
    description: ClassVar[str] = "x" * 1_200
    parameters: ClassVar[dict] = {"type": "object", "properties": {}, "required": []}

    def execute(self) -> str:
        return ""


def test_agent_fails_when_tool_definitions_exceed_the_context_window():
    with pytest.raises(ValueError, match="exceeding the 100-token context window"):
        Agent(llm=LLM.__new__(LLM), tools=[LargeTool()], max_context_tokens=100)


def test_agent_reports_a_high_startup_context_fraction():
    agent = Agent(llm=LLM.__new__(LLM), tools=[LargeTool()], max_context_tokens=2_000)

    assert agent.startup_context_warning
    assert agent.startup_context_tokens <= 2_000


def test_snapshot_notice_is_queued_once_without_replacing_tools():
    runtime = McpRuntime(McpServerConfig("local", "unused", [], {}))
    original_tools = runtime.tools

    runtime.mark_tool_snapshot_stale()
    runtime.mark_tool_snapshot_stale()

    assert runtime.tools is original_tools
    assert runtime.consume_tool_snapshot_notice() == "MCP tools changed. Restart CoreCoder to refresh the tool snapshot."
    assert runtime.consume_tool_snapshot_notice() is None


def test_repl_prints_the_queued_snapshot_notice_before_prompt(monkeypatch):
    prints = []
    runtime = SimpleNamespace(
        consume_tool_snapshot_notice=lambda: "MCP tools changed. Restart CoreCoder to refresh the tool snapshot.",
    )
    config = SimpleNamespace(
        primary=SimpleNamespace(model="test", base_url=None),
        fallbacks=[],
    )
    monkeypatch.setattr(cli.console, "print", prints.append)
    monkeypatch.setattr(cli, "FileHistory", lambda path: None)
    monkeypatch.setattr(cli, "pt_prompt", lambda *args, **kwargs: "quit")

    cli._repl(SimpleNamespace(), config, mcp_runtime=runtime)

    assert any("MCP tools changed" in str(item) for item in prints)


def test_repl_mcp_command_shows_connected_server_tool_counts(monkeypatch):
    prints = []
    runtime = SimpleNamespace(
        consume_tool_snapshot_notice=lambda: None,
        startup_reports=[
            SimpleNamespace(server_key="firecrawl", status="connected", tool_count=3),
            SimpleNamespace(server_key="broken", status="failed", tool_count=0),
        ],
    )
    config = SimpleNamespace(
        primary=SimpleNamespace(model="test", base_url=None),
        fallbacks=[],
    )
    inputs = iter(["/mcp", "quit"])
    monkeypatch.setattr(cli.console, "print", prints.append)
    monkeypatch.setattr(cli, "FileHistory", lambda path: None)
    monkeypatch.setattr(cli, "pt_prompt", lambda *args, **kwargs: next(inputs))

    cli._repl(SimpleNamespace(), config, mcp_runtime=runtime)

    assert "MCP firecrawl: connected (3 tools)" in prints
    assert not any("broken" in str(item) for item in prints)
