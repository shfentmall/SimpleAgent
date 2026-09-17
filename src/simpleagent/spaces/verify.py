"""验证状态的判定：目录指纹 + stale。

设计见 docs/design/client-ui.md 4.3。核心是那句「让验证不会骗人」——
验证通过后工作目录又被改动（无论是 agent 写的还是你自己改的），验证结果就降级为 `stale`，
三处显示（左栏标记 / 头部 chip / 日志 tab）同源，都读这一份。

指纹取 (相对路径, mtime_ns, size) 的哈希，用 `tools/walk.iter_files` 的忽略规则，
和 list_dir / grep 看到的是同一批文件，不会出现「工具认为没改、指纹认为改了」的错位。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from simpleagent.tools.walk import iter_files

# 超大目录只取前 N 个文件：指纹是用于「有没有变」的粗判，不需要全量
MAX_FILES = 5000


def fingerprint(cwd: Path, max_files: int = MAX_FILES) -> str:
    """cwd 下 tracked 文件的指纹。目录不存在或读不动时返回空串（表示无从判断）。"""
    if not cwd.exists():
        return ""
    h = hashlib.sha1()
    count = 0
    for path in sorted(iter_files(cwd)):
        if count >= max_files:
            break
        try:
            st = path.stat()
        except OSError:  # 文件在遍历过程中被删了，跳过即可
            continue
        try:
            rel = path.relative_to(cwd)
        except ValueError:
            continue
        h.update(f"{rel}|{st.st_mtime_ns}|{st.st_size}\n".encode())
        count += 1
    return f"sha1:{h.hexdigest()}"


def changed_since(frozen: str | None, cwd: Path) -> bool:
    """目录相对 frozen 指纹是否变了。没有基准指纹时返回 False——无从判断就不该报 stale。"""
    if not frozen:
        return False
    return fingerprint(cwd) != frozen
