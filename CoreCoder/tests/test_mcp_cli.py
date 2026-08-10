from types import SimpleNamespace

import pytest

from corecoder import cli


def test_no_mcp_skips_config_discovery(monkeypatch):
    monkeypatch.setattr(cli.Path, "home", classmethod(lambda cls: pytest.fail("home should not be read")))

    runtime, tools = cli._start_mcp(SimpleNamespace(no_mcp=True, mcp_config=None))

    assert runtime is None
    assert tools == []


def test_missing_default_config_leaves_mcp_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(cli.Path, "home", classmethod(lambda cls: tmp_path))

    runtime, tools = cli._start_mcp(SimpleNamespace(no_mcp=False, mcp_config=None))

    assert runtime is None
    assert tools == []


def test_missing_explicit_config_is_an_error(tmp_path):
    args = SimpleNamespace(no_mcp=False, mcp_config=str(tmp_path / "missing.json"))

    with pytest.raises(cli.McpError, match="configuration file not found"):
        cli._start_mcp(args)


def test_history_mcp_detection_uses_tool_call_names_only():
    assert cli._history_uses_mcp([
        {"role": "assistant", "tool_calls": [{"function": {"name": "mcp__server__tool"}}]},
    ])
    assert not cli._history_uses_mcp([
        {"role": "assistant", "content": "MCP is mentioned in text only"},
    ])


def test_tool_argument_brief_recursively_redacts_sensitive_values():
    summary = cli._brief({
        "query": "visible",
        "nested": {"apiToken": "must-not-appear", "items": [{"password": "hidden"}]},
    })

    assert "visible" in summary
    assert "must-not-appear" not in summary
    assert "hidden" not in summary
    assert "***" in summary


def test_parser_accepts_each_mcp_startup_option_and_rejects_both(monkeypatch):
    monkeypatch.setattr("sys.argv", ["corecoder", "--mcp-config", "alternate.json"])
    assert cli._parse_args().mcp_config == "alternate.json"

    monkeypatch.setattr("sys.argv", ["corecoder", "--no-mcp"])
    assert cli._parse_args().no_mcp

    monkeypatch.setattr("sys.argv", ["corecoder", "--mcp-config", "alternate.json", "--no-mcp"])
    with pytest.raises(SystemExit):
        cli._parse_args()


def test_startup_summary_does_not_include_server_launch_details():
    runtime = SimpleNamespace(startup_reports=[
        SimpleNamespace(server_key="safe", status="connected", tool_count=2, error=None),
        SimpleNamespace(server_key="broken", status="failed", tool_count=0, error="Connection failed (OSError)."),
    ])

    lines = cli._mcp_startup_summary(runtime)

    assert lines == [
        "MCP safe: connected (2 tools)",
        "MCP broken: failed (0 tools) — Connection failed (OSError).",
    ]


def test_help_lists_mcp_status_command(monkeypatch):
    prints = []
    monkeypatch.setattr(cli.console, "print", prints.append)

    cli._show_help()

    assert "/mcp           Show connected MCP servers" in str(prints[0].renderable)
