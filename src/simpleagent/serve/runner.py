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
import sys
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
from simpleagent.panel.store import PanelStore
from simpleagent.panel.summary import one_line, summarize
from simpleagent.permissions import Policy
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
from simpleagent.spaces.verify import changed_since, fingerprint
from simpleagent.tools import ToolRegistry, builtin_tools

# (profile_name, Profile) -> LLM 实例；测试时注入 FakeLLM
LLMFactory = Callable[[str, Any], Any]


def _log_future_error(fut: Any) -> None:
    """兜底日志：协程里漏出去的异常不取出来的话，asyncio 会一声不响地吞掉。"""
    if fut.cancelled():
        return
    exc = fut.exception()
    if exc is not None:
        print(f"[runner] 未处理的异常：{type(exc).__name__}: {exc}", file=sys.stderr)


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
        self.panel = PanelStore()
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
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        # 兜底：协程里漏掉的异常如果不取出来，asyncio 会静默吞掉，排查时毫无痕迹
        fut.add_done_callback(_log_future_error)

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
        try:
            # 先落用户消息再构造 agent：起不来的时候（没配 key、profile 不存在、外部 CLI
            # 还没接入）也要把用户说的这句留下来，否则刷新页面它就不见了。
            self.store.append_message(space_id, session_id, {"role": "user", "content": user_input})
            self.store.update_meta(space_id, session_id, status="running")
            agent = self._build_agent(space, session)
            self._agents[session_id] = agent
        except Exception as e:  # noqa: BLE001
            # 起不来必须让客户端知道：这一段在原来是在 try 之外，异常会被 asyncio future
            # 吞掉，表现是「发了消息没有任何反应，且永远停在运行中」。典型触发：没配 API key、
            # profile 不存在、cwd 不存在。CancelledError 继承自 BaseException，不会被这里吃掉。
            self.store.update_meta(space_id, session_id, status="error")
            self.bus.publish(error_frame(session_id, f"启动失败：{type(e).__name__}: {e}"))
            self.bus.publish(status_frame(session_id, "error"))
            self._agents.pop(session_id, None)
            return

        task = asyncio.current_task()
        if task is not None:
            self._tasks[session_id] = task
        try:
            async for event in agent.run(session, user_input):
                self._on_event(space_id, session_id, event)
        except asyncio.CancelledError:
            self.bus.publish(status_frame(session_id, "cancelled"))
            self._finalize(space_id, session_id, session, "cancelled")
            raise
        except Exception as e:  # noqa: BLE001  任何异常都转成 error 帧并落盘状态
            self.bus.publish(error_frame(session_id, f"{type(e).__name__}: {e}"))
            self._finalize(space_id, session_id, session, "error")
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
            self._maybe_mark_stale(space_id, session_id, event)
        self.bus.publish(event_to_frame(event, session_id))

    def _maybe_mark_stale(self, space_id: str, session_id: str, event: ToolResult) -> None:
        """写工具真的改动了文件之后，已通过的验证降级为 `stale`。

        用指纹而不是「跑过写工具就 stale」：bash 也是写工具，但 `ls` 之类并不改文件，
        指纹没变就不该让验证结果失效。
        """
        if event.is_error:
            return
        agent = self._agents.get(session_id)
        if agent is None or agent.tools.is_readonly(event.name):
            return
        meta = self.store.get_session_meta(space_id, session_id)
        if meta is None or meta.verification.status != "passed":
            return
        space = self.store.get_space(space_id)
        if space is None or not changed_since(meta.verification.fingerprint, self.cwd_for(space)):
            return
        verification = meta.verification.to_dict()
        verification["status"] = "stale"
        self.store.update_meta(space_id, session_id, verification=verification)
        self.bus.publish(verification_frame(session_id, verification))

    def _finalize(self, space_id: str, session_id: str, session: Session, status: str) -> None:
        self.store.update_meta(space_id, session_id, status=status, usage=session.usage.__dict__)
        # 收口处统一广播状态：正常结束时没有 error 帧，客户端要靠这帧把「运行中」切回空闲
        self.bus.publish(status_frame(session_id, status))
        if status in ("done", "error", "cancelled"):
            self._notify(space_id, session_id, session, status)

    def _notify(self, space_id: str, session_id: str, session: Session, status: str) -> None:
        """跑完往控制面板的消息里落一条：否则用户只能一直盯着页面才知道结果。"""
        space = self.store.get_space(space_id)
        meta = self.store.get_session_meta(space_id, session_id)
        title = (meta.title if meta else "") or "会话"
        name = space.name if space else space_id
        if status == "done":
            level, head, body = "success", "完成", one_line(summarize(session.messages, meta))
        elif status == "error":
            level, head, body = "error", "失败", "运行出错，去那个会话的日志 tab 看原因"
        else:
            level, head, body = "warn", "已取消", "被手动停止"
        self.panel.add_message(
            source="system",
            title=f"{name} · {head}：{title}",
            body=body,
            level=level,
            ref={"space_id": space_id, "session_id": session_id},
        )

    def _build_agent(self, space: Space, session: Session) -> Agent:
        # 选了外部 CLI 就必须显式失败，**不能**静默回退到内置 loop：那会让用户以为
        # 在跑 claude code。外部 CLI 的启动器规划在 M8，这里只是把话说清楚。
        if space.executor != "simpleagent":
            raise RuntimeError(
                f"执行者 {space.executor} 还没接入（规划在 M8），"
                "先把这个空间的执行者改成内置 SimpleAgent"
            )
        if space.profile not in self.config.profiles:
            raise RuntimeError(
                f"空间用的 profile「{space.profile}」不在 config.toml 里，"
                f"可选：{'、'.join(sorted(self.config.profiles))}"
            )
        profile = self.config.profiles[space.profile]
        llm = self.llm_factory(space.profile, profile)
        tools = ToolRegistry(
            builtin_tools(),
            max_output_chars=self.config.tool_output.max_chars,
            max_output_lines=self.config.tool_output.max_lines,
        )
        approver = APIApprover(self.bus, self.pending, self.always_allow)
        tools.approver = approver
        cwd = self.cwd_for(space)
        # 客户端模式也走同一套权限判定：越界和危险命令先被拦掉，剩下的才去问客户端
        tools.policy = Policy(cwd)
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
        )

    def cwd_for(self, space: Space) -> Path:
        """空间的工作目录：有 cwd 用它，没有就用 spaces/<id>/tmp。

        HTTP 层（文件树、验证）也要用同一个口径，所以是公开方法。
        """
        if space.cwd:
            return Path(space.cwd).expanduser()
        return self.store._space_dir(space.id) / "tmp"

    async def _verify(self, space_id: str, session_id: str) -> None:
        space = self.store.get_space(space_id)
        if space is None or not space.verify or not space.verify.command:
            self.bus.publish(error_frame(session_id, "该空间没有配置验证命令"))
            return
        v = space.verify
        cwd = self.cwd_for(space)
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
            "output": (result.stdout or "")[-2000:],
            # 只在通过时冻结指纹：失败的结果本来就要重跑，没有「失效」一说
            "fingerprint": fingerprint(cwd) if status == "passed" else None,
            "source": "auto",
        }
        self.store.update_meta(space_id, session_id, verification=verification)
        self.bus.publish(verification_frame(session_id, verification))
        if status == "failed":
            self.panel.add_message(
                source="system",
                title=f"{space.name} · 验证未通过",
                body=f"{v.command}　退出码 {result.returncode}",
                level="error",
                ref={"space_id": space_id, "session_id": session_id},
            )

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
