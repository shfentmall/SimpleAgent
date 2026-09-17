"""System prompt 组装。M1 只有基础提示词 + 环境信息；后续加入 AGENTS.md、记忆、skills 列表。"""

from __future__ import annotations

import platform
from datetime import datetime
from pathlib import Path


def build_system_prompt(base: str, cwd: Path | None = None, now: datetime | None = None) -> str:
    now = now or datetime.now()
    cwd = cwd or Path.cwd()
    weekday = "一二三四五六日"[now.weekday()]
    # 只精确到日期：system prompt 在会话内保持不变，才能持续命中前缀缓存
    return (
        f"{base}\n\n"
        "# 环境\n"
        f"- 日期：{now:%Y-%m-%d}（星期{weekday}）\n"
        f"- 系统：{platform.system()} {platform.release()}\n"
        f"- 工作目录：{cwd}"
    )
