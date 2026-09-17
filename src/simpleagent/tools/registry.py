"""工具注册表：生成 tools 字段，执行模型发来的 tool_call。"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from pydantic import ValidationError

from simpleagent.events import ToolResult
from simpleagent.tools.base import Tool, ToolContext, ToolError


def format_validation_error(error: ValidationError) -> str:
    # 不带 input：参数可能很长，模型自己知道传了什么
    return "；".join(
        f"{'.'.join(map(str, err['loc'])) or '参数'}: {err['msg']}"
        for err in error.errors(include_url=False, include_input=False)
    )


class ToolRegistry:
    def __init__(self, tools: Iterable[Tool] = ()) -> None:
        self._tools: dict[str, Tool] = {}
        for item in tools:
            self.register(item)

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"工具重名：{tool.name}")
        self._tools[tool.name] = tool

    def schemas(self) -> list[dict[str, Any]]:
        # 按注册顺序输出：工具列表在会话内保持不变，请求前缀才能命中缓存
        return [item.schema() for item in self._tools.values()]

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
        try:
            content = await tool.fn(args, ctx)
        except ToolError as e:
            return error(str(e))
        except Exception as e:  # 工具自身的 bug 也回给模型，不让整个 loop 崩掉
            return error(f"工具执行异常 {type(e).__name__}: {e}")
        return ToolResult(call_id, name, content)
