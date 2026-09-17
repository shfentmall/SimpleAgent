"""Claude Code 的无头模式适配器。

命令形状（`claude 2.1.274` 实测 + 官方文档）：

    claude --output-format stream-json --verbose --include-partial-messages [权限] -p -- "<prompt>"

两个必须记住的细节：

1. `--output-format stream-json` **必须**配 `--verbose`，否则是硬报错（不是警告）。
2. 命令行把 prompt 放在最后并用 `--` 隔开：`--tools` 这类参数是变长的，
   不加 `--` 的话 prompt 会被当成它的取值吃掉。

事件形状（NDJSON）：

    {"type":"system","subtype":"init", session_id, cwd, model, permissionMode, tools, ...}
    {"type":"assistant","message":{"content":[{text|thinking|tool_use}...]}, session_id, uuid}
    {"type":"user","message":{"content":[{type:"tool_result", tool_use_id, content, is_error}]}}
    {"type":"result","subtype":"success","is_error":false,"result":"最终文本","usage":{...},"total_cost_usd":..}

**成败只能看 `result.is_error`**：本机没登录时录到的样本里 `subtype` 还是 `"success"`，
`is_error` 才是 `true`（见 tests/fixtures/cli/claude-not-logged-in.jsonl）。
"""

from __future__ import annotations

import json
from typing import Any

from simpleagent.agents.base import FULL, SAFE, CliTurn
from simpleagent.events import (
    Event,
    MessageDone,
    ReasoningDelta,
    TextDelta,
    ToolCallStart,
    ToolResult,
    Usage,
)

DEFAULT_COMMAND = "claude"

# 只读档放行的工具：这三个只读文件，不会改东西也不会跑命令
SAFE_TOOLS = "Read,Glob,Grep"


