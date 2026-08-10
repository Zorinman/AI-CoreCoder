"""Configuration schema, construction and redaction contracts."""

import os
import sys
from dataclasses import FrozenInstanceError

import pytest

from corecoder import session as session_module
from corecoder.config import Config, ConfigError, ModelConfig
from corecoder.fallback import ModelChain
from corecoder.session import load_session, save_session


def _clear_config_env(monkeypatch):
    for name in list(os.environ):
        if name.startswith("CORECODER_") or name in {"OPENAI_API_KEY", "OPENAI_BASE_URL"}:
            monkeypatch.delenv(name)


def _set_primary(monkeypatch, **values):
    defaults = {
        "MODEL": "primary-model",
        "API_KEY": "primary-secret",
    }
    defaults.update(values)
    for field, value in defaults.items():
        monkeypatch.setenv(f"CORECODER_PRIMARY_{field}", value)


def test_environment_and_programmatic_construction_are_equivalent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _clear_config_env(monkeypatch)
    _set_primary(
        monkeypatch,
        MODEL="main",
        API_KEY="main-key",
        PROVIDER="openai",
        BASE_URL="https://api.example.test/v1",
        TEMPERATURE="0.2",
        MAX_TOKENS="2048",
    )
    monkeypatch.setenv("CORECODER_FALLBACK_1_MODEL", "backup")
    monkeypatch.setenv("CORECODER_FALLBACK_1_API_KEY", "backup-key")
    monkeypatch.setenv("CORECODER_FALLBACK_1_BACKEND", "litellm")
    monkeypatch.setenv("CORECODER_FALLBACK_1_PROVIDER", "anthropic")
    monkeypatch.setenv("CORECODER_FALLBACK_1_TEMPERATURE", "0.3")
    monkeypatch.setenv("CORECODER_FALLBACK_1_MAX_TOKENS", "1024")

    from_env = Config.from_env()
    direct = Config(
        primary=ModelConfig(
            model="main", api_key="main-key", provider="openai", base_url="https://api.example.test/v1",
            temperature=0.2, max_tokens=2048,
        ),
        fallbacks=(ModelConfig(
            model="backup", api_key="backup-key", backend="litellm", provider="anthropic",
            temperature=0.3, max_tokens=1024,
        ),),
    )

    assert from_env == direct
    with pytest.raises(FrozenInstanceError):
        from_env.primary = direct.primary  # type: ignore[misc]


def test_cli_overrides_primary_before_snapshot_creation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _clear_config_env(monkeypatch)
    _set_primary(monkeypatch, MODEL="from-env", API_KEY="from-env-key")

    config = Config.from_env({"model": "from-cli", "api_key": "from-cli-key"})

    assert config.primary.model == "from-cli"
    assert config.primary.api_key == "from-cli-key"


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("CORECODER_PRIMARY_UNKNOWN", "x", "supported primary-model field"),
        ("CORECODER_FALLBACK_1_UNKNOWN", "x", "supported fallback-model field"),
        ("CORECODER_FALLBACK_2_MODEL", "backup", "consecutive"),
        ("CORECODER_PRIMARY_BACKEND", "custom", "must be one of"),
        ("CORECODER_PRIMARY_TEMPERATURE", "-1", "finite non-negative"),
        ("CORECODER_PRIMARY_MAX_TOKENS", "0", "positive integer"),
    ],
)
def test_invalid_environment_configuration_fails_with_a_specific_error(tmp_path, monkeypatch, name, value, message):
    monkeypatch.chdir(tmp_path)
    _clear_config_env(monkeypatch)
    _set_primary(monkeypatch)
    monkeypatch.setenv(name, value)

    with pytest.raises(ConfigError, match=message):
        Config.from_env()


def test_dotenv_is_loaded_only_from_the_current_directory(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (tmp_path / ".env").write_text(
        "CORECODER_PRIMARY_MODEL=parent\nCORECODER_PRIMARY_API_KEY=parent-key\n", encoding="utf-8"
    )
    (workspace / ".env").write_text(
        "CORECODER_PRIMARY_MODEL=current\nCORECODER_PRIMARY_API_KEY=current-key\n", encoding="utf-8"
    )
    monkeypatch.chdir(workspace)
    _clear_config_env(monkeypatch)

    config = Config.from_env()

    assert config.primary.model == "current"


def test_dotenv_loader_failure_is_explicit_when_a_file_exists(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _clear_config_env(monkeypatch)
    (tmp_path / ".env").write_text("CORECODER_PRIMARY_MODEL=test\n", encoding="utf-8")
    monkeypatch.setitem(sys.modules, "dotenv", None)

    with pytest.raises(ConfigError, match="python-dotenv"):
        Config.from_env()


def test_model_chain_uses_equal_generation_parameters_for_primary_and_fallback(monkeypatch):
    class FakeConfiguredLLM:
        def __init__(self, model, api_key, base_url=None, **kwargs):
            self.model = model
            self.api_key = api_key
            self.base_url = base_url
            self.extra = kwargs
            self.total_prompt_tokens = 0
            self.total_completion_tokens = 0

    import corecoder.llm as llm_module

    monkeypatch.setattr(llm_module, "LLM", FakeConfiguredLLM)
    config = Config(
        primary=ModelConfig(model="main", api_key="main-key", temperature=0.1, max_tokens=100),
        fallbacks=(ModelConfig(model="backup", api_key="backup-key", temperature=0.2, max_tokens=200),),
    )

    chain = ModelChain.from_config(config)

    assert [model.extra for model in chain.models] == [
        {"temperature": 0.1, "max_tokens": 100},
        {"temperature": 0.2, "max_tokens": 200},
    ]


def test_summaries_redact_api_keys_and_url_queries():
    config = Config(
        primary=ModelConfig(
            model="main",
            api_key="super-secret",
            base_url="https://api.example.test/v1?access_token=secret#fragment",
        )
    )
    chain = ModelChain.from_config(config)

    assert "super-secret" not in config.summary()
    assert "access_token" not in config.summary()
    assert "super-secret" not in chain.status()
    assert "access_token" not in chain.status()


def test_runtime_model_switch_does_not_mutate_startup_configuration():
    config = Config(primary=ModelConfig(model="configured", api_key="key"))
    chain = ModelChain.from_config(config)

    chain.set_model("runtime-only")

    assert chain.model == "runtime-only"
    assert config.primary.model == "configured"


def test_session_persists_only_an_explicit_runtime_model_override(tmp_path, monkeypatch):
    monkeypatch.setattr(session_module, "SESSIONS_DIR", tmp_path)
    messages = [{"role": "user", "content": "hello"}]

    session_id = save_session(messages, None, "no_override")

    assert load_session(session_id) == (messages, None)


def test_startup_summary_shows_only_model_names_and_base_urls():
    from corecoder.cli import _startup_model_summary

    config = Config(
        primary=ModelConfig(model="main", api_key="key", base_url="https://main.example.test/v1"),
        fallbacks=(ModelConfig(model="backup", api_key="key", base_url="https://backup.example.test/v1"),),
    )

    assert _startup_model_summary(config) == (
        "Model: [cyan]main[/cyan]  Base: [dim]https://main.example.test/v1[/dim]\n"
        "Fallback 1: [cyan]backup[/cyan]  Base: [dim]https://backup.example.test/v1[/dim]"
    )
