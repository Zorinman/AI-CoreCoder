"""Runtime state owned by one Agent's stateful tools."""

from dataclasses import dataclass, field


@dataclass
class ToolRuntimeState:
    cwd: str | None = None
    changed_files: set[str] = field(default_factory=set)
