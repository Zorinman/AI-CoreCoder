"""Mutable process-wide budget state for model chains."""

from __future__ import annotations

import math


class BudgetState:
    """Mutable process-wide hard budget state shared by agents and children."""

    def __init__(self, limit_usd: float | None = None):
        self.limit_usd = limit_usd

    def set_limit(self, value: float, *, only_increase: bool = False) -> float:
        value = float(value)
        if not math.isfinite(value) or value < 0:
            raise ValueError("Budget limit must be a finite non-negative number.")
        if only_increase and self.limit_usd is not None and value < self.limit_usd:
            raise ValueError("New budget limit must be greater than or equal to the current limit.")
        self.limit_usd = value
        return value
