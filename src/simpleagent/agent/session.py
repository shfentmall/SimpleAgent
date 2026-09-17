"""会话状态：消息历史和用量。M3 再加 JSONL 持久化和恢复。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from simpleagent.events import Usage


@dataclass
class Session:
    id: str
    messages: list[dict[str, Any]] = field(default_factory=list)  # 不含 system prompt
    usage: Usage = field(default_factory=Usage)
    requests: int = 0
