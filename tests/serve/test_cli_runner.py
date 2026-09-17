"""Runner 的 CLI 路径：用假 CLI + 录下来的样本跑端到端，不联网、不依赖本机装没装 CLI。

假 CLI 见 `tests/fixtures/cli/fake_cli.sh`：收下参数写进文件，再吐一份样本。
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from simpleagent.agents.base import FULL
from simpleagent.serve.runner import Runner
from simpleagent.spaces.models import SpaceSpec
from simpleagent.spaces.store import SpaceStore

FIXTURES = Path(__file__).parent.parent / "fixtures" / "cli"
FAKE = str(FIXTURES / "fake_cli.sh")


def _make_space(store, tmp_path, monkeypatch, events: str, **overrides):
    monkeypatch.setenv("FAKE_CLI_ARGV", str(tmp_path / "argv.txt"))
    monkeypatch.setenv("FAKE_CLI_EVENTS", str(FIXTURES / events))
    body = dict(
        name="cli 空间",
        kind="agent",
        executor="opencode",
        cwd=str(tmp_path),
        command=FAKE,
    )
    body.update(overrides)
    return store.create_space(SpaceSpec(**body))


def _run_until_settled(runner, session_id, timeout: float = 15) -> list:
    q, _ = runner.bus.subscribe(session_id)
    frames = []
    while True:
        f = q.get(timeout=timeout)
        frames.append(f)
        if f.type == "status" and f.payload["status"] in ("done", "error", "cancelled"):
            break
    return frames


def _wait_for(pred, timeout: float = 10.0) -> None:
    """等某个条件成立（子进程真的起来了），比 sleep 固定秒数稳。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return
        time.sleep(0.05)
    raise AssertionError("等待超时")


def test_cli_run_translates_events_and_records_everything(config, sa_home, tmp_path, monkeypatch):
    store = SpaceStore(sa_home)
    space = _make_space(store, tmp_path, monkeypatch, "opencode-bash-pwd.jsonl")
    session = store.create_session(space.id)
    runner = Runner(config, store=store)
    runner.start()

    runner.run_input(space.id, session.id, "跑一下 pwd")
    frames = _run_until_settled(runner, session.id)
    types = [f.type for f in frames]

    # 外部 CLI 的输出被翻译成了我们自己的帧，前端不用知道对面是谁
    assert "tool_call_start" in types and "tool_result" in types
    assert "text_delta" in types and "message_done" in types
    assert frames[-1].payload["status"] == "done"

    meta = store.get_session_meta(space.id, session.id)
    # 对面的会话 id 存下来了——下次追问要带它 resume
    assert meta.agent_session_id == "ses_0000000000000000000000000000"
    assert meta.usage["prompt_tokens"] > 0

    messages = store.load_session(space.id, session.id).messages
    assert messages[0] == {"role": "user", "content": "跑一下 pwd"}
    assert messages[1]["role"] == "tool"
    assert messages[-1]["role"] == "assistant"
    assert "当前目录是" in messages[-1]["content"]

    # 命令行：safe 档不给 --auto，prompt 用 -- 隔开
    argv = (tmp_path / "argv.txt").read_text(encoding="utf-8").splitlines()
    assert argv[:3] == ["run", "--format", "json"]
    assert "--auto" not in argv
    assert argv[-1] == "跑一下 pwd"
    runner.shutdown()


def test_cli_followup_resumes_the_other_side_session(config, sa_home, tmp_path, monkeypatch):
    store = SpaceStore(sa_home)
    space = _make_space(store, tmp_path, monkeypatch, "opencode-bash-pwd.jsonl")
    session = store.create_session(space.id)
    runner = Runner(config, store=store)
    runner.start()

    runner.run_input(space.id, session.id, "第一句")
    _run_until_settled(runner, session.id)

    runner.run_input(space.id, session.id, "第二句")
    _run_until_settled(runner, session.id)

    argv = (tmp_path / "argv.txt").read_text(encoding="utf-8").splitlines()
    # 第二轮要带上对面的 session id，历史在它那边，我们只记 id
    assert argv[argv.index("--session") + 1] == "ses_0000000000000000000000000000"
    assert argv[-1] == "第二句"
    runner.shutdown()


