"""工具输出截断：回给模型的内容不能无限长。

放在注册表里统一做（而不是每个工具自己截断）有两个好处：
- 新增工具自动获得这个能力，不用记得加；
- 完整内容落盘后，模型可以用 read_file 加 offset/limit 自己去看被截掉的部分。

不保留结尾：模型的下一步通常是“再去查”，而不是“看结尾”，
提示里给出落盘路径，比塞进一段没有上下文的尾部更有用。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

DEFAULT_MAX_CHARS = 30_000  # 约 8k token：一次工具结果不该吃掉大半个上下文
DEFAULT_MAX_LINES = 500

# 把完整内容存起来，返回路径；返回 None 表示没存成
Saver = Callable[[str], Path | None]


def clip(text: str, max_chars: int, max_lines: int) -> tuple[str, bool]:
    """同时满足行数和字符数两个上限，返回 (保留的部分, 是否发生了截断)。

    两个上限都要检查：几百行、每行上万字符（压缩过的 json、长日志）只按行数截断
    照样会撑爆上下文。
    """
    kept = "".join(text.splitlines(keepends=True)[:max_lines])[:max_chars]
    return kept, len(kept) < len(text)


def truncate(
    content: str,
    max_chars: int = DEFAULT_MAX_CHARS,
    max_lines: int = DEFAULT_MAX_LINES,
    save: Saver | None = None,
) -> str:
    kept, cut = clip(content, max_chars, max_lines)
    if not cut:
        return content
    total_lines = len(content.splitlines())
    kept_lines = len(kept.splitlines())
    note = (
        f"[输出过长已截断：只显示前 {kept_lines}/{total_lines} 行、"
        f"{len(kept):,}/{len(content):,} 字符。"
    )
    saved = save(content) if save else None
    if not kept.endswith("\n"):
        kept += "\n"  # 按字符截断可能切在行中间，提示另起一行
    if saved:
        note += f"完整内容在 {saved}；要看其他部分就用 read_file 加 offset/limit 读它。]"
    else:
        note += "没有落盘目录（或写入失败），完整内容找不回来了，请缩小范围重新查询。]"
    return kept + note
