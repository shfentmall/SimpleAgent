"""bash：执行 shell 命令。

M2 只做执行本身：超时或中断就杀掉整个进程组、合并 stdout 和 stderr、限制读取的输出量。
危险命令识别、工作目录边界、是否要问用户，都是 M3 权限系统的事（bash 标了
readonly=False，到时候在注册表里就能拿到这个标记）。

子进程的环境变量会去掉 ctx.hidden_env（配置里各 profile 的 api_key_env）：
.env 里的 key 本来就不进 os.environ，但用户自己 export 的 key 会被子进程继承。
"""

from __future__ import annotations

import asyncio
import os
import signal

from pydantic import BaseModel, ConfigDict, Field

from simpleagent.tools.base import ToolContext, ToolError, tool

# 最多读取这么多字节的输出，再多就停止读取并终止命令：输出先放在内存里，
# `yes` 这类命令 60 秒能攒出好几 GB。注册表还会再截断到回给模型的长度，完整内容落盘
MAX_OUTPUT_BYTES = 10 * 1024 * 1024
# 杀掉进程组之后最多再等这么久让管道关闭（个别进程自己脱离了进程组，还占着输出管道）
KILL_GRACE_SECONDS = 2

DESCRIPTION = (
    "执行一条 shell 命令，返回合并后的输出（stdout + stderr）和退出码。"
    "有 timeout 上限，超时会终止命令（包括它启动的子进程）并返回已经产生的部分输出。"
    "默认在工作目录里执行，可以用 cwd 参数换目录（相对工作目录解析）。"
    "不能用它做交互输入（没有 stdin），也不要用它改文件——改文件用 write_file / edit_file。"
)


class BashArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: str = Field(..., description="要执行的 shell 命令")
    timeout: int = Field(60, ge=1, le=600, description="超时秒数，超时后杀掉进程")
    cwd: str | None = Field(None, description="在哪个目录执行，默认工作目录")


class _Capture:
    """边读边存输出；超过上限就停下。放在对象里，读取被取消时已经读到的部分还在。"""

    def __init__(self, limit: int):
        self.buffer = bytearray()
        self.limit = limit
        self.overflow = False

    async def pump(self, stream: asyncio.StreamReader) -> None:
        while chunk := await stream.read(64 * 1024):
            room = self.limit - len(self.buffer)
            self.buffer += chunk[:room]
            if len(chunk) > room:
                self.overflow = True
                return

    def text(self) -> str:
        return self.buffer.decode("utf-8", errors="replace")


def _kill_group(process: asyncio.subprocess.Process) -> None:
    """杀掉整个进程组：只杀 /bin/sh 的话，它启动的子进程还会占着输出管道继续跑。"""
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass  # 已经全部退出了


@tool(name="bash", description=DESCRIPTION, readonly=False)
async def bash(args: BashArgs, ctx: ToolContext) -> str:
    cwd = ctx.resolve(args.cwd) if args.cwd else ctx.cwd
    if not cwd.is_dir():
        raise ToolError(f"工作目录不存在：{cwd}")
    process = await asyncio.create_subprocess_shell(
        args.command,
        cwd=cwd,
        env=ctx.subprocess_env(),
        stdin=asyncio.subprocess.DEVNULL,  # 没有 stdin：命令卡在等待输入时不会把 agent 挂住
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,  # 合并到 stdout，顺序才是对的
        start_new_session=True,  # 自成一个进程组，超时或中断时能连同子进程一起杀掉
    )
    assert process.stdout is not None
    capture = _Capture(MAX_OUTPUT_BYTES)

    async def run() -> int:
        await capture.pump(process.stdout)
        if capture.overflow:
            _kill_group(process)
        return await process.wait()

    # shield：wait_for 超时只放弃等待，run() 继续跑，杀掉进程后还能收尾、拿到部分输出
    task = asyncio.ensure_future(run())
    timed_out = False
    try:
        returncode = await asyncio.wait_for(asyncio.shield(task), args.timeout)
    except TimeoutError:
        timed_out = True
        _kill_group(process)
        try:
            returncode = await asyncio.wait_for(task, KILL_GRACE_SECONDS)
        except TimeoutError:
            returncode = process.returncode
    except BaseException:
        # Ctrl+C（CancelledError）：不能把命令留在后台继续跑
        _kill_group(process)
        task.cancel()
        raise

    body = capture.text().rstrip() or "(无输出)"
    if timed_out:
        note = (
            f"[超过 {args.timeout}s 已终止命令及其子进程（退出码 {returncode}）；"
            "上面是终止前产生的部分输出]"
        )
    elif capture.overflow:
        note = (
            f"[输出超过 {MAX_OUTPUT_BYTES:,} 字节，已停止读取并终止命令（退出码 {returncode}）；"
            "上面是前面的部分]"
        )
    else:
        note = f"[退出码 {returncode}]"
    return f"$ {args.command}\n{body}\n{note}"