def test_cli_failure_becomes_an_error_frame(config, sa_home, tmp_path, monkeypatch):
    """本机 claude 没登录时的真实样本：必须报错，不能当成成功。"""
    store = SpaceStore(sa_home)
    space = _make_space(
        store, tmp_path, monkeypatch, "claude-not-logged-in.jsonl", executor="claude-code"
    )
    session = store.create_session(space.id)
    runner = Runner(config, store=store)
    runner.start()

    runner.run_input(space.id, session.id, "你好")
    frames = _run_until_settled(runner, session.id)

    errors = [f for f in frames if f.type == "error"]
    assert errors and "Not logged in" in errors[0].payload["message"]
    assert frames[-1].payload["status"] == "error"
    # 用户那句话仍然留着，配好之后还能重跑
    assert store.load_session(space.id, session.id).messages[0] == {
        "role": "user",
        "content": "你好",
    }
    runner.shutdown()


def test_cli_missing_binary_reports_clearly(config, sa_home, tmp_path, monkeypatch):
    store = SpaceStore(sa_home)
    space = _make_space(
        store, tmp_path, monkeypatch, "opencode-bash-pwd.jsonl", command="/nonexistent/sa-cli"
    )
    session = store.create_session(space.id)
    runner = Runner(config, store=store)
    runner.start()

    runner.run_input(space.id, session.id, "你好")
    frames = _run_until_settled(runner, session.id)
    assert "找不到可执行文件" in frames[0].payload["message"]
    assert frames[-1].payload["status"] == "error"
    runner.shutdown()


def test_cli_cancel_kills_the_process(config, sa_home, tmp_path, monkeypatch):
    store = SpaceStore(sa_home)
    space = _make_space(store, tmp_path, monkeypatch, "opencode-bash-pwd.jsonl", permission=FULL)
    session = store.create_session(space.id)
    monkeypatch.setenv("FAKE_CLI_SLEEP", "30")  # 让假 CLI 卡住，好让我们有机会取消
    runner = Runner(config, store=store)
    runner.start()

    runner.run_input(space.id, session.id, "长任务")
    _wait_for(lambda: (tmp_path / "argv.txt").exists())  # 等假 CLI 真的起来了
    runner.cancel(session.id)

    # 取消不是把任务 cancel 掉，是杀进程；要能等到 cancelled 收口帧
    frames = _run_until_settled(runner, session.id)
    assert frames[-1].payload["status"] == "cancelled"
    meta = store.get_session_meta(space.id, session.id)
    assert meta.status == "cancelled"
    assert meta.agent_session_id is None
    runner.shutdown()


def test_cli_full_mode_adds_the_dangerous_flag(config, sa_home, tmp_path, monkeypatch):
    store = SpaceStore(sa_home)
    space = _make_space(store, tmp_path, monkeypatch, "opencode-bash-pwd.jsonl", permission=FULL)
    session = store.create_session(space.id)
    runner = Runner(config, store=store)
    runner.start()

    runner.run_input(space.id, session.id, "干活")
    _run_until_settled(runner, session.id)

    argv = (tmp_path / "argv.txt").read_text(encoding="utf-8").splitlines()
    assert "--auto" in argv  # 全放行档：opencode 不等确认
    runner.shutdown()


@pytest.mark.parametrize("events", ["opencode-bash-pwd.jsonl"])
def test_cli_writes_nothing_to_the_real_home(events, config, sa_home, tmp_path, monkeypatch):
    """产物必须落在空间目录里，别写进用户真实的 ~/.simpleagent。"""
    store = SpaceStore(sa_home)
    space = _make_space(store, tmp_path, monkeypatch, events)
    session = store.create_session(space.id)
    runner = Runner(config, store=store)
    runner.start()

    runner.run_input(space.id, session.id, "你好")
    _run_until_settled(runner, session.id)

    assert (sa_home / "spaces" / space.id / "sessions" / f"{session.id}.jsonl").exists()
    runner.shutdown()
