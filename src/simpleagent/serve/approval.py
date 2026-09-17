"""审批桥：把「写操作需要人工确认」翻译成一次 SSE 事件 + 一个被挂起的 Future。

流程（对应设计文档 6.2 的 approval_request / POST /api/approvals/{id}）：
1. 注册表在执行只读=False 的工具前，调用 Approver.request(...)。
2. APIApprover 生成 approval_id，通过总线推一帧 approval_request（客户端弹出审批卡），
   然后 await 一个 Future —— 此时 agent loop 被挂起，不会真的去改文件。
3. 客户端回 POST /api/approvals/{id} {action: allow|deny|always}，HTTP 层在 runner 的
   asyncio 线程上 set_result，Future 解除，request() 返回决策，工具按决策执行或跳过。
4. 客户端不在线 / 超时则按无人值守策略拒绝（M3 行为；这里直接返回拒绝）。

「本次会话始终允许」(always) 记在 Runner 传进来的共享字典里，按 session_id 维度生效，
跨多轮对话都有效。
"""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass
from typing import Protocol

from simpleagent.serve.bus import EventBus
from simpleagent.serve.frames import approval_request_frame


@dataclass
class ApprovalDecision:
    allow: bool
    always: bool = False


class Approver(Protocol):
    async def request(
        self, *, session_id: str, tool_name: str, arguments: str
    ) -> ApprovalDecision: ...


def _new_id(prefix: str) -> str:
    return f"{prefix}_{int(time.time() * 1000)}_{secrets.token_hex(2)}"


class PendingApprovals:
    """在 runner 的 asyncio 线程里管理挂起的审批 Future。"""

    def __init__(self) -> None:
        self._futures: dict[str, asyncio.Future[ApprovalDecision]] = {}

    def add(self, approval_id: str) -> asyncio.Future[ApprovalDecision]:
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._futures[approval_id] = fut
        return fut

    def resolve(self, approval_id: str, decision: ApprovalDecision) -> None:
        fut = self._futures.pop(approval_id, None)
        if fut is not None and not fut.done():
            fut.set_result(decision)

    def pending_ids(self) -> list[str]:
        return list(self._futures)


class APIApprover:
    def __init__(
        self,
        bus: EventBus,
        pending: PendingApprovals,
        always_store: dict[str, set[str]] | None = None,
    ) -> None:
        self.bus = bus
        self.pending = pending
        # session_id -> 已选「始终允许」的工具名集合（跨轮对话共享）
        self.always_store: dict[str, set[str]] = always_store if always_store is not None else {}

    async def request(self, *, session_id: str, tool_name: str, arguments: str) -> ApprovalDecision:
        if tool_name in self.always_store.get(session_id, set()):
            return ApprovalDecision(allow=True)
        approval_id = _new_id("ap")
        future = self.pending.add(approval_id)
        # 推一帧给客户端，然后挂起等决策
        self.bus.publish(approval_request_frame(session_id, approval_id, tool_name, arguments))
        try:
            decision = await future
        except asyncio.CancelledError:
            # 整个 run 被取消时顺手清掉这个挂起的审批，避免泄漏
            self.pending.resolve(approval_id, ApprovalDecision(allow=False))
            raise
        if decision.always:
            self.always_store.setdefault(session_id, set()).add(tool_name)
        return decision
