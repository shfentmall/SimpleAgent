"""工具：抽象、注册表和内置工具。"""

from simpleagent.tools.base import Tool, ToolContext, ToolError, tool
from simpleagent.tools.list_dir import list_dir
from simpleagent.tools.registry import ToolRegistry


def builtin_tools() -> list[Tool]:
    return [list_dir]


__all__ = ["Tool", "ToolContext", "ToolError", "ToolRegistry", "builtin_tools", "tool"]
