"""Runner：后台 asyncio 线程，把一次用户输入交给对应空间的 Agent 执行。

职责：
- 按空间配置（目录 / profile / 验证命令）构造 Agent，注入审批器。
- 跑 agent.run()，把事件实时发到事件总线，同时把消息和元信息落盘。
- 支持取消（取消底层 asyncio 任务，loop 会先修好历史再抛出）。
- 支持手动触发验证命令，并把结果写成 verification 帧 + 落盘。

线程模型：Runner 自己起一个线程跑 asyncio 事件循环；HTTP 层在另一个线程，通过
run_coroutine_threadsafe / call_soon_threadsafe 与它通信，两者用总线（线程安全）解耦。
"""

from __future__ import annotations

import asyncio
import subprocess
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from simpleagent.agent.loop import Agent
from simpleagent.agent.prompt import build_system_prompt
from simpleagent.agent.session import Session
from simpleagent.config import TOOL_OUTPUT_DIRNAME, Config, home_dir
from simpleagent.events import Event, MessageDone, ToolResult
from simpleagent.serve.approval import APIApprover, ApprovalDecision, PendingApprovals
from simpleagent.serve.bus import EventBus
from simpleagent.serve.frames import (
    error_frame,
    event_to_frame,
    status_frame,
    verification_frame,
)
from simpleagent.spaces.models import Space
from simpleagent.spaces.store import SpaceStore
from simpleagent.tools import ToolRegistry, builtin_tools

# (profile_name, Profile) -> LLM 实例；测试时注入 FakeLLM
LLMFactory = Callable[[str, Any], Any]


def _action_to_decision(action: str) -> ApprovalDecision:
    action = (action or "").lower()
    if action == "always":
        return ApprovalDecision(allow=True, always=True)
    if action == "deny":
        return ApprovalDecision(allow=False)
    return ApprovalDecision(allow=True)


