"""list_dir：列出目录内容（树形缩进，目录在前，文件带大小）。"""

from __future__ import annotations

import asyncio
import os
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from simpleagent.tools.base import ToolContext, ToolError, tool
from simpleagent.tools.walk import HIDDEN_FILES, IGNORED_DIRS

DESCRIPTION = (
    "列出目录内容，用来了解目录结构。输出是树形缩进："
    "目录名以 / 结尾并排在前面，文件名后面是大小，符号链接显示为 name -> target。"
    ".git、node_modules、.venv 等目录只列出、不展开，标记为 [已忽略]。"
    "条目数超过 limit 时按层截断，优先保留浅层。"
)


class ListDirArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(".", description="要列出的目录，绝对路径或相对工作目录的路径")
    depth: int = Field(2, ge=1, le=5, description="展开层数，1 表示只列直接子项")
    limit: int = Field(200, ge=1, le=1000, description="最多列出的条目数，超过时截断")


@dataclass
class _Node:
    name: str
    kind: Literal["dir", "file", "link"]
    path: Path
    size: int = 0
    target: str = ""  # 符号链接指向的路径
    note: str = ""  # 已忽略 / 无权限 / 无法读取
    children: list[_Node] | None = None  # None 表示没有展开


def format_size(size: int) -> str:
    if size < 1024:
        return f"{size}B"
    value = float(size)
    for unit in "KMGT":
        value /= 1024
        if value < 1024 or unit == "T":
            break
    return f"{value:.1f}{unit}"


def _read_children(path: Path) -> list[_Node]:
    """读取一层目录：目录在前、其余在后，各自按名字排序（不区分大小写）。"""
    nodes = []
    with os.scandir(path) as entries:
        for entry in entries:
            if entry.name in HIDDEN_FILES:
                continue
            entry_path = Path(entry.path)
            # 符号链接不跟进去，避免链接成环时无限递归
            if entry.is_symlink():
                try:
                    target = os.readlink(entry.path)
                except OSError:
                    target = "?"
                nodes.append(_Node(entry.name, "link", entry_path, target=target))
            elif entry.is_dir(follow_symlinks=False):
                nodes.append(_Node(entry.name, "dir", entry_path))
            else:
                try:
                    size = entry.stat(follow_symlinks=False).st_size
                except OSError:
                    size = 0
                nodes.append(_Node(entry.name, "file", entry_path, size=size))
    nodes.sort(key=lambda node: (node.kind != "dir", node.name.lower()))
    return nodes


def _scan(root: Path, depth: int, limit: int) -> tuple[list[_Node], bool]:
    """按层（BFS）扫描：先把浅层列完整，截断时丢掉的总是最深的部分。

    深度优先的话，名额可能全用在第一个大子目录上，后面的同级目录一个都看不到。
    返回 (顶层条目, 是否因为 limit 截断)。
    """
    top = _Node(root.name, "dir", root)
    queue = deque([(top, 1)])
    count = 0
    while queue:
        node, level = queue.popleft()
        try:
            children = _read_children(node.path)
        except OSError as e:
            if node is top:
                raise
            node.note = "无权限" if isinstance(e, PermissionError) else "无法读取"
            continue
        node.children = []
        for child in children:
            if count == limit:
                return top.children or [], True
            node.children.append(child)
            count += 1
            if child.kind != "dir":
                continue
            if child.name in IGNORED_DIRS:
                child.note = "已忽略"
            elif level < depth:
                queue.append((child, level + 1))
    return top.children or [], False


def _render(nodes: list[_Node], indent: int = 0) -> Iterator[str]:
    pad = "  " * indent
    for node in nodes:
        if node.kind == "dir":
            line = f"{pad}{node.name}/"
        elif node.kind == "link":
            line = f"{pad}{node.name} -> {node.target}"
        else:
            line = f"{pad}{node.name} {format_size(node.size)}"
        if node.note:
            line += f" [{node.note}]"
        yield line
        if node.children:
            yield from _render(node.children, indent + 1)


def _list(root: Path, depth: int, limit: int) -> str:
    nodes, truncated = _scan(root, depth, limit)
    lines = [str(root), *_render(nodes)]
    if not nodes:
        lines.append("(空目录)")
    if truncated:
        lines.append(
            f"[已达到 limit={limit}，还有条目没列出；"
            "可以换一个更具体的 path、减小 depth 或调大 limit]"
        )
    return "\n".join(lines)


@tool(name="list_dir", description=DESCRIPTION)
async def list_dir(args: ListDirArgs, ctx: ToolContext) -> str:
    root = ctx.resolve(args.path)
    if not root.exists():
        raise ToolError(f"路径不存在：{root}")
    if not root.is_dir():
        raise ToolError(f"不是目录：{root}")
    try:
        # 大目录的 scandir 会阻塞，放进线程执行，避免卡住事件循环
        return await asyncio.to_thread(_list, root, args.depth, args.limit)
    except PermissionError as e:
        raise ToolError(f"没有权限读取目录：{root}") from e
