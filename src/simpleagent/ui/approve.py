"""终端审批器：把一次审批请求变成终端里的一行提问。

唯一的坑是**不能直接调 input()**。approver 是在 agent loop 的协程里被 await 的，
同步阻塞的 input() 会把整个事件循环卡死——已经连着的 SSE 客户端、并行跑到一半的
其他工具任务全都跟着停摆，表现出来的症状是「进了提问就再也不动」。
所以读输入一律走 asyncio.to_thread。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TextIO

from simpleagent.permissions import ApprovalDecision, ApprovalRequest

PROMPT = "执行这一步？[y] 允许  [a] 本次会话都允许  [其他键] 拒绝 → "
ARGUMENT_LIMIT = 300  # 参数太长只显示前 300 字符，完整的在 trace 里


def _clip(text: str, limit: int = ARGUMENT_LIMIT) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[:limit] + "…"


class ConsoleApprover:
    """REPL 用的审批器。选了 a 的工具在整个会话里不再问。"""

    def __init__(
        self,
        *,
        input_fn: Callable[[str], str] = input,
        out: TextIO | None = None,
        prompt: str = PROMPT,
    ) -> None:
        self.input_fn = input_fn
        self.out = out
        self.prompt = prompt
        self.granted: set[str] = set()

    async def request(self, req: ApprovalRequest) -> ApprovalDecision:
        if req.tool_name in self.granted:
            return ApprovalDecision(allow=True)
        self._write(f"\n⚠ 需要确认：{req.tool_name} {_clip(req.arguments)}\n")
        if req.reason:
            self._write(f"  {req.reason}\n")
        try:
            # 提示语交给 input_fn：和 REPL 里 "> " 的用法一致，测试能整个接管输入
            answer = (await asyncio.to_thread(self.input_fn, self.prompt)).strip().lower()
        except (EOFError, KeyboardInterrupt):
            # 输入被关掉（管道跑的）或用户按了 Ctrl+C：都按拒绝处理，不让 loop 卡住
            answer = ""
            self._write("\n")
        if answer in ("a", "always"):
            self.granted.add(req.tool_name)
            return ApprovalDecision(allow=True, always=True)
        if answer in ("y", "yes"):
            return ApprovalDecision(allow=True)
        self._write("  已拒绝，让模型另想办法。\n")
        return ApprovalDecision(allow=False)

    def _write(self, text: str) -> None:
        if self.out is None:
            return
        self.out.write(text)
        self.out.flush()
