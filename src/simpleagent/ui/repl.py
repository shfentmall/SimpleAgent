"""交互式 REPL：读取输入 → 流式调用模型 → 渲染事件。

用 asyncio.Runner 在多轮之间复用同一个事件循环（AsyncOpenAI 的连接池绑定在循环上）。
Runner 会把 Ctrl+C 转成当前任务的 CancelledError，于是中断时可以在 chat() 里收尾。
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Callable
from typing import Any, TextIO

import openai

from simpleagent.agent.prompt import build_system_prompt
from simpleagent.config import ENV_FILENAME, Config, ConfigError, Profile, home_dir
from simpleagent.events import MessageDone, ReasoningDelta, TextDelta, Usage
from simpleagent.llm.client import LLM, LLMClient
from simpleagent.trace import Tracer, new_session_id

try:
    import readline  # noqa: F401  让 input() 支持方向键和输入历史
except ImportError:  # pragma: no cover
    pass

DIM, RED, BOLD, RESET = "\033[2m", "\033[31m", "\033[1m", "\033[0m"

HELP = '''命令：
  /model [name]   查看或切换模型 profile（对话历史保留）
  /clear          清空对话历史
  /usage          本会话的 token 用量
  /help           显示帮助
  /exit           退出
多行输入：单独一行输入 """ 开始，再输入 """ 结束。
Ctrl+C 中断当前回复，Ctrl+D 退出。'''

LLMFactory = Callable[[str, Profile], LLM]


def _supports_color(out: TextIO) -> bool:
    return hasattr(out, "isatty") and out.isatty() and not os.environ.get("NO_COLOR")


def format_stats(done: MessageDone, model: str) -> str:
    parts = [model]
    if done.usage:
        u = done.usage
        prompt = f"输入 {u.prompt_tokens:,}"
        if u.cached_tokens:
            prompt += f"（缓存 {u.cached_tokens:,}）"
        completion = f"输出 {u.completion_tokens:,}"
        if u.reasoning_tokens:
            completion += f"（思考 {u.reasoning_tokens:,}）"
        parts += [prompt, completion]
    else:
        parts.append("无 usage 数据")
    if done.ttft is not None:
        parts.append(f"首字 {done.ttft:.2f}s")
    parts.append(f"耗时 {done.elapsed:.2f}s")
    if done.finish_reason == "length":
        parts.append("输出达到 max_tokens 被截断")
    return "[" + " · ".join(parts) + "]"


def describe_error(error: openai.APIError, profile: Profile) -> str:
    if isinstance(error, openai.APIStatusError):
        text = f"HTTP {error.status_code}：{error.message}"
    else:
        text = f"{type(error).__name__}：{error}"
    if isinstance(error, openai.AuthenticationError) and profile.api_key_env:
        text += (
            f"\n→ 检查 API key：{profile.api_key_env}，"
            f"环境变量优先，其次 {home_dir() / ENV_FILENAME}"
        )
    return text


class Renderer:
    """把事件流渲染到终端：思考内容灰色显示，正文正常显示。"""

    def __init__(self, out: TextIO, color: bool, show_reasoning: bool):
        self.out = out
        self.color = color
        self.show_reasoning = show_reasoning
        self.mode: str | None = None  # None / "reasoning" / "text"
        self.text = ""

    def _write(self, text: str, style: str = "") -> None:
        self.out.write(f"{style}{text}{RESET}" if style and self.color else text)
        self.out.flush()

    def on_event(self, event: TextDelta | ReasoningDelta) -> None:
        if isinstance(event, ReasoningDelta):
            if not self.show_reasoning:
                return
            if self.mode != "reasoning":
                self._write("思考：", DIM)
                self.mode = "reasoning"
            self._write(event.text, DIM)
        else:
            if self.mode == "reasoning":
                self._write("\n\n")
            self.mode = "text"
            self.text += event.text
            self._write(event.text)

    def end(self) -> None:
        if self.mode is not None:
            self._write("\n")
            self.mode = None


class Repl:
    def __init__(
        self,
        config: Config,
        profile: str | None = None,
        *,
        llm_factory: LLMFactory | None = None,
        out: TextIO | None = None,
        input_fn: Callable[[str], str] = input,
    ):
        self.config = config
        self.out = out or sys.stdout
        self.color = _supports_color(self.out)
        self.input_fn = input_fn
        self.session_id = new_session_id()
        self.tracer = Tracer(
            home_dir() / "traces",
            self.session_id,
            enabled=config.trace.enabled,
            raw_chunks=config.trace.raw_chunks,
        )
        self.llm_factory = llm_factory or (lambda name, p: LLMClient(name, p, tracer=self.tracer))
        self.system_prompt = build_system_prompt(config.system_prompt)
        self.messages: list[dict[str, Any]] = []
        self.usage = Usage()
        self.requests = 0
        self.llm = self._make_llm(profile or config.default_profile)

    def _make_llm(self, name: str) -> LLM:
        if name not in self.config.profiles:
            raise ConfigError(f"没有名为 '{name}' 的 profile")
        return self.llm_factory(name, self.config.profiles[name])

    def print(self, text: str = "", style: str = "") -> None:
        if style and self.color:
            text = f"{style}{text}{RESET}"
        self.out.write(text + "\n")
        self.out.flush()

    # ------------------------------------------------------------------ 主循环

    def run(self) -> int:
        self.print(f"SimpleAgent · {self.llm.name}（{self.llm.profile.model}）", BOLD)
        if self.config.trace.enabled:
            self.print(f"trace：{self.tracer.dir}", DIM)
        self.print("输入 /help 查看命令", DIM)
        with asyncio.Runner() as runner:
            try:
                while True:
                    try:
                        line = self._read_input()
                    except KeyboardInterrupt:
                        self.print()
                        continue
                    except EOFError:
                        self.print()
                        break
                    if not line.strip():
                        continue
                    try:
                        if not runner.run(self.handle(line)):
                            break
                    except KeyboardInterrupt:
                        self.print("[已中断]", DIM)
            finally:
                runner.run(self.llm.close())
        return 0

    def _read_input(self) -> str:
        line = self.input_fn("> ")
        if line.strip() != '"""':
            return line
        lines = []
        while (next_line := self.input_fn("... ")).strip() != '"""':
            lines.append(next_line)
        return "\n".join(lines)

    async def handle(self, line: str) -> bool:
        """处理一行输入；返回 False 表示退出。"""
        if line.startswith("/"):
            return await self.command(line)
        await self.chat(line)
        return True

    # ------------------------------------------------------------------ 对话

    async def chat(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})
        renderer = Renderer(self.out, self.color, self.config.show_reasoning)
        request = [{"role": "system", "content": self.system_prompt}, *self.messages]
        done: MessageDone | None = None
        try:
            async for event in self.llm.stream(request):
                if isinstance(event, MessageDone):
                    done = event
                else:
                    renderer.on_event(event)
        except asyncio.CancelledError:
            renderer.end()
            # 保留已经输出的部分，让下一轮对话能接上；什么都没输出就撤回这条用户消息
            if renderer.text:
                self.messages.append({"role": "assistant", "content": renderer.text})
            else:
                self.messages.pop()
            raise
        except openai.APIError as e:
            renderer.end()
            self.messages.pop()
            self.print(f"请求失败：{describe_error(e, self.llm.profile)}", RED)
            return

        renderer.end()
        assert done is not None, "stream 结束时必须产出 MessageDone"
        self.messages.append(done.message)
        self.requests += 1
        if done.usage:
            self.usage += done.usage
        self.print(format_stats(done, self.llm.profile.model), DIM)

    # ------------------------------------------------------------------ 命令

    async def command(self, line: str) -> bool:
        name, _, arg = line[1:].strip().partition(" ")
        arg = arg.strip()
        match name:
            case "exit" | "quit":
                return False
            case "help":
                self.print(HELP)
            case "clear":
                self.messages.clear()
                self.print("已清空对话历史")
            case "usage":
                u = self.usage
                self.print(
                    f"请求 {self.requests} 次"
                    f" · 输入 {u.prompt_tokens:,}（缓存 {u.cached_tokens:,}）"
                    f" · 输出 {u.completion_tokens:,}（思考 {u.reasoning_tokens:,}）"
                )
            case "model":
                await self._switch_model(arg)
            case _:
                self.print(f"未知命令 /{name}，输入 /help 查看")
        return True

    async def _switch_model(self, name: str) -> None:
        if not name:
            for profile_name, profile in self.config.profiles.items():
                mark = "*" if profile_name == self.llm.name else " "
                self.print(f" {mark} {profile_name:<12} {profile.model}  {profile.base_url}")
            return
        try:
            llm = self._make_llm(name)
        except ConfigError as e:
            self.print(f"切换失败：{e}", RED)
            return
        await self.llm.close()
        self.llm = llm
        self.print(f"已切换到 {name}（{llm.profile.model}），对话历史保留")
