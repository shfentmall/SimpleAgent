"""审批桥：把「写操作需要人工确认」翻译成一次 SSE 事件 + 一个被挂起的 Future。

流程（对应设计文档 6.2 的 approval_request / POST /api/approvals/{id}）：
1. 注册表在执行只读=False 的工具前，调用 Approver.request(...)。
2. APIApprover 生成 approval_id，通过总线推一帧 approval_request（客户端弹出审批卡），
   然后 await 一个 Future —— 此时 agent loop 被挂起，不会真的去改文件。
3. 客户端回 POST /api/approvals/{id} {action: allow|deny|always}，HTTP 层在 runner 的
   asyncio 线程上 set_result，Future 解除，request() 返回决策，工具按决策执行或跳过。
4. 等满 timeout 秒还没人答（客户端关了、人走开了）就按拒绝处理，再推一帧 approval_timeout
   让还开着的审批卡作废。不设上限的话，没人管的审批会一直占着会话，这个会话再也发不了消息。

「本次会话始终允许」(always) 记在 Runner 传进来的共享字典里，按 session_id 维度生效，
跨多轮对话都有效。
"""

from __future__ import annotations

import asyncio
import secrets
import time

from simpleagent.permissions import (  # 协议本身在 permissions 里，这里是它的客户端实现
    ApprovalDecision,
    ApprovalRequest,
    Approver,
)
from simpleagent.serve.bus import EventBus
from simpleagent.serve.frames import approval_request_frame, approval_timeout_frame

__all__ = ["APIApprover", "ApprovalDecision", "ApprovalRequest", "Approver", "PendingApprovals"]


def _new_id(prefix: str) -> str:
    return f"{prefix}_{int(time.time() * 1000)}_{secrets.token_hex(2)}"


class PendingApprovals:
    """在 runner 的 asyncio 线程里管理挂起的审批 Future。

    除了 Future，还留下 ApprovalRequest 本身：审批帧发出去就过去了，晚一点连上来的
    客户端（比如刚切到这个会话、或者控制面板）收不到那一帧，得靠这里把它补出来，
    否则任务会一直挂在「运行中」，而界面上没有任何地方可以按批准。
    """

    def __init__(self) -> None:
        self._futures: dict[str, asyncio.Future[ApprovalDecision]] = {}
        self._requests: dict[str, ApprovalRequest] = {}

    def add(self, approval_id: str, req: ApprovalRequest) -> asyncio.Future[ApprovalDecision]:
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._futures[approval_id] = fut
        self._requests[approval_id] = req
        return fut

    def resolve(self, approval_id: str, decision: ApprovalDecision) -> None:
        fut = self._futures.pop(approval_id, None)
        self._requests.pop(approval_id, None)
        if fut is not None and not fut.done():
            fut.set_result(decision)

    def drop_session(self, session_id: str) -> None:
        """这个会话还挂着的审批全部按拒绝了结：一轮都收口了，留下的卡点了也不会有人接。"""
        for aid, req in list(self._requests.items()):
            if req.session_id == session_id:
                self.resolve(aid, ApprovalDecision(allow=False))

    def has(self, approval_id: str) -> bool:
        return approval_id in self._futures

    def pending_ids(self) -> list[str]:
        return list(self._futures)

    def details(self) -> list[dict[str, str]]:
        """待审批的详情。`arguments` 是模型给的原始 JSON 字符串，原样带给前端展示。"""
        out = []
        for aid, req in self._requests.items():
            if aid not in self._futures:
                continue
            out.append(
                {
                    "approval_id": aid,
                    "session_id": req.session_id,
                    "tool_name": req.tool_name,
                    "arguments": req.arguments,
                    "reason": req.reason,
                }
            )
        return out


class APIApprover:
    def __init__(
        self,
        bus: EventBus,
        pending: PendingApprovals,
        always_store: dict[str, set[str]] | None = None,
        *,
        timeout: float | None = None,
    ) -> None:
        self.bus = bus
        self.pending = pending
        # session_id -> 已选「始终允许」的工具名集合（跨轮对话共享）
        self.always_store: dict[str, set[str]] = always_store if always_store is not None else {}
        # 等人答的上限（秒）；None 或 0 = 一直等
        self.timeout = timeout or None

    async def request(self, req: ApprovalRequest) -> ApprovalDecision:
        if req.tool_name in self.always_store.get(req.session_id, set()):
            return ApprovalDecision(allow=True)
        approval_id = _new_id("ap")
        future = self.pending.add(approval_id, req)
        # 推一帧给客户端，然后挂起等决策
        self.bus.publish(
            approval_request_frame(
                req.session_id, approval_id, req.tool_name, req.arguments, req.reason
            )
        )
        try:
            decision = await asyncio.wait_for(future, self.timeout)
        except TimeoutError:
            # wait_for 已经把 Future 取消了，这里只是把登记清掉
            self.pending.resolve(approval_id, ApprovalDecision(allow=False))
            self.bus.publish(approval_timeout_frame(req.session_id, approval_id, self.timeout))
            minutes = (
                f"{self.timeout / 60:g} 分钟" if self.timeout >= 60 else f"{self.timeout:g} 秒"
            )
            return ApprovalDecision(allow=False, note=f"等了 {minutes}没人确认，按拒绝处理")
        except asyncio.CancelledError:
            # 整个 run 被取消时顺手清掉这个挂起的审批，避免泄漏
            self.pending.resolve(approval_id, ApprovalDecision(allow=False))
            raise
        if decision.always:
            self.always_store.setdefault(req.session_id, set()).add(req.tool_name)
        return decision
