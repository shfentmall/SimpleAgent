"""事件类型：LLM 客户端和 agent loop 产出的事件流。

前端（REPL / headless / daemon）只消费事件，不关心事件从哪来。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TextDelta:
    text: str


@dataclass
class ReasoningDelta:
    text: str


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0  # 命中前缀缓存的输入 token
    reasoning_tokens: int = 0  # 输出中用于思考的 token

    @classmethod
    def from_dict(cls, usage: dict[str, Any]) -> Usage:
        prompt_details = usage.get("prompt_tokens_details") or {}
        completion_details = usage.get("completion_tokens_details") or {}
        return cls(
            prompt_tokens=usage.get("prompt_tokens") or 0,
            completion_tokens=usage.get("completion_tokens") or 0,
            # OpenAI 标准字段 / DeepSeek 私有字段
            cached_tokens=prompt_details.get("cached_tokens")
            or usage.get("prompt_cache_hit_tokens")
            or 0,
            reasoning_tokens=completion_details.get("reasoning_tokens") or 0,
        )

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            self.prompt_tokens + other.prompt_tokens,
            self.completion_tokens + other.completion_tokens,
            self.cached_tokens + other.cached_tokens,
            self.reasoning_tokens + other.reasoning_tokens,
        )


@dataclass
class MessageDone:
    """一次 LLM 调用结束：拼好的 assistant 消息（OpenAI 格式 dict）。"""

    message: dict[str, Any]
    finish_reason: str | None = None
    usage: Usage | None = None
    ttft: float | None = None  # 首个 token 延迟（秒）
    elapsed: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolCallStart:
    """模型要求调用一个工具，马上执行。"""

    call_id: str
    name: str
    arguments: str  # 模型给的原始 JSON 字符串，怎么显示由前端决定


@dataclass
class ToolResult:
    """一次工具调用的结果；content 就是回给模型的文本。"""

    call_id: str
    name: str
    content: str
    is_error: bool = False

    def as_message(self) -> dict[str, Any]:
        return {"role": "tool", "tool_call_id": self.call_id, "content": self.content}


@dataclass
class MaxStepsReached:
    """一轮对话请求模型的次数达到上限，loop 停止。"""

    max_steps: int


Event = TextDelta | ReasoningDelta | MessageDone | ToolCallStart | ToolResult | MaxStepsReached
