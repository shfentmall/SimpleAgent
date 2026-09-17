"""read_file：读文本文件，输出带行号。

行号是为了让模型（和人）能直接用 offset/limit 指位置，也方便 edit_file 之后对照。

read_file 自己分页，不走注册表的统一截断（truncate_output=False）：
统一截断只会留开头、把完整内容另存一份，结尾的“用 offset=N 继续”提示也会被截掉；
而读文件本来就能按 offset 续读，按行数和字符数两个预算停下、告诉模型从哪继续更有用。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from simpleagent.tools.base import ToolContext, ToolError, tool
from simpleagent.tools.output import DEFAULT_MAX_CHARS
from simpleagent.tools.walk import is_binary, iter_lines

MAX_OUTPUT_CHARS = DEFAULT_MAX_CHARS  # 一次最多返回的字符数（约 8k token），超出就停下提示续读
MAX_LINE_CHARS = 2000  # 单行超过就截断：压缩过的 js / json 一行可能有几 MB

DESCRIPTION = (
    "读取文本文件内容，输出带行号（格式：行号<Tab>内容）。"
    "一次最多返回 limit 行、约 3 万字符，没读完时结尾会提示用 offset 继续读；"
    "超过 2000 字符的单行会被截断。二进制文件会被拒绝。"
)


class ReadFileArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(..., description="要读的文件，绝对路径或相对工作目录的路径")
    offset: int = Field(1, ge=1, description="从第几行开始读，1 起算")
    limit: int = Field(2000, ge=1, le=5000, description="最多读多少行")


def _read(path: Path, offset: int, limit: int) -> str:
    if is_binary(path):
        raise ToolError(f"看起来是二进制文件：{path}")

    selected: list[tuple[int, str]] = []
    used = 0
    budget_hit = False
    total = 0
    # 流式逐行读：只解码要返回的行，其余行只计数，大文件也不用整个读进内存
    for number, raw in iter_lines(path):
        total = number
        if number < offset or number >= offset + limit or budget_hit:
            continue
        try:
            line = raw.decode("utf-8")
        except UnicodeDecodeError as e:
            raise ToolError(f"第 {number} 行不是 UTF-8 文本，读不了：{path}（{e.reason}）") from e
        if len(line) > MAX_LINE_CHARS:
            line = f"{line[:MAX_LINE_CHARS]}…[本行共 {len(line):,} 字符，已截断]"
        # 至少返回一行，否则模型永远前进不了
        if selected and used + len(line) > MAX_OUTPUT_CHARS:
            budget_hit = True
            continue
        selected.append((number, line))
        used += len(line)

    if offset > max(total, 1):
        raise ToolError(f"文件只有 {total} 行，offset={offset} 超出范围")
    header = f"{path}（共 {total} 行）\n"
    if not selected:
        return header + "(空文件)\n"
    end = selected[-1][0]
    width = len(str(end))
    body = "".join(f"{number:>{width}}\t{line}\n" for number, line in selected)
    footer = ""
    if end < total:
        reason = f"（本次输出已达约 {MAX_OUTPUT_CHARS:,} 字符上限）" if budget_hit else ""
        footer = f"[还有 {total - end} 行没读{reason}；用 offset={end + 1} 继续]\n"
    return header + body + footer


@tool(name="read_file", description=DESCRIPTION, truncate_output=False)
async def read_file(args: ReadFileArgs, ctx: ToolContext) -> str:
    path = ctx.resolve(args.path)
    if not path.exists():
        raise ToolError(f"文件不存在：{path}")
    if path.is_dir():
        raise ToolError(f"是一个目录：{path}；要看目录内容用 list_dir")
    try:
        return await asyncio.to_thread(_read, path, args.offset, args.limit)
    except PermissionError as e:
        raise ToolError(f"没有权限读取：{path}") from e
