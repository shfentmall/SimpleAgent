import asyncio
from pathlib import Path

import httpx2
import openai
import pytest
from pydantic import BaseModel

from simpleagent.agent.loop import INTERRUPTED_RESULT, Agent
from simpleagent.agent.session import Session
from simpleagent.events import MaxStepsReached, MessageDone, TextDelta, ToolCallStart, ToolResult
from simpleagent.llm.fake import FakeLLM, Script
from simpleagent.tools import Tool, ToolContext, ToolRegistry, builtin_tools, tool


class NoArgs(BaseModel):
    pass


@tool(name="slow", description="很久才返回")
async def slow(args: NoArgs, ctx: ToolContext) -> str:
    await asyncio.sleep(10)
    return "不会返回"


def make_agent(
    tmp_path: Path,
    responses: list[Script],
    tools: list[Tool] | None = None,
    max_steps: int = 20,
) -> tuple[Agent, FakeLLM]:
    llm = FakeLLM(responses)
    registry = ToolRegistry(builtin_tools() if tools is None else tools)
    return Agent(llm, registry, "系统提示", cwd=tmp_path, max_steps=max_steps), llm


async def collect(agent: Agent, session: Session, text: str = "hi") -> list:
    return [event async for event in agent.run(session, text)]


def list_dir_call(arguments: dict | str | None = None, call_id: str = "call_0") -> dict:
    return {"id": call_id, "name": "list_dir", "arguments": {} if arguments is None else arguments}


async def test_tool_call_round_trip(tmp_path: Path):
    (tmp_path / "hello.txt").write_text("hi")
    agent, llm = make_agent(
        tmp_path,
        [{"content": "我看看", "tool_calls": [list_dir_call({"depth": 1})]}, "只有 hello.txt"],
    )
    session = Session("s")
    events = await collect(agent, session, "当前目录有什么？")

    assert [type(e) for e in events if not isinstance(e, TextDelta)] == [
        MessageDone,
        ToolCallStart,
        ToolResult,
        MessageDone,
    ]
    start = next(e for e in events if isinstance(e, ToolCallStart))
    assert (start.call_id, start.name, start.arguments) == ("call_0", "list_dir", '{"depth": 1}')

    assert session.messages == [
        {"role": "user", "content": "当前目录有什么？"},
        {
            "role": "assistant",
            "content": "我看看",
            "tool_calls": [
                {
                    "id": "call_0",
                    "type": "function",
                    "function": {"name": "list_dir", "arguments": '{"depth": 1}'},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_0",
            "content": f"{tmp_path.resolve()}\nhello.txt 2B",
        },
        {"role": "assistant", "content": "只有 hello.txt"},
    ]
    # 第二次请求带上了工具结果，每次请求都带着 tools
    assert llm.requests[1]["messages"] == [
        {"role": "system", "content": "系统提示"},
        *session.messages[:3],
    ]
    assert [t["function"]["name"] for t in llm.requests[0]["tools"]] == ["list_dir"]
    assert llm.requests[0]["tools"] == llm.requests[1]["tools"]
    assert session.requests == 2


async def test_invalid_arguments_are_returned_to_model(tmp_path: Path):
    agent, llm = make_agent(
        tmp_path, [{"tool_calls": [list_dir_call('{"depth": ')]}, "参数写错了，我重试"]
    )
    session = Session("s")
    events = await collect(agent, session)

    result = next(e for e in events if isinstance(e, ToolResult))
    assert result.is_error
    assert "参数不是合法 JSON" in result.content
    assert llm.requests[1]["messages"][-1] == result.as_message()
    assert session.messages[-1] == {"role": "assistant", "content": "参数写错了，我重试"}


async def test_multiple_calls_keep_order(tmp_path: Path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "x.txt").write_text("")
    calls = [list_dir_call({"path": "a"}, "c_a"), list_dir_call({"path": "b"}, "c_b")]
    agent, _ = make_agent(tmp_path, [{"tool_calls": calls}, "好了"])
    session = Session("s")
    events = await collect(agent, session)

    assert [e.call_id for e in events if isinstance(e, ToolResult)] == ["c_a", "c_b"]
    tool_messages = [m for m in session.messages if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_messages] == ["c_a", "c_b"]
    assert tool_messages[0]["content"].endswith("(空目录)")
    assert tool_messages[1]["content"].endswith("x.txt 0B")


async def test_multiple_calls_run_concurrently(tmp_path: Path):
    both_started = asyncio.Event()
    running = 0

    @tool(name="wait", description="等另一个调用也开始")
    async def wait(args: NoArgs, ctx: ToolContext) -> str:
        nonlocal running
        running += 1
        if running == 2:
            both_started.set()
        # 依次执行的话，第一个调用永远等不到第二个开始，会超时
        await asyncio.wait_for(both_started.wait(), timeout=1)
        return "ok"

    calls = [{"id": f"c{i}", "name": "wait", "arguments": {}} for i in range(2)]
    agent, _ = make_agent(tmp_path, [{"tool_calls": calls}, "done"], tools=[wait])
    events = await collect(agent, Session("s"))
    assert [e.content for e in events if isinstance(e, ToolResult)] == ["ok", "ok"]


async def test_max_steps_stops_loop(tmp_path: Path):
    agent, llm = make_agent(
        tmp_path,
        [{"tool_calls": [list_dir_call()]}, {"tool_calls": [list_dir_call()]}],
        max_steps=2,
    )
    session = Session("s")
    events = await collect(agent, session)

    assert events[-1] == MaxStepsReached(2)
    assert len(llm.requests) == 2
    assert [m["role"] for m in session.messages] == [
        "user",
        "assistant",
        "tool",
        "assistant",
        "tool",
    ]


async def test_cancel_during_tool_fills_missing_results(tmp_path: Path):
    calls = [{"id": "c1", "name": "slow", "arguments": {}}, {"id": "c2", "name": "slow"}]
    agent, _ = make_agent(tmp_path, [{"tool_calls": calls}], tools=[slow])
    session = Session("s")
    seen: list = []

    async def consume() -> None:
        async for event in agent.run(session, "hi"):
            seen.append(event)

    task = asyncio.create_task(consume())
    while not any(isinstance(e, ToolCallStart) for e in seen):
        await asyncio.sleep(0.005)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert session.messages[2:] == [
        {"role": "tool", "tool_call_id": "c1", "content": INTERRUPTED_RESULT},
        {"role": "tool", "tool_call_id": "c2", "content": INTERRUPTED_RESULT},
    ]


async def test_api_error_on_first_request_rolls_back_user_message(tmp_path: Path):
    error = openai.APIConnectionError(request=httpx2.Request("POST", "http://fake.invalid/v1"))
    agent, _ = make_agent(tmp_path, [error])
    session = Session("s")
    with pytest.raises(openai.APIConnectionError):
        await collect(agent, session)
    assert session.messages == []


async def test_api_error_after_tool_step_keeps_tool_results(tmp_path: Path):
    error = openai.APIConnectionError(request=httpx2.Request("POST", "http://fake.invalid/v1"))
    agent, llm = make_agent(tmp_path, [{"tool_calls": [list_dir_call()]}, error, "继续好了"])
    session = Session("s")
    with pytest.raises(openai.APIConnectionError):
        await collect(agent, session)
    assert [m["role"] for m in session.messages] == ["user", "assistant", "tool"]

    # 历史是合法的，下一轮可以直接接着聊
    await collect(agent, session, "继续")
    assert [m["role"] for m in llm.requests[-1]["messages"]] == [
        "system",
        "user",
        "assistant",
        "tool",
        "user",
    ]
