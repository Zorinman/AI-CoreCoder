"""Immutable startup configuration for CoreCoder model chains."""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit, urlunsplit


_MODEL_FIELDS = frozenset({
    "MODEL",
    "API_KEY",
    "BACKEND",
    "PROVIDER",
    "BASE_URL",
    "TEMPERATURE",
    "MAX_TOKENS",
})
_PRIMARY_PREFIX = "CORECODER_PRIMARY_"
_FALLBACK_RE = re.compile(r"^CORECODER_FALLBACK_([0-9]+)_(.+)$")
_DEFAULT_BACKEND = "openai_compatible"
_DEFAULT_PROVIDER = "openai"
_DEFAULT_TEMPERATURE = 0.0
_DEFAULT_MAX_TOKENS = 4096
_DEFAULT_MAX_CONTEXT_TOKENS = 128_000
_BACKENDS = frozenset({"openai_compatible", "litellm"})
_GLOBAL_FIELDS = frozenset({"CORECODER_MAX_CONTEXT_TOKENS", "CORECODER_MAX_COST_USD"})
_LEGACY_VARIABLES = {
    "CORECODER_MODEL": "CORECODER_PRIMARY_MODEL",
    "CORECODER_API_KEY": "CORECODER_PRIMARY_API_KEY",
    "CORECODER_BASE_URL": "CORECODER_PRIMARY_BASE_URL",
    "CORECODER_PROVIDER": "CORECODER_PRIMARY_PROVIDER",
    "CORECODER_MAX_TOKENS": "CORECODER_PRIMARY_MAX_TOKENS",
    "CORECODER_TEMPERATURE": "CORECODER_PRIMARY_TEMPERATURE",
    "CORECODER_MAX_CONTEXT": "CORECODER_MAX_CONTEXT_TOKENS",
    "OPENAI_API_KEY": "CORECODER_PRIMARY_API_KEY",
    "OPENAI_BASE_URL": "CORECODER_PRIMARY_BASE_URL",
}


class ConfigError(ValueError):
    """Raised when ZorinHelper configuration is invalid."""


@dataclass(frozen=True)
class ModelConfig:
    """Complete, resolved configuration for one callable model."""

    model: str
    api_key: str
    backend: str = _DEFAULT_BACKEND
    provider: str = _DEFAULT_PROVIDER
    base_url: str | None = None
    temperature: float = _DEFAULT_TEMPERATURE
    max_tokens: int = _DEFAULT_MAX_TOKENS

    def __post_init__(self) -> None:
        _require_non_empty(self.model, "model")
        _require_non_empty(self.api_key, "api_key")
        _validate_backend(self.backend, "backend")
        _require_non_empty(self.provider, "provider")
        _validate_temperature(self.temperature, "temperature")
        _validate_positive_int(self.max_tokens, "max_tokens")
        if self.base_url is not None and not isinstance(self.base_url, str):
            raise ConfigError("base_url must be a string or None.")

    def summary(self) -> str:
        """Return a display-safe description that never contains the API key."""
        base_url = _redact_base_url(self.base_url) if self.base_url else "default"
        return (
            f"model={self.model}, backend={self.backend}, provider={self.provider}, "
            f"base_url={base_url}, temperature={self.temperature}, max_tokens={self.max_tokens}"
        )


@dataclass(frozen=True)
class Config:
    """Immutable startup snapshot for a primary model, fallbacks and limits."""

    primary: ModelConfig
    fallbacks: tuple[ModelConfig, ...] = ()
    max_context_tokens: int = _DEFAULT_MAX_CONTEXT_TOKENS
    max_cost_usd: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.primary, ModelConfig):
            raise ConfigError("primary must be a ModelConfig.")
        fallbacks = tuple(self.fallbacks)
        if not all(isinstance(fallback, ModelConfig) for fallback in fallbacks):
            raise ConfigError("fallbacks must contain only ModelConfig instances.")
        object.__setattr__(self, "fallbacks", fallbacks)
        _validate_positive_int(self.max_context_tokens, "max_context_tokens")
        if self.max_cost_usd is not None:
            _validate_non_negative_number(self.max_cost_usd, "max_cost_usd")

    @classmethod
    def from_env(cls, primary_overrides: Mapping[str, str | None] | None = None) -> "Config":
        """Resolve the canonical environment schema into an immutable snapshot."""
        _load_dotenv()
        _validate_environment_names()
        overrides = _validate_primary_overrides(primary_overrides)
        primary = _model_from_env(_PRIMARY_PREFIX, overrides)
        fallbacks = _fallbacks_from_env()
        return cls(
            primary=primary,
            fallbacks=fallbacks,
            max_context_tokens=_positive_int_from_env(
                "CORECODER_MAX_CONTEXT_TOKENS", _DEFAULT_MAX_CONTEXT_TOKENS
            ),
            max_cost_usd=_budget_from_env(),
        )

    def summary(self) -> str:
        """Return a deterministic, redacted startup configuration summary."""
        lines = [f"Primary: {self.primary.summary()}"]
        for index, fallback in enumerate(self.fallbacks, start=1):
            lines.append(f"Fallback {index}: {fallback.summary()}")
        lines.append(f"Context limit: {self.max_context_tokens}")
        lines.append(
            f"Budget limit: ${self.max_cost_usd:.6f}" if self.max_cost_usd is not None else "Budget limit: unlimited"
        )
        return "\n".join(lines)


def _load_dotenv() -> None:
    """Load only ``.env`` in the current directory without overriding process env."""
    env_path = Path.cwd() / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv
    except ImportError as exc:  # pragma: no cover - packaging failure
        raise ConfigError("python-dotenv is required to load the current directory .env file.") from exc
    load_dotenv(env_path, override=False)


