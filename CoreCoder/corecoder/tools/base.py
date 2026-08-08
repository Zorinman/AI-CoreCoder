"""Base class for all tools."""

from abc import ABC, abstractmethod


class Tool(ABC):
    """Minimal tool interface. Subclass this to add new capabilities."""

    name: str
    description: str
    parameters: dict  # JSON Schema for the function args

    # Default-deny: a tool is only run concurrently with other tools when it
    # explicitly opts in. Tools with side effects or shared mutable state
    # (bash, agent, ...) must stay False so a forgotten declaration fails
    # safe (serial), not unsafe (concurrent).
    concurrency_safe: bool = False

    @abstractmethod
    def execute(self, **kwargs) -> str:
        """Run the tool and return a text result."""
        ...

    def schema(self) -> dict:
        """OpenAI function-calling schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
