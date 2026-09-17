"""目录遍历的公共部分：忽略清单、带剪枝的遍历、在线程里跑可取消的阻塞操作。

glob 和 grep 都要递归遍历目录，list_dir 也要用同一份忽略清单，所以抽到这里共享。
"""

from __future__ import annotations

import asyncio
import os
import threading
from collections.abc import Callable, Iterator
from pathlib import Path

# glob / grep 遍历时整个跳过，list_dir 列出但不展开：体积大、对了解和查找代码没有帮助
IGNORED_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
    }
)
# 完全不显示
HIDDEN_FILES = frozenset({".DS_Store"})


def iter_files(root: Path, stop: threading.Event | None = None) -> Iterator[Path]:
    """递归列出 root 下的文件：跳过忽略目录，不跟进符号链接，无权限的目录跳过。

    用栈而不是递归，目录层级很深时不会栈溢出；
    单个子目录读不了就跳过，不让它拖垮整个搜索。
    stop 被设置时尽快结束（调用方已经不要结果了，比如用户按了 Ctrl+C）。
    """
    if root.is_file():
        yield root
        return
    stack = [root]
    while stack:
        if stop is not None and stop.is_set():
            return
        current = stack.pop()
        try:
            entries = sorted(os.scandir(current), key=lambda entry: entry.name)
        except OSError:  # 无权限或目录已被删除
            continue
        for entry in entries:
            if entry.name in HIDDEN_FILES:
                continue
            if entry.is_dir(follow_symlinks=False):
                if entry.name not in IGNORED_DIRS:
                    stack.append(Path(entry.path))
            elif entry.is_symlink() and not entry.is_file():
                continue  # 指向目录或已经失效的链接：既不跟进，也不当成文件
            else:
                yield Path(entry.path)


async def run_in_thread[T](fn: Callable[[threading.Event], T]) -> T:
    """在线程里执行阻塞的遍历/搜索，不卡住事件循环。

    线程没法从外面强行停止：await 被取消（Ctrl+C）时设置 stop，让 fn 自己尽快退出，
    否则 `grep path=/` 这类操作会在后台一直跑完。
    """
    stop = threading.Event()
    try:
        return await asyncio.to_thread(fn, stop)
    except asyncio.CancelledError:
        stop.set()
        raise


def is_binary(path: Path) -> bool:
    """按前 8KB 里有没有 NUL 字节判断，比看扩展名可靠。

    打不开时（比如没有读权限）直接抛 OSError，让调用方报出真正的原因。
    """
    with path.open("rb") as f:
        return b"\0" in f.read(8192)


def iter_lines(path: Path) -> Iterator[tuple[int, bytes]]:
    """逐行读取，产出 (行号, 去掉行尾换行符的内容)。

    只按 \\n 分行，行号和 `grep -n`、编辑器一致（str.splitlines 还会在 \\f、\\u2028 等处分行）；
    流式读取，大文件也不用整个读进内存。read_file 和 grep 共用，两边的行号才对得上。
    """
    with path.open("rb") as f:
        for number, raw in enumerate(f, start=1):
            yield number, raw.rstrip(b"\r\n")


def relative(path: Path, root: Path) -> str:
    """相对 root 的 POSIX 风格路径；给模型和显示看都用这种形式。"""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()
