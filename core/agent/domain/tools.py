from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ToolResult:
    content: str
    tool_name: str
    success: bool
    error: Optional[str] = None
    metadata: dict = field(default_factory=dict)


class BaseTool(ABC):
    name: str = ''

    @abstractmethod
    def execute(self, **kwargs) -> ToolResult:
        pass

    def _error(self, message: str) -> ToolResult:
        return ToolResult(content=message, tool_name=self.name, success=False, error=message)
