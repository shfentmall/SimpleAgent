"""工具抽象：执行上下文、Tool、@tool 装饰器。

一个工具 = pydantic 参数模型 + async 函数。参数模型同时用来生成给模型看的 JSON Schema
和校验模型传来的参数，两边不会对不上。
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, get_type_hints

from pydantic import BaseModel

ToolFn = Callable[[Any, "ToolContext"], Awaitable[str]]


@dataclass
class ToolContext:
    """工具执行时拿到的环境。后续加入审批器（M3）、进度上报、取消信号。"""

    cwd: Path

    def resolve(self, path: str) -> Path:
        """相对路径基于 ctx.cwd 解析，不用进程的当前目录（daemon 里两者不一样）。"""
        return (self.cwd / Path(path).expanduser()).resolve()


class ToolError(Exception):
    """可以预期的失败（比如路径不存在）：消息原样回给模型，让它自己调整参数。"""


# JSON Schema 里值是子 schema 的字段；其余字段（default、enum 等）里的 title 不是 schema 标题
_SCHEMA_MAPS = ("properties", "$defs")
_SCHEMA_LISTS = ("anyOf", "allOf", "oneOf", "prefixItems")
_SCHEMA_VALUES = ("items", "additionalProperties", "not")


def clean_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """去掉 pydantic 自动生成的 title：对模型没有信息量，白占 token。"""
    cleaned = {key: value for key, value in schema.items() if key != "title"}
    for key in _SCHEMA_MAPS:
        if key in cleaned:
            cleaned[key] = {name: clean_schema(sub) for name, sub in cleaned[key].items()}
    for key in _SCHEMA_LISTS:
        if key in cleaned:
            cleaned[key] = [clean_schema(sub) for sub in cleaned[key]]
    for key in _SCHEMA_VALUES:
        if isinstance(cleaned.get(key), dict):
            cleaned[key] = clean_schema(cleaned[key])
    return cleaned


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args_model: type[BaseModel]
    fn: ToolFn

    def schema(self) -> dict[str, Any]:
        """请求体 tools 字段里的一项（OpenAI function calling 格式）。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": clean_schema(self.args_model.model_json_schema()),
            },
        }


def tool(name: str, description: str) -> Callable[[ToolFn], Tool]:
    """把 `async def fn(args: SomeArgs, ctx: ToolContext) -> str` 包装成 Tool。

    参数模型从第一个参数的类型注解推出。
    """

    def decorate(fn: ToolFn) -> Tool:
        if not inspect.iscoroutinefunction(fn):
            raise TypeError(f"工具 {name} 必须是 async 函数")
        params = list(inspect.signature(fn).parameters)
        args_model = get_type_hints(fn).get(params[0]) if params else None
        if not (isinstance(args_model, type) and issubclass(args_model, BaseModel)):
            raise TypeError(f"工具 {name} 的第一个参数必须标注为 pydantic BaseModel 子类")
        return Tool(name, description, args_model, fn)

    return decorate
