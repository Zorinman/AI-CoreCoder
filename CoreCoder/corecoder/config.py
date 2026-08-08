"""Configuration - env vars and defaults."""

import os
import math
import re
from dataclasses import dataclass, field
from pathlib import Path


_FALLBACK_PREFIX = "CORECODER_FALLBACK_"
_FALLBACK_INDEX = re.compile(r"^CORECODER_FALLBACK_(\d+)_")


class ConfigError(ValueError):
    """Raised when environment-backed CoreCoder configuration is invalid."""


@dataclass(frozen=True)
class ModelConfig:
    """Fully resolved connection settings for one model level."""

    model: str
    api_key: str
    base_url: str | None = None
    provider: str = "openai"


def _load_dotenv():
    """Load .env from cwd, walking up to home dir. No-op if python-dotenv missing."""
    try:
        from dotenv import load_dotenv
        # search cwd first, then parent dirs up to ~
        env_path = Path(".env")
        if not env_path.exists():
            cur = Path.cwd()
            home = Path.home()
            while cur != home and cur != cur.parent:
                candidate = cur / ".env"
                if candidate.exists():
                    env_path = candidate
                    break
                cur = cur.parent
        load_dotenv(env_path, override=False)
    except ImportError:
        pass  # python-dotenv not installed, silently skip


@dataclass
class Config:
    model: str = "gpt-5.5"
    api_key: str = ""
    base_url: str | None = None
    max_tokens: int = 4096
    temperature: float = 0.0
    max_context_tokens: int = 128_000
    provider: str = "openai"
    max_cost_usd: float | None = None
    fallbacks: list[ModelConfig] = field(default_factory=list)

    @classmethod
    def from_env(cls) -> "Config":
        # load .env if present (won't override existing env vars)
        _load_dotenv()
        # pick up common env vars automatically
        api_key = (
            os.getenv("CORECODER_API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or ""
        )
        raw_budget = os.getenv("CORECODER_MAX_COST_USD")
        try:
            max_cost_usd = float(raw_budget) if raw_budget not in (None, "") else None
            if max_cost_usd is not None and (not math.isfinite(max_cost_usd) or max_cost_usd < 0):
                max_cost_usd = None
        except ValueError:
            max_cost_usd = None
        return cls(
            model=os.getenv("CORECODER_MODEL", "gpt-5.5"),
            api_key=api_key,
            base_url=os.getenv("OPENAI_BASE_URL") or os.getenv("CORECODER_BASE_URL"),
            max_tokens=int(os.getenv("CORECODER_MAX_TOKENS", "4096")),
            temperature=float(os.getenv("CORECODER_TEMPERATURE", "0")),
            max_context_tokens=int(os.getenv("CORECODER_MAX_CONTEXT", "128000")),
            provider=os.getenv("CORECODER_PROVIDER", "openai"),
            max_cost_usd=max_cost_usd,
            fallbacks=_fallbacks_from_env(),
        )


def _fallbacks_from_env() -> list[ModelConfig]:
    """Read ordered fallback levels from ``CORECODER_FALLBACK_<N>_*`` vars."""
    indexes = sorted(
        {int(match.group(1)) for name in os.environ if (match := _FALLBACK_INDEX.match(name))}
    )
    if indexes and indexes != list(range(1, indexes[-1] + 1)):
        raise ConfigError("Fallback indexes must be consecutive and start at 1.")

    fallbacks: list[ModelConfig] = []
    for index in indexes:
        prefix = f"{_FALLBACK_PREFIX}{index}_"
        model = os.getenv(f"{prefix}MODEL")
        if not model:
            raise ConfigError(f"{prefix}MODEL is required for fallback level {index}.")
        api_key = os.getenv(f"{prefix}API_KEY")
        if not api_key:
            raise ConfigError(f"{prefix}API_KEY is required for fallback level {index}.")
        fallbacks.append(
            ModelConfig(
                model=model,
                api_key=api_key,
                base_url=os.getenv(f"{prefix}BASE_URL"),
                provider=os.getenv(f"{prefix}PROVIDER", "openai"),
            )
        )
    return fallbacks
