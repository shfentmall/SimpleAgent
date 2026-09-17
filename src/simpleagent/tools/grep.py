"""grep：按正则搜索文件内容。

为什么自己遍历而不用系统的 grep 命令：
- 能统一跳过 .git / node_modules 和二进制文件，行为不依赖用户机器上有没有 ripgrep；
- 输出格式（相对路径:行号:内容）稳定，方便模型直接拿去做下一步。
找文件用 glob，找内容用 grep；两个工具的遍历逻辑共用 tools/walk.py。
"""

from __future__ import annotations

import re
import threading
from collections.abc import Iterator
from fnmatch import fnmatch
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from simpleagent.tools.base import ToolContext, ToolError, tool
from simpleagent.tools.walk import is_binary, iter_files, iter_lines, relative, run_in_thread

MAX_FILE_BYTES = 2 * 1024 * 1024  # 跳过更大的文件，避免把内存和上下文吃光
MAX_LINE_CHARS = 300  # 单行太长时（压缩后的 json 之类）截断，免得一条结果刷屏

DESCRIPTION = (
    "按正则表达式搜索文件内容，输出“相对路径:行号:该行内容”。"
    "path 可以是目录（递归搜索，自动跳过 .git、node_modules、二进制文件和大于 2MB 的文件）"
    "也可以是单个文件。用 include 限定文件名（如 *.py），用 case_sensitive 控制大小写。"
    "只想找文件名、不关心内容时用 glob。"
)


class GrepArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pattern: str = Field(..., description="正则表达式，如 def run_agent|class Agent")
    path: str = Field(".", description="搜索的文件或目录，默认工作目录")
    include: str | None = Field(None, description="只搜这几类文件，如 *.py；None 表示全部")
    case_sensitive: bool = Field(True, description="是否区分大小写，默认区分")
    limit: int = Field(50, ge=1, le=500, description="最多返回多少条匹配")


def _candidates(root: Path, include: str | None, stop: threading.Event) -> Iterator[Path]:
    # 边遍历边搜：凑够 limit 条就不用再遍历剩下的目录
    for path in iter_files(root, stop):
        if not include or fnmatch(path.name, include):
            yield path


def _search(root: Path, args: GrepArgs, matcher: re.Pattern[str], stop: threading.Event) -> str:
    hits: list[str] = []
    skipped = 0
    base = root if root.is_dir() else root.parent
    for path in _candidates(root, args.include, stop):
        try:
            if path.stat().st_size > MAX_FILE_BYTES or is_binary(path):
                skipped += 1
                continue
            name = relative(path, base)
            # 行号和 read_file 一样只按 \n 计算，模型拿 grep 的行号去 read_file 能对上
            for number, raw in iter_lines(path):
                line = raw.decode("utf-8", errors="replace")
                if matcher.search(line):
                    hits.append(f"{name}:{number}:{line.rstrip()[:MAX_LINE_CHARS]}")
                    if len(hits) == args.limit:
                        break
        except OSError:
            skipped += 1
            continue
        if len(hits) == args.limit:
            break
    head = f"匹配 {len(hits)} 条（pattern：{args.pattern}"
    head += f"，include：{args.include}" if args.include else ""
    head += f"，limit：{args.limit}）"
    lines = [head, *hits]
    if skipped:
        lines.append(f"[已跳过 {skipped} 个二进制、超过 {MAX_FILE_BYTES:,} 字节或读不了的文件]")
    if not hits:
        lines.append("(没有匹配)")
    elif len(hits) == args.limit:
        lines.append("[可能还有更多匹配：调大 limit 或把 pattern 写精确]")
    return "\n".join(lines) + "\n"


@tool(name="grep", description=DESCRIPTION)
async def grep(args: GrepArgs, ctx: ToolContext) -> str:
    root = ctx.resolve(args.path)
    if not root.exists():
        raise ToolError(f"路径不存在：{root}")
    flags = 0 if args.case_sensitive else re.IGNORECASE
    try:
        matcher = re.compile(args.pattern, flags)
    except re.error as e:
        raise ToolError(f"不是合法的正则表达式：{args.pattern}（{e}）") from e
    return await run_in_thread(lambda stop: _search(root, args, matcher, stop))
