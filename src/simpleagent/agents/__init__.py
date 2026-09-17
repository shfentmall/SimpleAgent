"""外部 CLI agent 适配层：把 claude-code / opencode 的无头输出接成我们的 Event。"""

from __future__ import annotations

from simpleagent.agents.base import (
    FULL,
    PERMISSION_LABELS,
    PERMISSIONS,
    SAFE,
    CliAdapter,
    CliTurn,
)
from simpleagent.agents.claude import ClaudeAdapter
from simpleagent.agents.opencode import OpenCodeAdapter

ADAPTERS: dict[str, type] = {
    "claude-code": ClaudeAdapter,
    "opencode": OpenCodeAdapter,
}


def adapter_for(executor: str) -> CliAdapter:
    """按执行者名造一个适配器。实例是一次运行一份，可以放心挂状态。"""
    try:
        return ADAPTERS[executor]()
    except KeyError:
        raise ValueError(f"没有 {executor} 的适配器（内置 loop 不走这里）") from None


__all__ = [
    "ADAPTERS",
    "FULL",
    "PERMISSION_LABELS",
    "PERMISSIONS",
    "SAFE",
    "CliAdapter",
    "CliTurn",
    "ClaudeAdapter",
    "OpenCodeAdapter",
    "adapter_for",
]
