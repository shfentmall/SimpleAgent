"""glob：按通配符找文件。

只支持 *、?、** 三种通配符，自己转正则：
一是 stdlib 的 glob 模块没法在遍历时跳过 .git / node_modules，遇到大仓库会卡住；
二是统一的规则（**/ 匹配任意层目录）比 shell 的 glob 行为好解释给模型听。
"""

from __future__ import annotations

import re
import threading
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from simpleagent.tools.base import ToolContext, ToolError, tool
from simpleagent.tools.walk import iter_files, relative, run_in_thread

DESCRIPTION = (
    "按通配符查找文件，支持 *（一层内任意字符）、?（单个字符）、**（任意层目录）。"
    "例如 **/*.py 是所有 Python 文件，src/**/*.ts 只找 src 下。"
    ".git、node_modules、.venv 等目录会自动跳过。结果按路径排序，只列文件不列目录。"
)


class GlobArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pattern: str = Field(..., description="通配符模式，如 **/*.py")
    path: str = Field(".", description="搜索起点，绝对路径或相对工作目录的路径，默认工作目录")
    limit: int = Field(200, ge=1, le=2000, description="最多列出多少个文件")


def compile_pattern(pattern: str) -> re.Pattern[str]:
    """通配符 → 正则：`**/` 匹配 0 层或多层目录，`*` 和 `?` 不跨目录。"""
    normalized = pattern.removeprefix("./")
    parts: list[str] = []
    index = 0
    while index < len(normalized):
        if normalized.startswith("**/", index):
            parts.append("(?:.*/)?")
            index += 3
        elif normalized.startswith("**", index):
            parts.append(".*")
            index += 2
        elif normalized[index] == "*":
            parts.append("[^/]*")
            index += 1
        elif normalized[index] == "?":
            parts.append("[^/]")
            index += 1
        else:
            parts.append(re.escape(normalized[index]))
            index += 1
    return re.compile("".join(parts) + r"\Z")


def _glob(root: Path, pattern: str, limit: int, stop: threading.Event) -> str:
    matcher = compile_pattern(pattern)
    names = (relative(path, root) for path in iter_files(root, stop))
    matches = sorted(name for name in names if matcher.match(name))
    shown = matches[:limit]
    lines = [f"匹配 {len(matches)} 个文件（pattern：{pattern}，起点 {root}）", *shown]
    if len(matches) > limit:
        lines.append(f"[还有 {len(matches) - limit} 个没列出：调大 limit 或把 pattern 写精确]")
    if not matches:
        lines.append("(没有匹配的文件)")
    return "\n".join(lines) + "\n"


@tool(name="glob", description=DESCRIPTION)
async def glob(args: GlobArgs, ctx: ToolContext) -> str:
    root = ctx.resolve(args.path)
    if not root.exists():
        raise ToolError(f"路径不存在：{root}")
    return await run_in_thread(lambda stop: _glob(root, args.pattern, args.limit, stop))
