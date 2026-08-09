import sys
from types import ModuleType, SimpleNamespace

from corecoder.llm import LLM, LiteLLM


def _chunk(content=None, tool_calls=None, usage=None):
    choices = []
    if content is not None or tool_calls:
        choices = [SimpleNamespace(delta=SimpleNamespace(content=content, tool_calls=tool_calls))]
    return SimpleNamespace(choices=choices, usage=usage)


def _tool_call(index, call_id=None, name=None, arguments=None):
    function = SimpleNamespace(name=name, arguments=arguments)
    return SimpleNamespace(index=index, id=call_id, function=function)


def _stream():
    return iter([
        _chunk(),
        _chunk("hel"),
        _chunk("lo", tool_calls=[
            _tool_call(1, "call-1", "write_file", '{"path":'),
            _tool_call(0, "call-0", "broken", "{"),
        ]),
        _chunk(tool_calls=[_tool_call(1, arguments='"file.txt"}')]),
        _chunk(usage=SimpleNamespace(prompt_tokens=7, completion_tokens=3)),
    ])


def _assert_stream_contract(chat):
    tokens = []
    response = chat(on_token=tokens.append)
    assert response.content == "hello"
    assert tokens == ["hel", "lo"]
    assert response.prompt_tokens == 7
    assert response.completion_tokens == 3
    assert [(call.id, call.name, call.arguments) for call in response.tool_calls] == [
        ("call-0", "broken", {}),
        ("call-1", "write_file", {"path": "file.txt"}),
    ]


def test_llm_chat_decodes_common_stream_contract():
    llm = LLM.__new__(LLM)
    llm.model = "test"
    llm.extra = {}
    llm.total_prompt_tokens = 0
    llm.total_completion_tokens = 0
    llm._usage_by_model = {}
    llm._call_with_retry = lambda params: _stream()

    _assert_stream_contract(lambda **kwargs: llm.chat(messages=[], **kwargs))
    assert (llm.total_prompt_tokens, llm.total_completion_tokens) == (7, 3)


def test_litellm_chat_decodes_common_stream_contract(monkeypatch):
    fake_litellm = ModuleType("litellm")
    fake_litellm.completion = lambda **params: _stream()
    monkeypatch.setitem(sys.modules, "litellm", fake_litellm)
    llm = LiteLLM(model="test")

    _assert_stream_contract(lambda **kwargs: llm.chat(messages=[], **kwargs))
    assert (llm.total_prompt_tokens, llm.total_completion_tokens) == (7, 3)
