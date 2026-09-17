"""Agent loop：请求模型 → 执行模型要求的工具 → 把结果交回模型，直到模型不再调用工具。

要不要调用工具、调用哪个、参数是什么，都由模型决定；这里只负责执行和回传。
状态都在 Session 里，前端（REPL / headless / daemon）只消费 run() 产出的事件。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

from simpleagent.agent.session import Session
from simpleagent.events import Event, MaxStepsReached, MessageDone, TextDelta, ToolCallStart
from simpleagent.llm.client import LLM
from simpleagent.tools import ToolContext, ToolRegistry

INTERRUPTED_RESULT = "错误：执行被中断，没有结果"


class Agent:
    def __init__(
        self,
        llm: LLM,
        tools: ToolRegistry,
        system_prompt: str,
        cwd: Path,
        max_steps: int = 20,
    ):
        self.llm = llm
        self.tools = tools
        self.system_prompt = system_prompt
        self.cwd = cwd
        self.max_steps = max_steps

    async def run(self, session: Session, user_input: str) -> AsyncIterator[Event]:
        """处理一条用户输入。中断或出错时先把历史修成合法状态，再把异常原样抛出。"""
        turn_start = len(session.messages)
        session.messages.append({"role": "user", "content": user_input})
        ctx = ToolContext(cwd=self.cwd)
        partial: list[str] = []  # 本次请求已经输出的正文，中断时保存
        try:
            for _ in range(self.max_steps):
                request = [{"role": "system", "content": self.system_prompt}, *session.messages]
                done: MessageDone | None = None
                async for event in self.llm.stream(request, tools=self.tools.schemas()):
                    if isinstance(event, TextDelta):
                        partial.append(event.text)
                    elif isinstance(event, MessageDone):
                        # 先记进历史再往外发：消费方在这里停下，历史也是完整的
                        done = event
                        session.messages.append(event.message)
                        partial.clear()
                        session.requests += 1
                        if event.usage:
                            session.usage += event.usage
                    yield event
                assert done is not None, "stream 结束时必须产出 MessageDone"

                tool_calls = done.message.get("tool_calls")
                if not tool_calls:
                    return  # 模型没有调用工具，这一轮结束
                for call in tool_calls:
                    function = call.get("function") or {}
                    yield ToolCallStart(
                        call.get("id") or "",
                        function.get("name") or "",
                        function.get("arguments") or "",
                    )
                # 同一条消息里的多个调用并行执行；结果按 tool_calls 的顺序回传
                results = await asyncio.gather(
                    *(self.tools.execute(call, ctx) for call in tool_calls)
                )
                session.messages.extend(result.as_message() for result in results)
                for result in results:
                    yield result
            yield MaxStepsReached(self.max_steps)
        except BaseException:  # Ctrl+C（CancelledError）、API 错误、消费方提前退出
            self._repair(session, turn_start, "".join(partial))
            raise

    @staticmethod
    def _repair(session: Session, turn_start: int, partial_text: str) -> None:
        """把本轮历史修成合法状态，否则下一次请求会被 API 拒绝。"""
        messages = session.messages
        turn = messages[turn_start:]
        answered = {m.get("tool_call_id") for m in turn if m.get("role") == "tool"}
        # 1. 有 tool_call 还没有结果（工具执行时被中断）：补一条结果
        for message in turn:
            for call in message.get("tool_calls") or []:
                if call.get("id") not in answered:
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.get("id"),
                            "content": INTERRUPTED_RESULT,
                        }
                    )
        # 2. 模型已经输出了一部分正文：保留下来，下一轮能接上
        if partial_text:
            messages.append({"role": "assistant", "content": partial_text})
        # 3. 这一轮什么都没留下：撤回用户消息
        if len(messages) == turn_start + 1:
            messages.pop()
