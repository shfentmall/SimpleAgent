"""serve：本地 API（HTTP + SSE）的服务端实现，供桌面客户端连接。

分层：
- bus.py       事件总线（按 session 分发、seq 自增、Last-Event-ID 重放）
- frames.py    事件 → 帧 的序列化，以及服务端补充帧（status/error/approval_request/verification）
- approval.py  审批器的异步协议 + 挂起/恢复桥（APIApprover）
- runner.py    后台 asyncio 线程：按空间构造 Agent、跑 run()、把事件发到总线、落盘
- app.py       http.server 路由 + SSE handler，以及 `sa serve` 入口

依赖：仅标准库 + 项目内模块；无新第三方依赖（符合设计文档 A 方案）。
"""

from simpleagent.serve.approval import APIApprover, ApprovalDecision, Approver, PendingApprovals
from simpleagent.serve.bus import EventBus, Frame
from simpleagent.serve.runner import Runner

__all__ = [
    "APIApprover",
    "ApprovalDecision",
    "Approver",
    "EventBus",
    "Frame",
    "PendingApprovals",
    "Runner",
]
