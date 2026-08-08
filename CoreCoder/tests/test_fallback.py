"""Tests for fallback routing, per-model costs, and shared budgets."""

import os

import pytest

from corecoder.agent import Agent
from corecoder.budget import BudgetState
from corecoder.config import Config, ConfigError, ModelConfig
from corecoder.fallback import (
    FallbackExhaustedError,
    ModelChain,
)
from corecoder.llm import LLM, LLMResponse
from corecoder.tools.agent import AgentTool


class _FakeLLM:
    def __init__(self, model, failures=0, cost=0.0):
        self.model = model
        self.failures = failures
        self.calls = 0
        self.cost = cost
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.requests = []

    @property
    def estimated_cost(self):
        return self.cost

    @property
    def has_known_cost(self):
        return self.cost is not None

    def chat(self, **kwargs):
        self.calls += 1
        self.requests.append(kwargs.get("messages", []))
        if self.calls <= self.failures:
            raise RuntimeError(f"{self.model} unavailable")
        return LLMResponse(content=self.model)


def _clear_fallback_environment(monkeypatch):
    for name in list(os.environ):
        if name.startswith("CORECODER_") or name in {"OPENAI_API_KEY", "OPENAI_BASE_URL"}:
            monkeypatch.delenv(name)


def test_chain_advances_after_each_level_exhausts_its_retries():
    primary = _FakeLLM("primary", failures=3)
    first_fallback = _FakeLLM("first", failures=3)
    second_fallback = _FakeLLM("second")
    chain = ModelChain(primary, [first_fallback, second_fallback])

    assert chain.chat(messages=[]).content == "second"
    assert [primary.calls, first_fallback.calls, second_fallback.calls] == [3, 3, 1]
    assert chain.active_index == 2
    assert chain.circuit_open is True
    assert "Active: fallback 2" in chain.status()
    assert "Last failure: level 1: RuntimeError" in chain.status()

    # The circuit breaker keeps using the successful fallback.
    assert chain.chat(messages=[]).content == "second"
    assert [primary.calls, first_fallback.calls, second_fallback.calls] == [3, 3, 2]


def test_chain_reports_a_clear_error_when_every_level_fails():
    chain = ModelChain(_FakeLLM("primary", failures=3), [_FakeLLM("fallback", failures=3)])

    with pytest.raises(FallbackExhaustedError, match="fallback chain exhausted"):
        chain.chat(messages=[])


def test_agent_notifies_user_and_records_a_successful_fallback():
    fallback = _FakeLLM("fallback")
    agent = Agent(
        llm=ModelChain(_FakeLLM("primary", failures=3), [fallback]),
        tools=[],
    )

    response = agent.chat("hello")
    agent.chat("which model is active?")

    notice = "当前使用模型已切换为备用模型 fallback。"
    assert response == f"{notice}\n\nfallback"
    assert agent.messages[1] == {"role": "system", "content": notice}
    assert {"role": "system", "content": notice} in fallback.requests[1]


def test_explicit_model_switch_resets_circuit_without_changing_fallbacks():
    primary = _FakeLLM("primary", failures=3)
    fallback = _FakeLLM("fallback")
    chain = ModelChain(primary, [fallback])
    chain.chat(messages=[])
    before = chain.fallbacks

    chain.set_model("new-primary")

    assert chain.active_index == 0
    assert chain.model == "new-primary"
    assert chain.fallbacks == before


