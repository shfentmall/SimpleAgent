import pytest

from simpleagent.events import MessageDone, ReasoningDelta, TextDelta
from simpleagent.llm.fake import FakeLLM


async def collect(llm: FakeLLM, messages: list[dict] | None = None) -> list:
    return [event async for event in llm.stream(messages or [{"role": "user", "content": "hi"}])]


async def test_text_and_reasoning_are_chunked():
    llm = FakeLLM(
        [{"content": "hello world", "reasoning": "thinking", "usage": {"prompt_tokens": 3}}],
        chunk_size=5,
    )
    events = await collect(llm)
    assert [e for e in events if isinstance(e, ReasoningDelta)] == [
        ReasoningDelta("think"),
        ReasoningDelta("ing"),
    ]
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["hello", " worl", "d"]
    done = events[-1]
    assert isinstance(done, MessageDone)
    assert done.message["content"] == "hello world"
    assert done.message["reasoning_content"] == "thinking"
    assert done.usage is not None and done.usage.prompt_tokens == 3


async def test_tool_calls_round_trip_through_accumulator():
    llm = FakeLLM([{"tool_calls": [{"name": "read_file", "arguments": {"path": "/tmp/中文.txt"}}]}])
    done = (await collect(llm))[-1]
    assert done.finish_reason == "tool_calls"
    assert done.message["tool_calls"] == [
        {
            "id": "call_0",
            "type": "function",
            "function": {"name": "read_file", "arguments": '{"path": "/tmp/中文.txt"}'},
        }
    ]


async def test_records_requests_and_raises_scripted_errors():
    llm = FakeLLM(["ok", RuntimeError("boom")])
    await collect(llm, [{"role": "user", "content": "first"}])
    with pytest.raises(RuntimeError, match="boom"):
        await collect(llm)
    with pytest.raises(AssertionError, match="用完"):
        await collect(llm)
    assert llm.requests[0]["messages"] == [{"role": "user", "content": "first"}]
