"""M3 会话持久化测试：JSONL 的追加、撤回、恢复，以及列表/最近会话。

重点是「中断后修好的历史」重放出来必须和当时内存里的一模一样——这是能不能
接着上次继续聊的前提。全部不联网。"""

from __future__ import annotations

from pathlib import Path

from simpleagent.agent.loop import Agent
from simpleagent.agent.session import Session, SessionStore
from simpleagent.events import Usage
from simpleagent.llm.fake import FakeLLM
from simpleagent.tools import ToolRegistry, builtin_tools


def store_at(tmp_path: Path) -> SessionStore:
    return SessionStore(tmp_path / "sessions")


# ------------------------------------------------------------------ 1. 读写
def test_messages_survive_roundtrip(tmp_path: Path):
    store = store_at(tmp_path)
    session = store.start(Session("s1"), profile="a", cwd="/work")
    session.add({"role": "user", "content": "你好"})
    session.add({"role": "assistant", "content": "你好！"})
    session.record_stats(Usage(prompt_tokens=10, completion_tokens=4), requests=1)

    restored = store.load("s1")
    assert restored is not None
    assert restored.messages == [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好！"},
    ]
    assert restored.requests == 1
    assert restored.usage.prompt_tokens == 10
    assert restored.title() == "你好"
    # 恢复出来的会话继续写，追加到同一个文件而不是覆盖
    restored.add({"role": "user", "content": "继续"})
    assert len(store.load("s1").messages) == 3


def test_truncate_is_replayed(tmp_path: Path):
    """撤回（Ctrl+C 后的修复）要能在重放时还原，否则下次请求会被 API 拒绝。"""
    store = store_at(tmp_path)
    session = store.start(Session("s2"))
    session.add({"role": "user", "content": "q1"})
    session.add({"role": "assistant", "content": "a1"})
    session.truncate(1)
    session.add({"role": "user", "content": "q2"})

    assert store.load("s2").messages == [
        {"role": "user", "content": "q1"},
        {"role": "user", "content": "q2"},
    ]


def test_missing_session_returns_none(tmp_path: Path):
    assert store_at(tmp_path).load("不存在") is None


def test_broken_line_is_skipped(tmp_path: Path):
    """写了一半的行（进程被 kill）不能让整个会话读不出来。"""
    store = store_at(tmp_path)
    session = store.start(Session("s3"))
    session.add({"role": "user", "content": "好的"})
    with store.path("s3").open("a", encoding="utf-8") as f:
        f.write('{"op": "append", "mess')  # 截断的坏行
    session.add({"role": "assistant", "content": "收到"})

    assert len(store.load("s3").messages) == 2


def test_write_failure_does_not_break_the_session(tmp_path: Path):
    """持久化失败只是丢存档，对话必须照样能进行。"""
    store = SessionStore(tmp_path / "sessions")
    session = store.start(Session("s4"))
    file = store.path("s4")
    file.write_text("")
    file.chmod(0o444)  # 只读：追加会失败
    try:
        session.add({"role": "user", "content": "还在"})
        assert session.messages[-1]["content"] == "还在"
    finally:
        file.chmod(0o644)


# ------------------------------------------------------------------ 2. 列表
def test_list_is_sorted_by_recent(tmp_path: Path):
    store = store_at(tmp_path)
    old = store.start(Session("old"))
    old.add({"role": "user", "content": "早些时候的问题"})
    store.path("old").touch()
    new = store.start(Session("new"))
    new.add({"role": "user", "content": "一个特别特别长的提问" + "啊" * 60})
    new.record_stats(requests=3)

    assert store.latest() == "new"
    rows = store.list()
    assert [r.id for r in rows] == ["new", "old"]
    assert rows[0].requests == 3
    assert rows[0].title.endswith("…")  # 超长标题被截断
    assert len(rows[0].title) <= 41
    assert store.list(limit=1)[0].id == "new"


def test_delete(tmp_path: Path):
    store = store_at(tmp_path)
    store.start(Session("s5"))
    assert store.delete("s5") is True
    assert store.load("s5") is None


# ------------------------------------------------------------------ 3. 端到端
def _make_agent(tmp_path: Path, llm: FakeLLM) -> Agent:
    return Agent(
        llm,
        ToolRegistry(builtin_tools()),
        "系统提示",
        cwd=tmp_path,
    )


async def test_agent_history_is_persisted_and_resumable(tmp_path: Path):
    """跑两轮，再用新的 Session 实例加载回来接着聊：发给模型的历史必须完整。"""
    store = store_at(tmp_path)
    session = store.start(Session("e2e"), profile="a", cwd=str(tmp_path))
    agent = _make_agent(tmp_path, FakeLLM([{"content": "第一轮回答"}, "第二轮回答"]))

    async for _ in agent.run(session, "第一个问题"):
        pass
    async for _ in agent.run(session, "第二个问题"):
        pass
    assert len(session.messages) == 4

    restored = store.load("e2e")
    assert restored is not None
    assert [m["content"] for m in restored.messages] == [
        "第一个问题",
        "第一轮回答",
        "第二个问题",
        "第二轮回答",
    ]

    second_llm = FakeLLM(["第三轮"])
    agent.llm = second_llm
    async for _ in agent.run(restored, "第三个问题"):
        pass
    sent = second_llm.requests[0]["messages"]
    assert sent[0]["role"] == "system"
    assert [m["content"] for m in sent[1:]] == [
        "第一个问题",
        "第一轮回答",
        "第二个问题",
        "第二轮回答",
        "第三个问题",
    ]
