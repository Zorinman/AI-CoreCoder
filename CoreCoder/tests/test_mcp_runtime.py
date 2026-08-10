import json
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

mcp = pytest.importorskip("mcp")

from corecoder.agent import Agent
from corecoder.llm import LLM, ToolCall
from corecoder.mcp import MAX_MCP_RESULT_CHARS, McpConfigError, McpRuntime, _render_tool_result, load_mcp_config


def _config(tmp_path: Path) -> Path:
    config_path = tmp_path / "mcp.json"
    server_path = Path(__file__).parent / "fixtures" / "mcp_stdio_server.py"
    config_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "local": {
                        "command": sys.executable,
                        "args": [str(server_path)],
                        "env": {"FROM_CONFIG": "configured"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    return config_path


def _streamable_http_config(tmp_path: Path, port: int) -> Path:
    config_path = tmp_path / "mcp.json"
    config_path.write_text(
        json.dumps({"mcpServers": {"remote": {"url": f"http://127.0.0.1:{port}/mcp"}}}),
        encoding="utf-8",
    )
    return config_path


def _unused_tcp_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _wait_for_http_server(process: subprocess.Popen, port: int) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Local Streamable HTTP MCP Server exited during startup.")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError("Timed out starting the local Streamable HTTP MCP Server.")


def _stop_process(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def test_mcp_config_rejects_unknown_fields(tmp_path):
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"mcpServers": {}, "extra": True}), encoding="utf-8")

    with pytest.raises(McpConfigError, match="only the 'mcpServers' object"):
        load_mcp_config(path)


def test_mcp_config_accepts_streamable_http_url(tmp_path):
    path = tmp_path / "mcp.json"
    path.write_text(
        json.dumps({"mcpServers": {"remote": {"url": "https://mcp.example.com/v2/mcp"}}}),
        encoding="utf-8",
    )

    server = load_mcp_config(path)[0]

    assert server.key == "remote"
    assert server.url == "https://mcp.example.com/v2/mcp"
    assert server.command is None
    assert server.args == []
    assert server.env == {}


@pytest.mark.parametrize(
    "server",
    [
        {"url": "https://mcp.example.com/mcp", "command": "python"},
        {"url": "https://mcp.example.com/mcp", "args": []},
        {"url": "https://mcp.example.com/mcp", "env": {}},
        {"url": "ftp://mcp.example.com/mcp"},
        {"url": "https:///mcp"},
    ],
)
def test_mcp_config_rejects_invalid_streamable_http_server(tmp_path, server):
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"mcpServers": {"remote": server}}), encoding="utf-8")

    with pytest.raises(McpConfigError):
        load_mcp_config(path)


def test_stdio_mcp_tool_runs_through_the_agent(tmp_path):
    config = load_mcp_config(_config(tmp_path))
    runtime = McpRuntime(config[0])
    try:
        tools = runtime.start()
        assert {tool.name for tool in tools} == {
            "mcp__local__echo",
            "mcp__local__long_output",
            "mcp__local__notify_tools_changed",
            "mcp__local__tool_error",
        }
        echo_tool = next(tool for tool in tools if tool.name == "mcp__local__echo")
        assert echo_tool.parameters["type"] == "object"

        agent = Agent(llm=LLM.__new__(LLM), extra_tools=tools)
        result = agent._exec_tool(ToolCall("call-1", "mcp__local__echo", {"message": "hello"}))
        assert "echo: hello" in result
        assert "original length:" in agent._exec_tool(ToolCall("call-2", "mcp__local__long_output", {}))
        assert "server rejected" in agent._exec_tool(ToolCall("call-3", "mcp__local__tool_error", {})).lower()
        assert "notification sent" in agent._exec_tool(ToolCall("call-4", "mcp__local__notify_tools_changed", {}))

        notice = None
        for _ in range(20):
            notice = runtime.consume_tool_snapshot_notice()
            if notice:
                break
            time.sleep(0.05)
        assert notice == "MCP tools changed. Restart CoreCoder to refresh the tool snapshot."
    finally:
        runtime.close()
    assert not any(thread.name == "mcp-runtime" and thread.is_alive() for thread in threading.enumerate())