def test_config_loads_ordered_fallbacks_from_environment(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _clear_fallback_environment(monkeypatch)
    monkeypatch.setenv("CORECODER_PRIMARY_MODEL", "primary")
    monkeypatch.setenv("CORECODER_PRIMARY_API_KEY", "primary-key")
    monkeypatch.setenv("CORECODER_FALLBACK_1_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("CORECODER_FALLBACK_1_PROVIDER", "deepseek")
    monkeypatch.setenv("CORECODER_FALLBACK_1_API_KEY", "key-one")
    monkeypatch.setenv("CORECODER_FALLBACK_2_MODEL", "anthropic/claude-haiku-4-5")
    monkeypatch.setenv("CORECODER_FALLBACK_2_BACKEND", "litellm")
    monkeypatch.setenv("CORECODER_FALLBACK_2_PROVIDER", "anthropic")
    monkeypatch.setenv("CORECODER_FALLBACK_2_API_KEY", "key-two")

    config = Config.from_env()

    assert [fallback.model for fallback in config.fallbacks] == [
        "deepseek-v4-flash",
        "anthropic/claude-haiku-4-5",
    ]
    assert [fallback.api_key for fallback in config.fallbacks] == ["key-one", "key-two"]


def test_config_rejects_missing_fallback_key(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _clear_fallback_environment(monkeypatch)
    monkeypatch.setenv("CORECODER_PRIMARY_MODEL", "primary")
    monkeypatch.setenv("CORECODER_PRIMARY_API_KEY", "primary-key")
    monkeypatch.setenv("CORECODER_FALLBACK_1_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("CORECODER_FALLBACK_1_API_KEY", "")

    with pytest.raises(ConfigError, match="CORECODER_FALLBACK_1_API_KEY"):
        Config.from_env()


def test_config_rejects_obsolete_primary_environment_variable(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _clear_fallback_environment(monkeypatch)
    monkeypatch.setenv("CORECODER_MODEL", "legacy-model")

    with pytest.raises(ConfigError, match="CORECODER_PRIMARY_MODEL"):
        Config.from_env()


def test_config_loads_budget_from_environment(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _clear_fallback_environment(monkeypatch)
    monkeypatch.setenv("CORECODER_PRIMARY_MODEL", "primary")
    monkeypatch.setenv("CORECODER_PRIMARY_API_KEY", "primary-key")
    monkeypatch.setenv("CORECODER_MAX_COST_USD", "1.25")

    assert Config.from_env().max_cost_usd == 1.25


def test_config_rejects_skipped_fallback_index(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _clear_fallback_environment(monkeypatch)
    monkeypatch.setenv("CORECODER_PRIMARY_MODEL", "primary")
    monkeypatch.setenv("CORECODER_PRIMARY_API_KEY", "primary-key")
    monkeypatch.setenv("CORECODER_FALLBACK_2_MODEL", "gpt-4o-mini")

    with pytest.raises(ConfigError, match="consecutive"):
        Config.from_env()


def test_chain_builds_fallbacks_from_resolved_config():
    primary = _FakeLLM("primary")
    fallback_config = ModelConfig(model="fallback", api_key="key", provider="test")
    chain = ModelChain(primary, [fallback_config], llm_factory=lambda config: _FakeLLM(config.model))

    assert [model.model for model in chain.models] == ["primary", "fallback"]


def test_cost_is_summed_per_model_and_unknown_models_are_skipped():
    llm = LLM.__new__(LLM)
    llm.model = "gpt-5.4"
    llm.total_prompt_tokens = 0
    llm.total_completion_tokens = 0
    llm.token_usage_by_model = {
        "gpt-5.4": {"prompt_tokens": 1_000_000, "completion_tokens": 0},
        "gpt-4o": {"prompt_tokens": 0, "completion_tokens": 1_000_000},
        "unknown": {"prompt_tokens": 9_000_000, "completion_tokens": 9_000_000},
    }

    assert llm.estimated_cost == 2.5 + 10


def test_budget_blocks_known_model_but_fails_open_for_unknown_model():
    known = _FakeLLM("gpt-5.4", cost=2.0)
    limited = ModelChain(known, budget=BudgetState(1.0))
    assert limited.can_start_request() is False

    unknown = _FakeLLM("private-model", cost=None)
    fail_open = ModelChain(unknown, budget=BudgetState(0.0))
    assert fail_open.can_start_request() is True


def test_agent_returns_budget_message_without_starting_a_request():
    primary = _FakeLLM("gpt-5.4", cost=2.0)
    agent = Agent(llm=ModelChain(primary, budget=BudgetState(1.0)), tools=[])

    response = agent.chat("hello")

    assert "hard budget" in response
    assert primary.calls == 0


def test_sub_agent_reuses_its_parent_model_chain(monkeypatch):
    tool = AgentTool()
    parent = Agent(llm=ModelChain(_FakeLLM("gpt-5.4")), tools=[tool])
    seen = []
    original_chat = Agent.chat

    def record_chain(self, *args, **kwargs):
        seen.append(self.model_chain)
        return original_chat(self, *args, **kwargs)

    monkeypatch.setattr(Agent, "chat", record_chain)
    tool.execute("complete the subtask")

    assert seen == [parent.model_chain]
