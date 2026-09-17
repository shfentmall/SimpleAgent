"""工具注册表：生成 tools 字段，执行模型发来的 tool_call。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterable
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from simpleagent.events import ToolResult
from simpleagent.tools.base import Tool, ToolContext, ToolError
from simpleagent.tools.output import (
    DEFAULT_MAX_CHARS,
    DEFAULT_MAX_LINES,
    truncate,
)

if TYPE_CHECKING:
    # 只用于类型标注。不能在这里做运行时导入：serve/__init__ 会拉起 runner，
    # runner 又回来导入本模块的调用方 agent.loop，形成循环导入。
    from simpleagent.serve.approval import Approver


def format_validation_error(error: ValidationError) -> str:
    # 不带 input：参数可能很长，模型自己知道传了什么
    return "；".join(
        f"{'.'.join(map(str, err['loc'])) or '参数'}: {err['msg']}"
        for err in error.errors(include_url=False, include_input=False)
    )


class ToolRegistry:
    def __init__(
        self,
        tools: Iterable[Tool] = (),
        max_output_chars: int = DEFAULT_MAX_CHARS,
        max_output_lines: int = DEFAULT_MAX_LINES,
        approver: Approver | None = None,
    ) -> None:
        self._tools: dict[str, Tool] = {}
        for item in tools:
            self.register(item)
        self.max_output_chars = max_output_chars
        self.max_output_lines = max_output_lines
        # 写操作执行前的审批器；None 表示不审批（沿用旧行为，REPL 默认走这条路径）
        self.approver = approver

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"工具重名：{tool.name}")
        self._tools[tool.name] = tool

    def schemas(self) -> list[dict[str, Any]]:
        # 按注册顺序输出：工具列表在会话内保持不变，请求前缀才能命中缓存
        return [item.schema() for item in self._tools.values()]

    def is_readonly(self, name: str) -> bool:
        """未知工具当只读处理：它只会得到一条错误结果，不会有副作用。"""
        tool = self._tools.get(name)
        return True if tool is None else tool.readonly

    async def execute(self, tool_call: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """执行一个 tool_call。任何失败都转成 is_error 的结果回给模型，不抛异常。

        Chat Completions 的 tool 消息没有错误标记，所以错误要写在 content 文本里。
        CancelledError 不在这里处理（它是 BaseException），由 agent loop 收尾。
        """
        call_id = tool_call.get("id") or ""
        function = tool_call.get("function") or {}
        name = function.get("name") or ""
        raw_arguments = function.get("arguments") or ""

        def error(message: str) -> ToolResult:
            return ToolResult(call_id, name, f"错误：{message}", is_error=True)

        tool = self._tools.get(name)
        if tool is None:
            return error(f"未知工具 {name}。可用工具：{', '.join(self._tools) or '无'}")
        try:
            # 有些模型调用无参工具时 arguments 传空串
            data = json.loads(raw_arguments) if raw_arguments.strip() else {}
        except json.JSONDecodeError as e:
            return error(f"参数不是合法 JSON（{e}）：{raw_arguments[:200]}")
        try:
            args = tool.args_model.model_validate(data)
        except ValidationError as e:
            return error(f"参数校验失败：{format_validation_error(e)}")
        # 写操作（readonly=False）执行前先问审批器；只读工具直接放行
        if self.approver is not None and not tool.readonly:
            decision = await self.approver.request(
                session_id=ctx.session_id or "",
                tool_name=name,
                arguments=raw_arguments,
            )
            if not decision.allow:
                return error(f"工具 {name} 需要人工审批，已被拒绝")
        try:
            content = await tool.fn(args, ctx)
        except ToolError as e:
            return error(str(e))
        except Exception as e:  # 工具自身的 bug 也回给模型，不让整个 loop 崩掉
            return error(f"工具执行异常 {type(e).__name__}: {e}")
        if tool.truncate_output:
            content = self.trim(content, name, ctx)
        return ToolResult(call_id, name, content)

    async def execute_many(
        self, tool_calls: list[dict[str, Any]], ctx: ToolContext
    ) -> AsyncIterator[list[ToolResult]]:
        """执行同一条消息里的多个 tool_call，按 tool_calls 的顺序分批产出结果。

        全是只读工具就并行（省时间），所有结果作为一批产出；只要有一个写操作就按原顺序
        逐个执行，每执行完一个就产出一批——模型常常“先读 A 再写 A”，并行的话可能读到
        旧内容，或者两个写互相覆盖。逐个产出是为了中途被中断时，已经执行完的写操作
        能如实记进历史，而不是被当成“没有结果”，让模型重做一遍。
        """
        names = [(call.get("function") or {}).get("name") or "" for call in tool_calls]
        if all(self.is_readonly(name) for name in names):
            yield list(await asyncio.gather(*(self.execute(call, ctx) for call in tool_calls)))
            return
        for call in tool_calls:
            yield [await self.execute(call, ctx)]

    def trim(self, content: str, name: str, ctx: ToolContext) -> str:
        """过长的输出只留开头，完整内容落盘。"""
        return truncate(
            content,
            self.max_output_chars,
            self.max_output_lines,
            save=lambda text: ctx.save_output(text, name),
        )