def test_streamable_http_mcp_tool_runs_through_the_agent(tmp_path):
    port = _unused_tcp_port()
    server_path = Path(__file__).parent / "fixtures" / "mcp_streamable_http_server.py"
    process = subprocess.Popen(
        [sys.executable, str(server_path), str(port)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    runtime = None
    try:
        _wait_for_http_server(process, port)
        runtime = McpRuntime(load_mcp_config(_streamable_http_config(tmp_path, port)))
        tools = runtime.start()

        assert [tool.name for tool in tools] == ["mcp__remote__echo"]
        assert [(report.server_key, report.status, report.tool_count) for report in runtime.startup_reports] == [
            ("remote", "connected", 1)
        ]
        echo_tool = tools[0]
        agent = Agent(llm=LLM.__new__(LLM), extra_tools=tools)
        assert "echo: hello" in agent._exec_tool(ToolCall("call-1", echo_tool.name, {"message": "hello"}))
    finally:
        if runtime is not None:
            runtime.close()
        _stop_process(process)


def test_closed_mcp_runtime_reports_an_unavailable_tool():
    from corecoder.mcp import McpServerConfig

    runtime = McpRuntime(McpServerConfig("local", "unused", [], {}))
    runtime.close()

    assert "unavailable" in runtime.call_tool("echo", {}).lower()


def test_multiple_servers_use_config_key_namespaces_and_isolate_failures(tmp_path):
    config_path = _config(tmp_path)
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    raw["mcpServers"]["broken"] = {"command": sys.executable, "args": ["-c", "raise SystemExit(1)"]}
    raw["mcpServers"]["other"] = raw["mcpServers"]["local"].copy()
    config_path.write_text(json.dumps(raw), encoding="utf-8")

    runtime = McpRuntime(load_mcp_config(config_path))
    try:
        tools = runtime.start()

        assert {tool.name for tool in tools} == {
            "mcp__local__echo",
            "mcp__local__long_output",
            "mcp__local__notify_tools_changed",
            "mcp__local__tool_error",
            "mcp__other__echo",
            "mcp__other__long_output",
            "mcp__other__notify_tools_changed",
            "mcp__other__tool_error",
        }
        assert [(report.server_key, report.status, report.tool_count) for report in runtime.startup_reports] == [
            ("local", "connected", 4),
            ("broken", "failed", 0),
            ("other", "connected", 4),
        ]
    finally:
        runtime.close()


def test_mcp_result_supports_text_json_unsupported_content_and_truncation():
    text_and_json = SimpleNamespace(
        content=[SimpleNamespace(text="text result")],
        structured_content={"answer": 42},
    )
    assert _render_tool_result(text_and_json) == 'text result\n{"answer": 42}'

    unsupported = SimpleNamespace(content=[SimpleNamespace(data=b"binary")], structured_content=None)
    assert "unsupported MCP result content type" in _render_tool_result(unsupported)

    long_result = SimpleNamespace(content=[SimpleNamespace(text="a" * (MAX_MCP_RESULT_CHARS + 1))], structured_content=None)
    rendered = _render_tool_result(long_result)
    assert len(rendered) <= MAX_MCP_RESULT_CHARS
    assert rendered.startswith("a") and rendered.endswith("a")
    assert "original length:" in rendered


def test_all_failed_mcp_servers_prevent_startup(tmp_path):
    path = tmp_path / "mcp.json"
    path.write_text(
        json.dumps({"mcpServers": {"broken": {"command": sys.executable, "args": ["-c", "raise SystemExit(1)"]}}}),
        encoding="utf-8",
    )
    runtime = McpRuntime(load_mcp_config(path))

    with pytest.raises(RuntimeError, match="no usable tools"):
        runtime.start()
