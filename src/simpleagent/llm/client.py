"""OpenAI 兼容协议（Chat Completions）的流式客户端。

职责：
- 按 profile 组装请求体（stream_options / extra_body / 思考内容回传策略）
- 把 SSE chunk 拼成完整的 assistant 消息，同时产出增量事件
- 把每次请求/响应（包括失败和中断）写进 trace
"""

from __future__ import annotations

import asyncio
import ipaddress
import time
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx2
from openai import AsyncOpenAI, DefaultAsyncHttpxClient

from simpleagent.config import Profile, Quirks
from simpleagent.events import Event, MessageDone, ReasoningDelta, TextDelta, Usage
from simpleagent.trace import Tracer

# 会话历史里统一用这个字段保存思考内容；发请求时再按 profile 改名或去掉
REASONING_KEY = "reasoning_content"


class LLM(Protocol):
    name: str
    profile: Profile

    def stream(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> AsyncIterator[Event]: ...

    async def close(self) -> None: ...


class StreamAccumulator:
    """把流式 chunk（dict 形式）拼成完整的 assistant 消息。"""

    def __init__(self, reasoning_field: str | None = REASONING_KEY):
        self.reasoning_field = reasoning_field
        self.text: list[str] = []
        self.reasoning: list[str] = []
        self.tool_calls: dict[int, dict[str, Any]] = {}
        self.finish_reason: str | None = None
        self.usage: dict[str, Any] | None = None

    def feed(self, chunk: dict[str, Any]) -> list[Event]:
        events: list[Event] = []
        # 开启 include_usage 后，usage 通常在最后一个 choices 为空的 chunk 里
        if chunk.get("usage"):
            self.usage = chunk["usage"]
        for choice in chunk.get("choices") or []:
            if choice.get("index", 0) != 0:
                continue
            delta = choice.get("delta") or {}
            if self.reasoning_field and (reasoning := delta.get(self.reasoning_field)):
                self.reasoning.append(reasoning)
                events.append(ReasoningDelta(reasoning))
            if content := delta.get("content"):
                self.text.append(content)
                events.append(TextDelta(content))
            for tool_call in delta.get("tool_calls") or []:
                self._merge_tool_call(tool_call)
            if choice.get("finish_reason"):
                self.finish_reason = choice["finish_reason"]
        return events

    def _merge_tool_call(self, delta: dict[str, Any]) -> None:
        # 同一个 tool_call 的多个分片靠 index 关联；个别实现不带 index，视为新的调用
        index = delta.get("index")
        if index is None:
            index = len(self.tool_calls)
        slot = self.tool_calls.setdefault(
            index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
        )
        if delta.get("id"):
            slot["id"] = delta["id"]
        function = delta.get("function") or {}
        # name 只取第一次出现的（有的实现每个分片都重复带 name）；arguments 是真正分片拼接的
        if function.get("name") and not slot["function"]["name"]:
            slot["function"]["name"] = function["name"]
        if function.get("arguments"):
            slot["function"]["arguments"] += function["arguments"]

    def message(self) -> dict[str, Any]:
        message: dict[str, Any] = {"role": "assistant", "content": "".join(self.text)}
        if self.reasoning:
            message[REASONING_KEY] = "".join(self.reasoning)
        if self.tool_calls:
            message["tool_calls"] = [self.tool_calls[i] for i in sorted(self.tool_calls)]
            message["content"] = message["content"] or None
        return message


def prepare_messages(messages: list[dict[str, Any]], quirks: Quirks) -> list[dict[str, Any]]:
    """按 reasoning_echo 策略处理历史里的思考内容，返回新的列表（不修改会话历史）。"""
    last_user = max((i for i, m in enumerate(messages) if m.get("role") == "user"), default=-1)
    prepared = []
    for i, message in enumerate(messages):
        if REASONING_KEY not in message:
            prepared.append(message)
            continue
        message = dict(message)
        reasoning = message.pop(REASONING_KEY)
        echo = quirks.reasoning_echo == "all" or (
            quirks.reasoning_echo == "current_turn" and i > last_user
        )
        if echo and quirks.reasoning_field:
            message[quirks.reasoning_field] = reasoning
        prepared.append(message)
    return prepared


def is_loopback(url: str) -> bool:
    host = urlsplit(url).hostname or ""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class LLMClient:
    def __init__(
        self,
        name: str,
        profile: Profile,
        tracer: Tracer | None = None,
        http_client: httpx2.AsyncClient | None = None,
    ):
        self.name = name
        self.profile = profile
        self.tracer = tracer
        if http_client is None and is_loopback(profile.base_url):
            # 本地服务（如 Ollama）不读 http_proxy 等环境变量，否则本机代理会把请求转走并返回 502
            http_client = DefaultAsyncHttpxClient(trust_env=False)
        self._client = AsyncOpenAI(
            base_url=profile.base_url,
            api_key=profile.api_key(),
            timeout=profile.timeout,
            max_retries=profile.max_retries,
            http_client=http_client,
        )

    def build_request(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        profile, quirks = self.profile, self.profile.quirks
        request: dict[str, Any] = {
            "model": profile.model,
            "messages": prepare_messages(messages, quirks),
            "stream": True,
        }
        if quirks.stream_usage:
            request["stream_options"] = {"include_usage": True}
        if profile.max_tokens is not None:
            request["max_tokens"] = profile.max_tokens
        if profile.temperature is not None:
            request["temperature"] = profile.temperature
        if tools:
            request["tools"] = tools
            if not quirks.parallel_tool_calls:
                request["parallel_tool_calls"] = False
        return request

    async def stream(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> AsyncIterator[Event]:
        request = self.build_request(messages, tools)
        accumulator = StreamAccumulator(self.profile.quirks.reasoning_field)
        raw_chunks: list[dict[str, Any]] | None = (
            [] if self.tracer and self.tracer.raw_chunks else None
        )
        started_at = datetime.now().isoformat(timespec="seconds")
        start = time.monotonic()
        ttft: float | None = None

        try:
            stream = await self._client.chat.completions.create(
                **request, extra_body=self.profile.extra_body or None
            )
            try:
                async for chunk in stream:
                    data = chunk.model_dump(exclude_none=True)
                    if raw_chunks is not None:
                        raw_chunks.append(data)
                    for event in accumulator.feed(data):
                        if ttft is None:
                            ttft = time.monotonic() - start
                        yield event
            finally:
                await stream.close()
        except BaseException as e:
            # 失败和中断也要留痕：Ctrl+C（CancelledError）或消费方提前退出（GeneratorExit）
            status = (
                "cancelled"
                if isinstance(e, asyncio.CancelledError | GeneratorExit | KeyboardInterrupt)
                else "error"
            )
            self._trace(request, accumulator, raw_chunks, started_at, start, ttft, status, e)
            raise

        self._trace(request, accumulator, raw_chunks, started_at, start, ttft, "ok")
        yield MessageDone(
            message=accumulator.message(),
            finish_reason=accumulator.finish_reason,
            usage=Usage.from_dict(accumulator.usage) if accumulator.usage else None,
            ttft=ttft,
            elapsed=time.monotonic() - start,
        )

    def _trace(
        self,
        request: dict[str, Any],
        accumulator: StreamAccumulator,
        raw_chunks: list[dict[str, Any]] | None,
        started_at: str,
        start: float,
        ttft: float | None,
        status: str,
        error: BaseException | None = None,
    ) -> None:
        if self.tracer is None:
            return
        record: dict[str, Any] = {
            "status": status,
            "profile": self.name,
            "base_url": self.profile.base_url,
            "started_at": started_at,
            "elapsed_s": round(time.monotonic() - start, 3),
            "ttft_s": None if ttft is None else round(ttft, 3),
            # 与实际发送的请求体一致：extra_body 会被 SDK 合并到顶层
            "request": {**request, **self.profile.extra_body},
            "response": {
                "message": accumulator.message(),
                "finish_reason": accumulator.finish_reason,
                "usage": accumulator.usage,
            },
        }
        if error is not None:
            record["error"] = f"{type(error).__name__}: {error}"
        if raw_chunks is not None:
            record["raw_chunks"] = raw_chunks
        self.tracer.record(record)

    async def close(self) -> None:
        await self._client.close()
