"""OpenCode 的无头模式适配器。

命令形状（`opencode 1.18.31` 实测）：

    opencode run --format json [--auto] [--model provider/model] [--session ses_xxx] -- "<prompt>"

几个和 claude 不一样的地方：

- 只有 `--format`，**没有** `--output-format`。
- 工具调用和工具结果在**同一个 `tool_use` 事件**里（`part.state.status == "completed"`
  时 state.output 就是结果），所以一条事件要翻成两个帧。
- `tokens` / `cost` 都是**本步增量**（每个 step_finish 只报这一步用了多少），必须自己累加。
- 进程退出码不可靠（任务失败也可能退 0），成败得看事件。
- 权限只能预置：无头模式下 `ask` 会挂住等人，所以要么 `--auto`（全放行），
  要么用 `OPENCODE_CONFIG_CONTENT` 注入一份只读的 permission 配置。
"""

from __future__ import annotations

import json
from typing import Any

from simpleagent.agents.base import FULL, SAFE, CliTurn
from simpleagent.events import Event, MessageDone, TextDelta, ToolCallStart, ToolResult, Usage

DEFAULT_COMMAND = "opencode"

# 只读档：除了读类工具，其余一律 deny（bash / edit / webfetch / task … 全在内）
SAFE_PERMISSION = {
    "permission": {
        "*": "deny",
        "read": "allow",
        "glob": "allow",
        "grep": "allow",
        "lsp": "allow",
    }
}


class OpenCodeAdapter:
    name = "opencode"

    def __init__(self) -> None:
        self.session_id: str | None = None
        self.usage = Usage()
        self.cost_usd = 0.0
        self._buffer = ""  # 当前这一步已经攒下的文本，step_finish 时收口

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
        argv = [command or DEFAULT_COMMAND, "run", "--format", "json"]
        if mode == FULL:
            argv.append("--auto")
        if model:
            argv += ["--model", model]
        if resume:
            argv += ["--session", resume]
        argv += ["--", prompt]  # `run [message..]` 是变长参数，用 -- 隔开免得被当成选项
        return argv

    def env(self, mode: str = SAFE) -> dict[str, str]:
        if mode == FULL:
            return {}
        # 内联配置只覆盖 permission 这一项，provider / 模型配置照旧从用户自己的配置读
        return {
            "OPENCODE_QUIET": "1",  # stdout 只留事件流，别混日志
            "OPENCODE_CONFIG_CONTENT": json.dumps(SAFE_PERMISSION, ensure_ascii=False),
        }

    # ----------------------------------------------------------------- 解析
    def parse(self, line: str) -> CliTurn:
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            return CliTurn()
        if not isinstance(d, dict):
            return CliTurn()

        if d.get("sessionID"):
            self.session_id = d["sessionID"]

        kind = d.get("type")
        if kind == "text":
            return self._text(d)
        if kind == "tool_use":
            return self._tool_use(d)
        if kind == "step_finish":
            return self._step_finish(d)
        if kind == "error":
            return self._error(d)
        return CliTurn()  # step_start 之类没有要翻译的东西

    def _text(self, d: dict[str, Any]) -> CliTurn:
        text = (d.get("part") or {}).get("text") or ""
        if not text:
            return CliTurn()
        self._buffer += text
        return CliTurn(events=[TextDelta(text)])

    def _tool_use(self, d: dict[str, Any]) -> CliTurn:
        part = d.get("part") or {}
        state = part.get("state") or {}
        call_id = part.get("callID") or ""
        name = part.get("tool") or "tool"
        events: list[Event] = [
            ToolCallStart(
                call_id=call_id,
                name=name,
                arguments=json.dumps(state.get("input") or {}, ensure_ascii=False),
            )
        ]
        if state.get("status") == "completed":
            meta = state.get("metadata") or {}
            exit_code = meta.get("exit")
            events.append(
                ToolResult(
                    call_id=call_id,
                    name=name,
                    content=str(state.get("output") or ""),
                    is_error=exit_code not in (None, 0),
                )
            )
        return CliTurn(events=events)

    def _step_finish(self, d: dict[str, Any]) -> CliTurn:
        part = d.get("part") or {}
        tokens = part.get("tokens") or {}
        cache = tokens.get("cache") or {}
        self.usage = self.usage + Usage(
            prompt_tokens=(tokens.get("input") or 0)
            + (cache.get("read") or 0)
            + (cache.get("write") or 0),
            completion_tokens=tokens.get("output") or 0,
            cached_tokens=cache.get("read") or 0,
            reasoning_tokens=tokens.get("reasoning") or 0,
        )
        self.cost_usd += float(part.get("cost") or 0)

        events: list[Event] = []
        if self._buffer:
            events.append(
                MessageDone(
                    message={"role": "assistant", "content": self._buffer},
                    finish_reason="stop" if part.get("reason") == "stop" else "tool_calls",
                    usage=self.usage,
                )
            )
            self._buffer = ""

        reason = part.get("reason")
        if reason == "stop":
            return CliTurn(events=events, finished=True, ok=True)
        if reason == "error":
            return CliTurn(events=events, finished=True, ok=False, error="opencode 这一步报错")
        return CliTurn(events=events)  # tool-calls：还有下一步

    def _error(self, d: dict[str, Any]) -> CliTurn:
        err = d.get("error") or {}
        data = err.get("data") if isinstance(err, dict) else None
        message = (data or {}).get("message") if isinstance(data, dict) else None
        if not message and isinstance(err, dict):
            message = err.get("message") or err.get("name")
        return CliTurn(finished=True, ok=False, error=str(message or "opencode 运行出错"))
