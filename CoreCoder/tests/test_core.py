"""Tests for core modules: config, context, session, imports."""

import os

from corecoder import LLM, Agent, Config, __version__
from corecoder import session as session_module
from corecoder.context import ContextManager, estimate_tokens
from corecoder.session import list_sessions, load_session, save_session
from corecoder.tools.base import Tool
from corecoder.tools.read import ReadFileTool


def test_version():
    assert __version__ == "0.4.0"


def test_public_api_exports():
    """Users should be able to import key classes from the top-level package."""
    assert Agent is not None
    assert LLM is not None
    assert Config is not None
    import corecoder
    assert not hasattr(corecoder, "ALL_TOOLS")


def _isolate_config_environment(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in list(os.environ):
        if name.startswith("CORECODER_") or name in {"OPENAI_API_KEY", "OPENAI_BASE_URL"}:
            monkeypatch.delenv(name)


def test_config_from_env(tmp_path, monkeypatch):
    _isolate_config_environment(tmp_path, monkeypatch)
    monkeypatch.setenv("CORECODER_PRIMARY_MODEL", "test-model")
    monkeypatch.setenv("CORECODER_PRIMARY_API_KEY", "test-key")
    c = Config.from_env()
    assert c.primary.model == "test-model"


def test_config_defaults(tmp_path, monkeypatch):
    _isolate_config_environment(tmp_path, monkeypatch)
    monkeypatch.setenv("CORECODER_PRIMARY_MODEL", "test-model")
    monkeypatch.setenv("CORECODER_PRIMARY_API_KEY", "test-key")

    c = Config.from_env()
    assert c.primary.max_tokens == 4096
    assert c.primary.temperature == 0.0
    assert c.max_context_tokens == 128_000


# --- Context ---

def test_estimate_tokens():
    msgs = [{"role": "user", "content": "hello world"}]
    t = estimate_tokens(msgs)
    assert t > 0
    assert t < 100


def test_context_snip():
    ctx = ContextManager(max_tokens=3000)
    msgs = [
        {"role": "tool", "tool_call_id": "t1", "content": "x\n" * 1000},
    ]
    before = estimate_tokens(msgs)
    ctx._snip_tool_outputs(msgs)
    after = estimate_tokens(msgs)
    assert after < before


def test_context_compress():
    ctx = ContextManager(max_tokens=2000)
    msgs = []
    for i in range(20):
        msgs.append({"role": "user", "content": f"msg {i} " + "a" * 200})
        msgs.append({"role": "tool", "tool_call_id": f"t{i}", "content": "b" * 2000})
    before = estimate_tokens(msgs)
    ctx.maybe_compress(msgs, None)
    after = estimate_tokens(msgs)
    assert after < before
    assert len(msgs) < 40  # should be compressed


def test_safe_split_never_orphans_a_tool_message():
    """The kept tail must not begin with a 'tool' message - it would be severed
    from the assistant tool_calls that produced it, which the API rejects."""
    ctx = ContextManager(max_tokens=1000)
    messages = [
        {"role": "user", "content": "do it"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1"}]},
        {"role": "tool", "tool_call_id": "c1", "content": "result"},
        {"role": "tool", "tool_call_id": "c2", "content": "result2"},
    ]
    split = ctx._safe_split(messages, keep_recent=1)
    assert messages[split].get("role") != "tool"


def test_compress_never_leaves_an_orphan_tool_reply():
    """After summarisation every tool reply must still follow its tool_calls."""
    ctx = ContextManager(max_tokens=2000)
    msgs = []
    for i in range(20):
        msgs.append({"role": "user", "content": f"msg {i} " + "a" * 200})
        msgs.append({"role": "assistant", "content": None, "tool_calls": [{"id": f"c{i}"}]})
        msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": "b" * 800})
    ctx.maybe_compress(msgs, None)
    for i, m in enumerate(msgs):
        if m.get("role") == "tool":
            prev = msgs[i - 1]
            assert prev.get("role") == "tool" or prev.get("tool_calls"), f"orphan tool at {i}"


# --- Session ---

def test_session_save_load(tmp_path, monkeypatch):
    monkeypatch.setattr(session_module, "SESSIONS_DIR", tmp_path)
    msgs = [{"role": "user", "content": "test message"}]
    save_session(msgs, "test-model", "pytest_test_session")
    loaded = load_session("pytest_test_session")
    assert loaded is not None
    assert loaded[0] == msgs
    assert loaded[1] == "test-model"


def test_session_name_is_sanitized(tmp_path, monkeypatch):
    monkeypatch.setattr(session_module, "SESSIONS_DIR", tmp_path)
    msgs = [{"role": "user", "content": "test message"}]
    sid = save_session(msgs, "test-model", "../Research Notes!")

    assert sid == "Research-Notes"
    assert (tmp_path / "Research-Notes.json").exists()
    assert load_session("../Research Notes!") is not None


def test_session_not_found():
    assert load_session("nonexistent_session_id") is None


def test_list_sessions():
    sessions = list_sessions()
    assert isinstance(sessions, list)


# --- Cost estimation ---

def test_cost_estimation_known_model():
    from corecoder.llm import LLM
    llm = LLM.__new__(LLM)
    llm.model = "gpt-5.4"
    llm.total_prompt_tokens = 1_000_000
    llm.total_completion_tokens = 500_000
    cost = llm.estimated_cost
    assert cost is not None
    assert cost == 2.5 + 7.5  # $2.5/M in + $15/M out * 0.5M

def test_cost_estimation_unknown_model():
    from corecoder.llm import LLM
    llm = LLM.__new__(LLM)
    llm.model = "some-custom-model"
    llm.total_prompt_tokens = 1000
    llm.total_completion_tokens = 500
    assert llm.estimated_cost is None


def test_cost_knowledge_is_exposed_by_the_llm_interface():
    from corecoder.llm import LLM

    known = LLM.__new__(LLM)
    known.model = "gpt-5.4"
    unknown = LLM.__new__(LLM)
    unknown.model = "private-model"

    assert known.has_known_cost is True
    assert unknown.has_known_cost is False


# --- Agent-owned default tools and runtime state ---

def test_default_agents_keep_tools_and_changed_files_isolated(tmp_path):
    first = Agent(llm=LLM.__new__(LLM))
    second = Agent(llm=LLM.__new__(LLM))

    first_by_name = {tool.name: tool for tool in first.tools}
    second_by_name = {tool.name: tool for tool in second.tools}
    assert first_by_name["agent"] is not second_by_name["agent"]
    assert first_by_name["agent"]._parent_agent is first
    assert second_by_name["agent"]._parent_agent is second

    path = tmp_path / "tracked.txt"
    first_by_name["write_file"].execute(file_path=str(path), content="tracked\n")
    assert first.changed_files == frozenset({str(path)})
    assert second.changed_files == frozenset()


# --- Agent tool execution ---

def test_agent_tool_scope_is_per_instance():
    """An Agent restricted to a subset of tools must not resolve tools outside it."""
    only_read = [ReadFileTool()]
    agent = Agent(llm=LLM.__new__(LLM), tools=only_read)
    assert set(agent._tool_by_name) == {"read_file"}

    class _TC:
        name = "bash"  # a real, registered tool - but not in this agent's set
        id = "x"
        arguments = {"command": "echo hi"}

    assert "unknown tool 'bash'" in agent._exec_tool(_TC())


def test_exec_tool_distinguishes_bad_args_from_internal_error():
    """A TypeError raised inside a tool must not be reported as bad arguments."""
    from corecoder.tools.base import Tool

    class _Boom(Tool):
        name = "boom"
        description = "raises TypeError internally"
        parameters = {"type": "object", "properties": {}, "required": []}

        def execute(self):
            raise TypeError("internal explosion")

    agent = Agent(llm=LLM.__new__(LLM), tools=[_Boom()])

    class _BadArgs:
        name, id, arguments = "boom", "1", {"unexpected": 1}

    class _Good:
        name, id, arguments = "boom", "2", {}

    assert "bad arguments" in agent._exec_tool(_BadArgs())
    assert "Error executing boom" in agent._exec_tool(_Good())
    assert "bad arguments" not in agent._exec_tool(_Good())


def test_interrupt_backfills_missing_tool_replies():
    """A half-finished tool round must be repaired so history stays valid."""
    agent = Agent(llm=LLM.__new__(LLM), tools=[])
    agent.messages = [
        {"role": "assistant", "content": None, "tool_calls": [{"id": "a"}, {"id": "b"}]},
        {"role": "tool", "tool_call_id": "a", "content": "done"},
    ]

    class _TC:
        def __init__(self, i):
            self.id = i

    agent._answer_pending_tool_calls([_TC("a"), _TC("b")])
    replies = [m for m in agent.messages if m.get("role") == "tool"]
    ids = [m["tool_call_id"] for m in replies]
    assert sorted(ids) == ["a", "b"]
    assert ids.count("a") == 1  # the already-answered call wasn't duplicated


# --- Concurrency-safe tool scheduling ---

def _make_tc(name, call_id):
    class _TC:
        pass
    tc = _TC()
    tc.name = name
    tc.id = call_id
    tc.arguments = {}
    return tc


def test_tool_base_defaults_to_unsafe():
    from corecoder.tools.base import Tool

    class _Undeclared(Tool):
        name = "undeclared"
        description = "does not declare concurrency_safe"
        parameters = {"type": "object", "properties": {}, "required": []}

        def execute(self):
            return "ok"

    assert _Undeclared.concurrency_safe is False


def test_segment_tool_calls_groups_consecutive_runs():
    """safe, safe, unsafe, safe -> three segments, in original order."""

    class _Safe(Tool):
        name = "safe"
        description = "x"
        parameters = {"type": "object", "properties": {}, "required": []}
        concurrency_safe = True

        def execute(self):
            return "s"

    class _Unsafe(Tool):
        name = "unsafe"
        description = "x"
        parameters = {"type": "object", "properties": {}, "required": []}

        def execute(self):
            return "u"

    agent = Agent(llm=LLM.__new__(LLM), tools=[_Safe(), _Unsafe()])
    calls = [
        _make_tc("safe", "1"),
        _make_tc("safe", "2"),
        _make_tc("unsafe", "3"),
        _make_tc("safe", "4"),
    ]
    segments = agent._segment_tool_calls(calls)
    assert [safe for safe, _ in segments] == [True, False, True]
    assert [[tc.id for tc in group] for _, group in segments] == [["1", "2"], ["3"], ["4"]]


def test_unknown_tool_is_treated_as_unsafe():
    """A tool_call for a tool outside this Agent's set must not be scheduled
    as concurrency-safe just because it's absent from _tool_by_name."""
    agent = Agent(llm=LLM.__new__(LLM), tools=[])
    assert agent._tool_is_safe(_make_tc("bash", "1")) is False


def test_safe_tool_calls_run_concurrently():
    """A run of concurrency-safe calls must overlap in wall-clock time."""
    import threading
    import time
    from corecoder.tools.base import Tool

    start_gate = threading.Barrier(3, timeout=5)
    overlap = threading.Event()
    active = {"count": 0}
    lock = threading.Lock()

    class _SlowSafe(Tool):
        name = "slow_safe"
        description = "x"
        parameters = {"type": "object", "properties": {}, "required": []}
        concurrency_safe = True

        def execute(self):
            with lock:
                active["count"] += 1
                if active["count"] >= 2:
                    overlap.set()
            start_gate.wait()
            time.sleep(0.05)
            with lock:
                active["count"] -= 1
            return "done"

    agent = Agent(llm=LLM.__new__(LLM), tools=[_SlowSafe()])
    calls = [_make_tc("slow_safe", str(i)) for i in range(3)]
    results = agent._exec_tools_parallel(calls)
    assert results == ["done", "done", "done"]
    assert overlap.is_set()


def test_unsafe_tool_calls_run_serially_without_overlap():
    """Consecutive unsafe calls must never be in flight at the same time."""
    import threading
    from corecoder.tools.base import Tool

    active = {"count": 0, "max_seen": 0}
    lock = threading.Lock()

    class _Unsafe(Tool):
        name = "unsafe"
        description = "x"
        parameters = {"type": "object", "properties": {}, "required": []}

        def execute(self):
            with lock:
                active["count"] += 1
                active["max_seen"] = max(active["max_seen"], active["count"])
            with lock:
                active["count"] -= 1
            return "done"

    agent = Agent(llm=LLM.__new__(LLM), tools=[_Unsafe()])
    calls = [_make_tc("unsafe", str(i)) for i in range(4)]
    results = agent._exec_tools_parallel(calls)
    assert results == ["done"] * 4
    assert active["max_seen"] == 1


def test_mixed_batch_preserves_original_result_order():
    """Result order must match the original tool_calls order regardless of
    how many concurrent/serial segments the batch was split into."""
    from corecoder.tools.base import Tool

    class _Safe(Tool):
        name = "safe"
        description = "x"
        parameters = {"type": "object", "properties": {}, "required": []}
        concurrency_safe = True

        def execute(self, tag=""):
            return f"safe:{tag}"

    class _Unsafe(Tool):
        name = "unsafe"
        description = "x"
        parameters = {"type": "object", "properties": {}, "required": []}

        def execute(self, tag=""):
            return f"unsafe:{tag}"

    agent = Agent(llm=LLM.__new__(LLM), tools=[_Safe(), _Unsafe()])

    def tc(name, call_id, tag):
        t = _make_tc(name, call_id)
        t.arguments = {"tag": tag}
        return t

    calls = [
        tc("safe", "1", "a"),
        tc("unsafe", "2", "b"),
        tc("safe", "3", "c"),
        tc("safe", "4", "d"),
    ]
    results = agent._exec_tools_parallel(calls)
    assert results == ["safe:a", "unsafe:b", "safe:c", "safe:d"]