def _validate_environment_names() -> None:
    """Reject obsolete and misspelled ZorinHelper configuration variables."""
    for name in os.environ:
        replacement = _LEGACY_VARIABLES.get(name)
        if replacement:
            raise ConfigError(f"{name} is obsolete; use {replacement}.")
        if name.startswith(_PRIMARY_PREFIX):
            field = name.removeprefix(_PRIMARY_PREFIX)
            if field not in _MODEL_FIELDS:
                raise ConfigError(f"{name} is not a supported primary-model field.")
            continue
        if name.startswith("CORECODER_FALLBACK_"):
            match = _FALLBACK_RE.match(name)
            if not match:
                raise ConfigError(f"{name} must use CORECODER_FALLBACK_<N>_<FIELD>.")
            index, field = match.groups()
            if int(index) < 1 or index.startswith("0"):
                raise ConfigError(f"{name} must use a fallback index starting at 1 without leading zeroes.")
            if field not in _MODEL_FIELDS:
                raise ConfigError(f"{name} is not a supported fallback-model field.")
            continue
        if name.startswith("CORECODER_") and name not in _GLOBAL_FIELDS:
            raise ConfigError(f"{name} is not a supported ZorinHelper configuration variable.")


def _validate_primary_overrides(
    primary_overrides: Mapping[str, str | None] | None,
) -> dict[str, str]:
    if primary_overrides is None:
        return {}
    allowed = {"model", "api_key", "base_url"}
    overrides: dict[str, str] = {}
    for key, value in primary_overrides.items():
        if key not in allowed:
            raise ConfigError(f"{key} is not a supported primary-model CLI override.")
        if value is not None:
            overrides[key] = value
    return overrides


def _fallbacks_from_env() -> tuple[ModelConfig, ...]:
    indexes = sorted(
        {
            int(match.group(1))
            for name in os.environ
            if (match := _FALLBACK_RE.match(name)) is not None
        }
    )
    if indexes and indexes != list(range(1, indexes[-1] + 1)):
        raise ConfigError("Fallback indexes must be consecutive and start at 1.")
    return tuple(_model_from_env(f"CORECODER_FALLBACK_{index}_") for index in indexes)


def _model_from_env(prefix: str, overrides: Mapping[str, str] | None = None) -> ModelConfig:
    overrides = overrides or {}

    def value(field: str, default: str | None = None) -> str | None:
        override_key = field.lower()
        if override_key in overrides:
            return overrides[override_key]
        return os.getenv(f"{prefix}{field}", default)

    model = _required_env_value(f"{prefix}MODEL", value("MODEL"))
    api_key = _required_env_value(f"{prefix}API_KEY", value("API_KEY"))
    backend = value("BACKEND", _DEFAULT_BACKEND) or ""
    _validate_backend(backend, f"{prefix}BACKEND")
    provider = value("PROVIDER", _DEFAULT_PROVIDER) or ""
    _require_non_empty(provider, f"{prefix}PROVIDER")
    temperature = _temperature_from_value(f"{prefix}TEMPERATURE", value("TEMPERATURE", str(_DEFAULT_TEMPERATURE)))
    max_tokens = _positive_int_from_value(f"{prefix}MAX_TOKENS", value("MAX_TOKENS", str(_DEFAULT_MAX_TOKENS)))
    base_url = value("BASE_URL") or None
    return ModelConfig(
        model=model,
        api_key=api_key,
        backend=backend,
        provider=provider,
        base_url=base_url,
        temperature=temperature,
        max_tokens=max_tokens,
    )


def _required_env_value(name: str, value: str | None) -> str:
    if value is None or not value.strip():
        raise ConfigError(f"{name} is required.")
    return value


def _positive_int_from_env(name: str, default: int) -> int:
    return _positive_int_from_value(name, os.getenv(name, str(default)))


def _positive_int_from_value(name: str, value: str | None) -> int:
    try:
        parsed = int(value or "")
    except ValueError as exc:
        raise ConfigError(f"{name} must be a positive integer.") from exc
    try:
        _validate_positive_int(parsed, name)
    except ConfigError:
        raise
    return parsed


def _temperature_from_value(name: str, value: str | None) -> float:
    try:
        parsed = float(value or "")
    except ValueError as exc:
        raise ConfigError(f"{name} must be a finite non-negative number.") from exc
    _validate_temperature(parsed, name)
    return parsed


def _budget_from_env() -> float | None:
    raw_budget = os.getenv("CORECODER_MAX_COST_USD")
    if raw_budget in (None, ""):
        return None
    try:
        budget = float(raw_budget)
    except ValueError as exc:
        raise ConfigError("CORECODER_MAX_COST_USD must be a finite non-negative number.") from exc
    _validate_non_negative_number(budget, "CORECODER_MAX_COST_USD")
    return budget


def _require_non_empty(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{name} must be a non-empty string.")


def _validate_backend(value: object, name: str) -> None:
    if value not in _BACKENDS:
        choices = ", ".join(sorted(_BACKENDS))
        raise ConfigError(f"{name} must be one of: {choices}.")


def _validate_temperature(value: object, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ConfigError(f"{name} must be a finite non-negative number.")


def _validate_positive_int(value: object, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigError(f"{name} must be a positive integer.")


def _validate_non_negative_number(value: object, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ConfigError(f"{name} must be a finite non-negative number.")


def _redact_base_url(value: str) -> str:
    """Remove URL query and fragment data before displaying a configured endpoint."""
    parts = urlsplit(value)
    if parts.scheme or parts.netloc:
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    return value.split("?", maxsplit=1)[0].split("#", maxsplit=1)[0]
