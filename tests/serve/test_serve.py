"""W2 测试：事件总线、审批回转、Runner（FakeLLM）、以及 HTTP + SSE 集成。

全部不联网：用 FakeLLM 取代真实模型，用 bus.subscribe 直接收帧，或用标准库 urllib 打真实 HTTP。
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
import urllib.request
from typing import Any

from simpleagent.llm.fake import FakeLLM
from simpleagent.permissions import ApprovalDecision, ApprovalRequest
from simpleagent.serve.approval import APIApprover, PendingApprovals
from simpleagent.serve.bus import EventBus, Frame
from simpleagent.serve.runner import Runner
from simpleagent.spaces.models import SpaceSpec
from simpleagent.spaces.store import SpaceStore


def _fake_factory(script: list[dict]) -> Any:
    """llm_factory：忽略 profile，返回吃固定脚本的 FakeLLM。"""

    def factory(name: str, profile: Any) -> FakeLLM:
        return FakeLLM(list(script))

    return factory


# --------------------------------------------------------------- 1. 总线顺序与重放
def test_bus_order_and_replay():
    bus = EventBus()
    q, replay = bus.subscribe("se1")
    assert replay == []
    for i in range(3):
        bus.publish(Frame("se1", "tick", {"i": i}))
    got = [q.get(timeout=2).seq for _ in range(3)]
    assert got == [1, 2, 3]

    # 新的订阅者带 Last-Event-ID=1 应只重放 seq 2,3（重放帧在返回的列表里，不进队列）
    q2, replay2 = bus.subscribe("se1", last_event_id="1")
    assert [f.seq for f in replay2] == [2, 3]
    bus.unsubscribe("se1", q2)
    bus.unsubscribe("se1", q)


# --------------------------------------------------------------- 2. 审批挂起 → 恢复
async def test_approval_roundtrip():
    bus = EventBus()
    pending = PendingApprovals()
    approver = APIApprover(bus, pending)

    async def requester() -> ApprovalDecision:
        return await approver.request(
            ApprovalRequest(session_id="se1", tool_name="bash", arguments='{"command":"rm -rf /"}')
        )

    task = asyncio.create_task(requester())  # noqa: F821
    await asyncio.sleep(0.02)
    # 应当已经推了一帧 approval_request，且 Future 还挂着
    assert pending.pending_ids()
    # 解析审批
    pending.resolve(pending.pending_ids()[0], ApprovalDecision(allow=True, always=True))
    decision = await task
    assert decision.allow is True
    assert decision.always is True

    # always 生效：同 session 同工具再次请求直接放行，不再推帧
    decision2 = await approver.request(
        ApprovalRequest(session_id="se1", tool_name="bash", arguments='{"x":1}')
    )
    assert decision2.allow is True


# --------------------------------------------------------------- 3. Runner + FakeLLM 流式
def test_runner_fake_llm_stream(config, sa_home):
    store = SpaceStore(sa_home)
    space = store.create_space(SpaceSpec(name="t", kind="generic", profile="a"))
    session = store.create_session(space.id)
    runner = Runner(config, store=store, llm_factory=_fake_factory([{"content": "你好，世界"}]))
    runner.start()
    q, _ = runner.bus.subscribe(session.id)

    runner.run_input(space.id, session.id, "hi")

    frames = []
    while True:
        f = q.get(timeout=5)
        frames.append(f)
        if f.type == "message_done":
            break

    assert any(f.type == "text_delta" for f in frames)
    assert frames[-1].type == "message_done"
    # 落盘：user + assistant 两条消息
    messages = store.load_session(space.id, session.id).messages
    assert len(messages) == 2
    assert messages[1]["role"] == "assistant"
    assert "你好" in messages[1]["content"]
    runner.shutdown()


# --------------------------------------------------------------- 4. 审批流：挂起 → POST → 继续
def test_runner_approval_flow(config, sa_home):
    store = SpaceStore(sa_home)
    space = store.create_space(SpaceSpec(name="t", kind="generic", profile="a"))
    session = store.create_session(space.id)
    script = [
        {
            "tool_calls": [
                {"id": "c1", "name": "write_file", "arguments": {"path": "x.txt", "content": "hi"}}
            ]
        },
        {"content": "写完了"},
    ]
    runner = Runner(config, store=store, llm_factory=_fake_factory(script))
    runner.start()
    q, _ = runner.bus.subscribe(session.id)

    runner.run_input(space.id, session.id, "写个文件")

    # 读到 approval_request 为止，此时不应有 tool_result
    frames: list[Frame] = []
    while True:
        f = q.get(timeout=5)
        frames.append(f)
        if f.type == "approval_request":
            break
    assert frames[-1].type == "approval_request"
    assert not any(f.type == "tool_result" for f in frames)
    ap_id = frames[-1].payload["approval_id"]

    # 允许后继续，直到下一个 message_done
    runner.approve(ap_id, "allow")
    while True:
        f = q.get(timeout=5)
        frames.append(f)
        if f.type == "message_done":
            break
    assert any(f.type == "tool_result" for f in frames)
    assert any(f.type == "text_delta" for f in frames)
    # 文件确实被写了
    written = (store._space_dir(space.id) / "tmp" / "x.txt").read_text(encoding="utf-8")
    assert written == "hi"
    runner.shutdown()


# --------------------------------------------------------------- 5. 验证命令
def test_runner_verify(config, sa_home):
    store = SpaceStore(sa_home)
    space = store.create_space(
        SpaceSpec(name="t", kind="generic", profile="a", verify_command="true")
    )
    session = store.create_session(space.id)
    runner = Runner(config, store=store, llm_factory=_fake_factory([{"content": "ok"}]))
    runner.start()
    q, _ = runner.bus.subscribe(session.id)

    runner.verify(space.id, session.id)
    frames = []
    while True:
        f = q.get(timeout=5)
        frames.append(f)
        if f.type == "verification":
            break
    assert frames[-1].type == "verification"
    assert frames[-1].payload["status"] == "passed"
    runner.shutdown()


# --------------------------------------------------------------- 6. HTTP + SSE 集成
def test_serve_http_sse(config, sa_home):
    store = SpaceStore(sa_home)
    space = store.create_space(SpaceSpec(name="t", kind="generic", profile="a"))
    session = store.create_session(space.id)

    from simpleagent.serve.app import make_server

    httpd = make_server(
        config, host="127.0.0.1", port=0, llm_factory=_fake_factory([{"content": "来自服务端"}])
    )
    port = httpd.server_address[1]
    srv = threading.Thread(target=httpd.serve_forever, daemon=True)
    srv.start()
    base = f"http://127.0.0.1:{port}"

    collected: list[dict] = []
    stop = threading.Event()

    def reader() -> None:
        try:
            with urllib.request.urlopen(
                f"{base}/api/sessions/{session.id}/events", timeout=10
            ) as resp:
                for raw in resp:
                    line = raw.decode("utf-8").strip()
                    if line.startswith("data:"):
                        payload = json.loads(line[5:].strip())
                        collected.append(payload)
                        if payload.get("type") == "message_done":
                            break
        except Exception:
            pass
        finally:
            stop.set()

    rt = threading.Thread(target=reader, daemon=True)
    rt.start()
    time.sleep(0.1)  # 确保 SSE 已订阅

    req = urllib.request.Request(
        f"{base}/api/sessions/{session.id}/input",
        data=json.dumps({"text": "hi"}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    resp = urllib.request.urlopen(req, timeout=5)
    assert resp.status == 202

    rt.join(timeout=8)
    assert any(c.get("type") == "text_delta" for c in collected)
    assert any(c.get("type") == "message_done" for c in collected)

    # 审批列表接口也通
    with urllib.request.urlopen(f"{base}/api/approvals", timeout=5) as r:
        assert r.status == 200

    httpd.shutdown()
    srv.join(timeout=3)