def _text_of(content: Any) -> str:
    """tool_result 的 content 可能是字符串，也可能是 text 块数组。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


class ClaudeAdapter:
    name = "claude-code"

    def __init__(self) -> None:
        self.session_id: str | None = None
        self.usage = Usage()
        self.cost_usd = 0.0
        # 逐字流式已经吐出去的文本：assistant 整段事件再来一次时要跳过，避免重复
        self._streamed = ""

    # ----------------------------------------------------------------- 命令
    def command(
        self,
        prompt: str,
        *,
        command: str | None = None,
        model: str | None = None,
        resume: str | None = None,
        mode: str = SAFE,
    ) -> list[str]:
        argv = [
            command or DEFAULT_COMMAND,
            "--output-format",
            "stream-json",
            "--verbose",
            "--include-partial-messages",
        ]
        if mode == FULL:
            argv.append("--dangerously-skip-permissions")
        else:
            # 只读档：工具集直接砍到三个只读工具，权限层再兜一道
            # （--permission-prompts none = 该问的一律自动拒，进程不会挂住等人）
            argv += [
                "--tools",
                SAFE_TOOLS,
                "--permission-mode",
                "dontAsk",
                "--permission-prompts",
                "none",
            ]
        if model:
            argv += ["--model", model]
        if resume:
            argv += ["--resume", resume]
        argv += ["-p", "--", prompt]
        return argv

    def env(self, mode: str = SAFE) -> dict[str, str]:
        """claude 这边权限全靠命令行参数，不需要注入环境变量。

        「本机默认」= 不注入任何东西，它该怎么读自己的登录态和配置就怎么读。
        以后要「用我们 config.toml 里的 profile 跑 claude」时，
        ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN / ANTHROPIC_MODEL 会从这里加进来。
        """
        return {}

    # ----------------------------------------------------------------- 解析
    def parse(self, line: str) -> CliTurn:
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            return CliTurn()  # 不是 JSON 的行直接跳过，别让一行噪音毁掉整轮
        if not isinstance(d, dict):
            return CliTurn()

        kind = d.get("type")
        if kind == "system":
            return self._system(d)
        if kind == "stream_event":
            return self._stream_event(d)
        if kind == "assistant":
            return self._assistant(d)
        if kind == "user":
            return self._tool_results(d)
        if kind == "result":
            return self._result(d)
        return CliTurn()  # tool_progress 之类的先不管

    def _system(self, d: dict[str, Any]) -> CliTurn:
        if d.get("subtype") == "init":
            self.session_id = d.get("session_id") or self.session_id
        return CliTurn()

    def _stream_event(self, d: dict[str, Any]) -> CliTurn:
        """`--include-partial-messages` 的增量块。

        注意：这个事件的形状**还没用真实样本验证过**（本机 claude 未登录，录不到成功路径）。
        解析失败没关系——整段的 `assistant` 事件照样会把文本吐出来，只是从逐字降级成整段。
        """
        ev = d.get("event") or {}
        if ev.get("type") != "content_block_delta":
            return CliTurn()
        delta = ev.get("delta") or {}
        dtype = delta.get("type")
        if dtype == "text_delta" and delta.get("text"):
            self._streamed += delta["text"]
            return CliTurn(events=[TextDelta(delta["text"])])
        if dtype == "thinking_delta" and delta.get("thinking"):
            return CliTurn(events=[ReasoningDelta(delta["thinking"])])
        return CliTurn()

    def _assistant(self, d: dict[str, Any]) -> CliTurn:
        message = d.get("message") or {}
        if d.get("is_api_error_message"):
            text = _text_of(message.get("content"))
            return CliTurn(finished=True, ok=False, error=text or "API 调用失败")

        events: list[Event] = []
        text = ""
        for block in message.get("content") or []:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                text += block.get("text", "")
            elif btype == "thinking":
                if block.get("thinking"):
                    events.append(ReasoningDelta(block["thinking"]))
            elif btype == "tool_use":
                events.append(
                    ToolCallStart(
                        call_id=block.get("id", ""),
                        name=block.get("name", ""),
                        arguments=json.dumps(block.get("input") or {}, ensure_ascii=False),
                    )
                )
        if not text:
            # 只调工具、没出文本的一步：不落消息，免得历史里多一条空气泡
            return CliTurn(events=events)
        if self._streamed:
            # 这段文本已经逐字流出去过了，message_done 用流出去的那份，别再整段重发
            text = self._streamed
        self._streamed = ""
        events.append(
            MessageDone(
                message={"role": "assistant", "content": text},
                finish_reason="stop",
                usage=self.usage,
            )
        )
        return CliTurn(events=events)

    def _tool_results(self, d: dict[str, Any]) -> CliTurn:
        """工具结果藏在 user 消息里（claude 就是这么设计的）。"""
        message = d.get("message") or {}
        events: list[Event] = []
        for block in message.get("content") or []:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            events.append(
                ToolResult(
                    call_id=block.get("tool_use_id", ""),
                    name="",  # 结果里没有工具名，前端按 call_id 找得到那张卡
                    content=_text_of(block.get("content")),
                    is_error=bool(block.get("is_error")),
                )
            )
        return CliTurn(events=events)

    def _result(self, d: dict[str, Any]) -> CliTurn:
        u = d.get("usage") or {}
        self.usage = self.usage + Usage(
            prompt_tokens=(u.get("input_tokens") or 0)
            + (u.get("cache_read_input_tokens") or 0)
            + (u.get("cache_creation_input_tokens") or 0),
            completion_tokens=u.get("output_tokens") or 0,
            cached_tokens=u.get("cache_read_input_tokens") or 0,
        )
        self.cost_usd += float(d.get("total_cost_usd") or 0)
        self.session_id = d.get("session_id") or self.session_id
        if d.get("is_error"):
            # 没登录 / 余额不足这类：把对面原话交给 Runner，由它发 error 帧，
            # 不再当成一条助手消息落盘（否则界面上会同时出现气泡和错误卡）
            return CliTurn(
                finished=True, ok=False, error=d.get("result") or "claude 结束但没给出结果"
            )
        return CliTurn(finished=True, ok=True)
