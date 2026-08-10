"""Model fallback chains and process-wide budget accounting.

The chain deliberately wraps the existing LLM implementations.  Their
provider-specific retry behaviour remains in :mod:`corecoder.llm`; this
module only decides which model should receive the next request.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any

from .budget import BudgetState
from .config import ModelConfig

if TYPE_CHECKING:
    from .config import Config


class FallbackError(RuntimeError):
    """Base exception for fallback-chain failures."""


class FallbackExhaustedError(FallbackError):
    """Raised when every model in the chain has failed."""


class BudgetExceededError(FallbackError):
    """Raised before a new LLM request when the hard budget is exhausted."""


class ModelChain:
    """Route requests through a primary model and ordered fallback levels."""

    def __init__(
        self,
        primary: Any = None,
        fallbacks: Sequence[Any] | None = None,
        *,
        llm: Any = None,
        models: Sequence[Any] | None = None,
        budget: BudgetState | None = None,
        llm_factory: Callable[..., Any] | None = None,
    ):
        self._factory = llm_factory
        primary = primary if primary is not None else llm
        if primary is None and models:
            primary, models = models[0], models[1:]
        if primary is None:
            raise ValueError("ModelChain requires a primary model.")
        raw_fallbacks = fallbacks
        if raw_fallbacks is None:
            raw_fallbacks = models
        if raw_fallbacks is None:
            raw_fallbacks = []

        items = [primary, *raw_fallbacks]
        self._configs = [item if isinstance(item, ModelConfig) else None for item in items]
        self._models = [self._make_model(item) for item in items]
        self._active_index = 0
        self.budget = budget if budget is not None else BudgetState()
        self._last_failure: str | None = None

    @classmethod
    def from_config(
        cls,
        config: Config,
        *,
        budget: BudgetState | None = None,
        llm_factory: Callable[[ModelConfig], Any] | None = None,
    ) -> ModelChain:
        """Create every production model through one resolved configuration path."""
        return cls(
            primary=config.primary,
            fallbacks=config.fallbacks,
            budget=budget if budget is not None else BudgetState(config.max_cost_usd),
            llm_factory=llm_factory,
        )

    def _make_model(self, item: Any) -> Any:
        if not isinstance(item, ModelConfig):
            return item
        cfg = item
        if self._factory:
            return self._factory(cfg)
        from .llm import LLM, LiteLLM

        cls = LiteLLM if cfg.backend == "litellm" else LLM
        return cls(
            model=cfg.model,
            api_key=cfg.api_key,
            base_url=cfg.base_url,
            temperature=cfg.temperature,
            max_tokens=cfg.max_tokens,
        )

    @property
    def models(self) -> list[Any]:
        return list(self._models)

    @property
    def fallbacks(self) -> list[Any]:
        return list(self._models[1:])

    @property
    def current(self) -> Any:
        return self._models[self._active_index]

    @property
    def active_index(self) -> int:
        return self._active_index

    @property
    def circuit_open(self) -> bool:
        return self._active_index > 0

    def status(self) -> str:
        """Return a display-safe summary of the configured chain and runtime state."""
        lines: list[str] = []
        for index, (model, config) in enumerate(zip(self._models, self._configs)):
            label = "Primary" if index == 0 else f"Fallback {index}"
            if config is not None:
                detail = config.summary()
            else:
                detail = f"model={getattr(model, 'model', '')}"
            lines.append(f"{label}: {detail}")
        active = "primary" if self._active_index == 0 else f"fallback {self._active_index}"
        lines.append(f"Active: {active}")
        if self._last_failure:
            lines.append(f"Last failure: {self._last_failure}")
        return "\n".join(lines)

    @property
    def model(self) -> str:
        return getattr(self.current, "model", "")

    @model.setter
    def model(self, value: str) -> None:
        self.set_model(value)

    @property
    def total_prompt_tokens(self) -> int:
        return sum(getattr(m, "total_prompt_tokens", 0) for m in self._models)

    @property
    def total_completion_tokens(self) -> int:
        return sum(getattr(m, "total_completion_tokens", 0) for m in self._models)

    @property
    def usage_by_model(self) -> dict[str, dict[str, int]]:
        result: dict[str, dict[str, int]] = {}
        for model in self._models:
            usage = getattr(model, "usage_by_model", None)
            if isinstance(usage, dict):
                levels = usage.items()
            else:
                levels = [(getattr(model, "model", ""), {
                    "prompt_tokens": getattr(model, "total_prompt_tokens", 0),
                    "completion_tokens": getattr(model, "total_completion_tokens", 0),
                })]
            for name, counts in levels:
                entry = result.setdefault(name, {"prompt_tokens": 0, "completion_tokens": 0})
                entry["prompt_tokens"] += counts.get("prompt_tokens", 0)
                entry["completion_tokens"] += counts.get("completion_tokens", 0)
        return result

    @property
    def estimated_cost(self) -> float | None:
        costs = [getattr(m, "estimated_cost", None) for m in self._models]
        known = [cost for cost in costs if cost is not None]
        return sum(known) if known else None

    def _current_model_cost_known(self) -> bool:
        return bool(getattr(self.current, "has_known_cost", False))

    def can_start_request(self) -> bool:
        """Return False only when a known-cost current model exceeds the cap."""
        if self.budget.limit_usd is None or not self._current_model_cost_known():
            return True
        cost = self.estimated_cost
        return cost is None or cost <= self.budget.limit_usd

    def check_budget(self) -> None:
        if not self.can_start_request():
            raise BudgetExceededError(
                f"Estimated cost ${self.estimated_cost:.6f} exceeds the hard budget "
                f"of ${self.budget.limit_usd:.6f}. Use /budget <amount> to increase it."
            )

    def set_budget_limit(self, value: float, *, only_increase: bool = False) -> float:
        return self.budget.set_limit(value, only_increase=only_increase)

    def chat(self, *args, **kwargs):
        self.check_budget()
        errors: list[Exception] = []
        for index in range(self._active_index, len(self._models)):
            model = self._models[index]
            try:
                result = self._invoke(model, *args, **kwargs)
                self._active_index = index
                return result
            except Exception as exc:  # noqa: BLE001 - model implementations expose heterogeneous errors.
                self._last_failure = f"level {index}: {type(exc).__name__}"
                errors.append(exc)
        detail = "; ".join(f"{type(e).__name__}: {e}" for e in errors)
        raise FallbackExhaustedError(
            f"All {len(self._models)} model levels failed; fallback chain exhausted. {detail}"
        ) from (errors[-1] if errors else None)

    @staticmethod
    def _invoke(model: Any, *args, **kwargs):
        """Call a real LLM once (it owns retries), retry simple test doubles."""
        if hasattr(model, "_call_with_retry"):
            return model.chat(*args, **kwargs)
        last = None
        for _ in range(3):
            try:
                return model.chat(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - test doubles and providers may raise arbitrary errors.
                last = exc
        if last is not None:
            raise last
        raise RuntimeError("LLM call did not return or raise.")

    def set_model(self, model_name: str, **kwargs) -> Any:
        """Explicitly select a new primary model and reset the circuit breaker."""
        primary = self._models[0]
        self._active_index = 0
        if hasattr(primary, "model"):
            primary.model = model_name
        for key, value in kwargs.items():
            if value is not None and hasattr(primary, key):
                setattr(primary, key, value)
        return primary

    switch_model = set_model

    def reset_circuit_breaker(self) -> None:
        self._active_index = 0

    reset = reset_circuit_breaker
    request = chat
    call = chat
