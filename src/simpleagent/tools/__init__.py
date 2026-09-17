"""工具：抽象、注册表和内置工具。"""

from simpleagent.tools.base import Tool, ToolContext, ToolError, tool
from simpleagent.tools.bash import bash
from simpleagent.tools.edit_file import edit_file
from simpleagent.tools.glob import glob
from simpleagent.tools.grep import grep
from simpleagent.tools.list_dir import list_dir
from simpleagent.tools.read_file import read_file
from simpleagent.tools.registry import ToolRegistry
from simpleagent.tools.write_file import write_file


def builtin_tools() -> list[Tool]:
    # 顺序即请求里 tools 数组的顺序：会话内保持不变，请求前缀才能命中缓存
    return [list_dir, read_file, write_file, edit_file, glob, grep, bash]


__all__ = ["Tool", "ToolContext", "ToolError", "ToolRegistry", "builtin_tools", "tool"]