class Runner:
    def __init__(
        self,
        config: Config,
        *,
        store: SpaceStore | None = None,
        bus: EventBus | None = None,
        pending: PendingApprovals | None = None,
        llm_factory: LLMFactory | None = None,
    ) -> None:
        self.config = config
        self.store = store or SpaceStore()
        self.bus = bus or EventBus()
        self.pending = pending or PendingApprovals()
        self.llm_factory = llm_factory or self._default_llm_factory
        # 跨轮有效的「本次会话始终允许」记录
        self.always_allow: dict[str, set[str]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._tasks: dict[str, asyncio.Task] = {}
        self._agents: dict[str, Agent] = {}

    # ----------------------------------------------------------------- 生命周期
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        # 在主线程先把 loop 造好，保证 start() 返回后 _schedule 就能用，避免竞态
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="simpleagent-runner"
        )
        self._thread.start()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def shutdown(self) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _schedule(self, coro: Any) -> None:
        assert self._loop is not None
        asyncio.run_coroutine_threadsafe(coro, self._loop)

    # --------------------------------------------------- 对 HTTP 层暴露的同步接口
    def run_input(self, space_id: str, session_id: str, user_input: str) -> None:
        self._schedule(self._run_input(space_id, session_id, user_input))

    def cancel(self, session_id: str) -> None:
        if self._loop is None:
            return
        agent = self._agents.get(session_id)
        if agent is not None:
            self._loop.call_soon_threadsafe(agent.cancel)

    def approve(self, approval_id: str, action: str) -> bool:
        if self._loop is None:
            return False
        decision = _action_to_decision(action)
        self._loop.call_soon_threadsafe(self.pending.resolve, approval_id, decision)
        return True

    def verify(self, space_id: str, session_id: str) -> None:
        self._schedule(self._verify(space_id, session_id))

    # --------------------------------------------------------------- 内部实现
    async def _run_input(self, space_id: str, session_id: str, user_input: str) -> None:
        space = self.store.get_space(space_id)
        if space is None:
            return
        session = self.store.load_session(space_id, session_id)
        agent = self._build_agent(space, session)
        self._agents[session_id] = agent
        # 落盘：用户消息 + 状态置为 running；再广播一帧，面板才知道"开始了"
        self.store.append_message(space_id, session_id, {"role": "user", "content": user_input})
        self.store.update_meta(space_id, session_id, status="running")
        self.bus.publish(status_frame(session_id, "running", {"space_id": space_id}))

        task = asyncio.current_task()
        if task is not None:
            self._tasks[session_id] = task
        try:
            async for event in agent.run(session, user_input):
                self._on_event(space_id, session_id, event)
        except asyncio.CancelledError:
            self._finalize(space_id, session_id, session, "cancelled")
            raise
        except Exception as e:  # noqa: BLE001  任何异常都转成 error 帧并落盘状态
            message = f"{type(e).__name__}: {e}"
            self.bus.publish(error_frame(session_id, message))
            self._finalize(space_id, session_id, session, "error", reason=message)
        else:
            self._finalize(space_id, session_id, session, "done")
        finally:
            self._tasks.pop(session_id, None)
            self._agents.pop(session_id, None)

    def _on_event(self, space_id: str, session_id: str, event: Event) -> None:
        # 先落盘再广播：保证订阅者看到 message_done 帧时，消息已经写进 jsonl，
        # 避免「刷新页面消息丢了」这类竞态
        if isinstance(event, MessageDone):
            self.store.append_message(space_id, session_id, event.message)
        elif isinstance(event, ToolResult):
            self.store.append_message(space_id, session_id, event.as_message())
        self.bus.publish(event_to_frame(event, session_id))

    def _finalize(
        self,
        space_id: str,
        session_id: str,
        session: Session,
        status: str,
        *,
        reason: str | None = None,
    ) -> None:
        """一轮结束的唯一收口点：落盘终态 + 广播一帧 status。

        原来只有 cancelled / error 分支发帧，正常结束只写 meta，所以"跑完了"这件事
        从来没出过总线 —— 控制面板只能靠轮询 meta、且只筛 running，于是任务一结束
        就从面板消失。三个终态（done / error / cancelled）统一在这里广播。
        """
        usage = session.usage.__dict__
        self.store.update_meta(space_id, session_id, status=status, usage=usage)
        extra: dict[str, Any] = {"space_id": space_id, "usage": usage}
        if reason is not None:
            extra["reason"] = reason
        self.bus.publish(status_frame(session_id, status, extra))

    def _build_agent(self, space: Space, session: Session) -> Agent:
        profile = self.config.profiles[space.profile]
        llm = self.llm_factory(space.profile, profile)
        tools = ToolRegistry(
            builtin_tools(),
            max_output_chars=self.config.tool_output.max_chars,
            max_output_lines=self.config.tool_output.max_lines,
        )
        approver = APIApprover(self.bus, self.pending, self.always_allow)
        tools.approver = approver
        cwd = self._cwd_for(space)
        output_dir = home_dir() / TOOL_OUTPUT_DIRNAME
        system_prompt = build_system_prompt(self.config.system_prompt, cwd=cwd)
        return Agent(
            llm=llm,
            tools=tools,
            system_prompt=system_prompt,
            cwd=cwd,
            max_steps=self.config.max_steps,
            output_dir=output_dir,
            hidden_env=self.config.api_key_env_names(),
            approver=approver,
        )

    def _cwd_for(self, space: Space) -> Path:
        if space.kind == "agent" and space.agent and space.agent.cwd:
            return Path(space.agent.cwd).expanduser()
        return self.store._space_dir(space.id) / "tmp"

    async def _verify(self, space_id: str, session_id: str) -> None:
        space = self.store.get_space(space_id)
        if space is None or not space.verify or not space.verify.command:
            self.bus.publish(error_frame(session_id, "该空间没有配置验证命令"))
            return
        v = space.verify
        cwd = self._cwd_for(space)
        started = datetime.now(UTC).astimezone().isoformat(timespec="milliseconds")
        self.bus.publish(status_frame(session_id, "verifying"))
        try:
            # 用阻塞式 subprocess 跑在默认线程池里，避免 asyncio 子进程 watcher
            # 在非主线程的 loop 上不好使的问题
            result = await asyncio.to_thread(
                subprocess.run,
                v.command,
                shell=True,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=v.timeout,
            )
        except subprocess.TimeoutExpired:
            self.bus.publish(error_frame(session_id, f"验证命令超时（>{v.timeout}s）"))
            return
        except Exception as e:  # noqa: BLE001
            self.bus.publish(error_frame(session_id, f"验证命令执行失败：{e}"))
            return
        status = "passed" if result.returncode == 0 else "failed"
        verification = {
            "status": status,
            "command": v.command,
            "exit_code": result.returncode,
            "started_at": started,
            "finished_at": datetime.now(UTC).astimezone().isoformat(timespec="milliseconds"),
            "output": (result.stdout or "")[:2000],
            "source": "auto",
        }
        self.store.update_meta(space_id, session_id, verification=verification)
        self.bus.publish(verification_frame(session_id, verification))

    def _default_llm_factory(self, name: str, profile: Any) -> Any:
        from simpleagent.llm.client import LLMClient
        from simpleagent.trace import Tracer

        tracer = Tracer(
            home_dir() / "traces",
            "serve",
            enabled=self.config.trace.enabled,
            raw_chunks=self.config.trace.raw_chunks,
        )
        return LLMClient(name, profile, tracer=tracer)
