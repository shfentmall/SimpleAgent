import asyncio
import os
import sys
import time
from pathlib import Path

import pytest

from simpleagent.tools.base import ToolContext, ToolError
from simpleagent.tools.bash import BashArgs, bash

bash_module = sys.modules[
    "simpleagent.tools.bash"
]  # 包属性 bash 被 Tool 对象覆盖了，模块要从这里拿


def ctx(cwd: Path) -> ToolContext:
    return ToolContext(cwd=cwd)


async def run(command: str, workdir: Path, **kwargs) -> str:
    """workdir 是工具的工作目录；kwargs 里还能传工具的 cwd / timeout 参数。"""
    return await bash.fn(BashArgs(command=command, **kwargs), ctx(workdir))


async def test_stdout_and_exit_code(tmp_path: Path):
    assert await run("echo hello", tmp_path) == "$ echo hello\nhello\n[退出码 0]"


async def test_stderr_is_merged_into_output(tmp_path: Path):
    text = await run("echo bad >&2; exit 3", tmp_path)
    assert "bad" in text
    assert "[退出码 3]" in text


async def test_no_output(tmp_path: Path):
    assert "(无输出)" in await run("true", tmp_path)


def process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


async def wait_until_dead(pid: int, seconds: float = 3) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not process_alive(pid):
            return True
        await asyncio.sleep(0.05)
    return False


async def test_timeout_kills_process(tmp_path: Path):
    start = time.monotonic()
    text = await run("echo before; sleep 30; echo DONE", tmp_path, timeout=1)
    # sh 启动的 sleep 占着输出管道：只杀 sh 的话要等 sleep 自己跑完 30 秒
    assert time.monotonic() - start < 5
    # before 之后直接就是超时提示：后面的命令没机会执行
    assert "before\n[超过 1s 已终止命令及其子进程（退出码 -9）" in text


async def test_timeout_kills_background_children(tmp_path: Path):
    await run("sleep 30 & echo $! > child.pid; wait", tmp_path, timeout=1)
    child = int((tmp_path / "child.pid").read_text())
    assert await wait_until_dead(child)


async def test_cancel_kills_running_command(tmp_path: Path):
    """Ctrl+C 取消工具调用时，命令不能留在后台继续跑。"""
    task = asyncio.create_task(run("sleep 30 & echo $! > child.pid; wait", tmp_path, timeout=600))
    pid_file = tmp_path / "child.pid"
    while not pid_file.exists() or not pid_file.read_text().strip():
        await asyncio.sleep(0.02)
    child = int(pid_file.read_text())
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await wait_until_dead(child)


async def test_api_key_env_is_hidden_from_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SA_TEST_SECRET_KEY", "secret-value")
    monkeypatch.setenv("SA_TEST_VISIBLE", "visible-value")
    ctx = ToolContext(cwd=tmp_path, hidden_env=frozenset({"SA_TEST_SECRET_KEY"}))
    text = await bash.fn(BashArgs(command="env"), ctx)
    assert "secret-value" not in text
    assert "visible-value" in text  # 其他环境变量照常继承


async def test_output_over_limit_stops_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(bash_module, "MAX_OUTPUT_BYTES", 1000)
    start = time.monotonic()
    text = await run("yes", tmp_path, timeout=30)  # yes 会一直输出，不停下就要等满 30 秒
    assert time.monotonic() - start < 5
    assert "[输出超过 1,000 字节，已停止读取并终止命令" in text
    assert len(text) < 2000


async def test_cwd_option(tmp_path: Path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "only_here.txt").write_text("found")
    text = await run("cat only_here.txt", tmp_path, cwd="sub")
    assert "found" in text
    assert "[退出码 0]" in text


async def test_missing_cwd(tmp_path: Path):
    with pytest.raises(ToolError, match="工作目录不存在"):
        await run("ls", tmp_path, cwd="nope")


async def test_command_waiting_on_stdin_returns_immediately(tmp_path: Path):
    # 没有 stdin：cat 立刻结束，不会把 agent 挂住
    text = await asyncio.wait_for(run("cat", tmp_path), timeout=5)
    assert "[退出码 0]" in text
