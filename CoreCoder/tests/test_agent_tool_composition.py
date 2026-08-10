from typing import ClassVar

import pytest

from corecoder.agent import Agent
from corecoder.llm import LLM
from corecoder.tools.agent import AgentTool
from corecoder.tools.base import Tool


class DummyTool(Tool):
    description: ClassVar[str] = "A test tool."
    parameters: ClassVar[dict] = {"type": "object", "properties": {}, "required": []}

    def __init__(self, name: str):
        self.name = name

    def execute(self) -> str:
        return self.name


def _agent(**kwargs) -> Agent:
    return Agent(llm=LLM.__new__(LLM), **kwargs)


def test_extra_tools_are_appended_to_default_tools():
    extra = DummyTool("external")

    agent = _agent(extra_tools=[extra])

    assert [tool.name for tool in agent.tools] == [
        "bash",
        "read_file",
        "write_file",
        "edit_file",
        "glob",
        "grep",
        "agent",
        "external",
    ]


def test_tools_remains_the_complete_tool_set():
    only_tool = DummyTool("only")
    extra = DummyTool("external")

    agent = _agent(tools=[only_tool], extra_tools=[extra])

    assert agent.tools == [only_tool, extra]


def test_explicit_empty_tools_creates_a_toolless_agent():
    agent = _agent(tools=[])

    assert agent.tools == []
    assert agent._tool_schemas() == []


@pytest.mark.parametrize(
    ("tools", "extra_tools"),
    [
        ([DummyTool("same"), DummyTool("same")], None),
        (None, [DummyTool("bash")]),
        ([DummyTool("same")], [DummyTool("same")]),
    ],
)
def test_duplicate_tool_names_are_rejected(tools, extra_tools):
    with pytest.raises(ValueError, match="Tool names must be unique; duplicates: "):
        _agent(tools=tools, extra_tools=extra_tools)


def test_schema_and_lookup_are_built_from_the_same_tool_set():
    tools = [DummyTool("first"), DummyTool("second")]
    agent = _agent(tools=tools)

    assert [schema["function"]["name"] for schema in agent._tool_schemas()] == ["first", "second"]
    assert list(agent._tool_by_name) == ["first", "second"]


def test_sub_agent_gets_fresh_default_tools_and_inherited_extras(monkeypatch):
    extra = DummyTool("external")
    parent = _agent(extra_tools=[extra])
    agent_tool = next(tool for tool in parent.tools if isinstance(tool, AgentTool))
    captured = {}

    def capture_chat(self, task):
        captured["sub"] = self
        return "done"

    monkeypatch.setattr(Agent, "chat", capture_chat)

    assert agent_tool.execute("sub-task") == "[Sub-agent completed]\ndone"

    sub = captured["sub"]
    assert "agent" not in [tool.name for tool in sub.tools]
    assert sub.extra_tools == [extra]
    assert sub.extra_tools[0] is extra
    assert sub._runtime_state is not parent._runtime_state
    assert sub.tools[0] is not parent.tools[0]
