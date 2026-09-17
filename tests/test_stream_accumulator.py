from simpleagent.config import Quirks
from simpleagent.events import ReasoningDelta, TextDelta, Usage
from simpleagent.llm.client import StreamAccumulator, prepare_messages


def delta_chunk(delta: dict, finish_reason: str | None = None) -> dict:
    return {"choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}]}


def test_text_reasoning_and_usage():
    acc = StreamAccumulator("reasoning_content")
    events = []
    for chunk in [
        delta_chunk({"role": "assistant", "reasoning_content": "先想"}),
        delta_chunk({"reasoning_content": "一下"}),
        delta_chunk({"content": "你"}),
        delta_chunk({"content": "好"}, "stop"),
        {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5}},
    ]:
        events += acc.feed(chunk)

    assert events == [
        ReasoningDelta("先想"),
        ReasoningDelta("一下"),
        TextDelta("你"),
        TextDelta("好"),
    ]
    assert acc.message() == {
        "role": "assistant",
        "content": "你好",
        "reasoning_content": "先想一下",
    }
    assert acc.finish_reason == "stop"
    assert acc.usage == {"prompt_tokens": 10, "completion_tokens": 5}


def test_custom_reasoning_field():
    acc = StreamAccumulator("reasoning")
    assert acc.feed(delta_chunk({"reasoning": "嗯"})) == [ReasoningDelta("嗯")]
    assert acc.message()["reasoning_content"] == "嗯"  # 内部统一存到 reasoning_content


def test_parallel_tool_call_fragments_are_merged_by_index():
    acc = StreamAccumulator()
    for chunk in [
        delta_chunk(
            {"tool_calls": [{"index": 0, "id": "c0", "function": {"name": "a", "arguments": ""}}]}
        ),
        delta_chunk(
            {"tool_calls": [{"index": 1, "id": "c1", "function": {"name": "b", "arguments": "{"}}]}
        ),
        delta_chunk({"tool_calls": [{"index": 0, "function": {"arguments": '{"x": '}}]}),
        # 有的实现在后续分片里重复带 name，不能拼成 "aa"
        delta_chunk({"tool_calls": [{"index": 0, "function": {"name": "a", "arguments": "1}"}}]}),
        delta_chunk({"tool_calls": [{"index": 1, "function": {"arguments": "}"}}]}, "tool_calls"),
    ]:
        assert acc.feed(chunk) == []

    message = acc.message()
    assert message["content"] is None
    assert message["tool_calls"] == [
        {"id": "c0", "type": "function", "function": {"name": "a", "arguments": '{"x": 1}'}},
        {"id": "c1", "type": "function", "function": {"name": "b", "arguments": "{}"}},
    ]
    assert acc.finish_reason == "tool_calls"


def test_usage_cached_tokens_variants():
    openai_style = Usage.from_dict(
        {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "prompt_tokens_details": {"cached_tokens": 80},
            "completion_tokens_details": {"reasoning_tokens": 15},
        }
    )
    assert openai_style == Usage(100, 20, 80, 15)
    deepseek_style = Usage.from_dict(
        {"prompt_tokens": 100, "completion_tokens": 20, "prompt_cache_hit_tokens": 64}
    )
    assert deepseek_style.cached_tokens == 64
    assert openai_style + deepseek_style == Usage(200, 40, 144, 15)


HISTORY = [
    {"role": "system", "content": "s"},
    {"role": "user", "content": "q1"},
    {"role": "assistant", "content": "a1", "reasoning_content": "r1"},
    {"role": "user", "content": "q2"},
    {"role": "assistant", "content": None, "reasoning_content": "r2", "tool_calls": []},
]


def test_reasoning_echo_none():
    prepared = prepare_messages(HISTORY, Quirks(reasoning_echo="none"))
    assert all("reasoning_content" not in m for m in prepared)
    assert "reasoning_content" in HISTORY[2]  # 不修改原始历史


def test_reasoning_echo_current_turn_only():
    prepared = prepare_messages(HISTORY, Quirks(reasoning_echo="current_turn"))
    assert "reasoning_content" not in prepared[2]
    assert prepared[4]["reasoning_content"] == "r2"


def test_reasoning_echo_all_renames_field():
    prepared = prepare_messages(HISTORY, Quirks(reasoning_echo="all", reasoning_field="reasoning"))
    assert prepared[2]["reasoning"] == "r1"
    assert prepared[4]["reasoning"] == "r2"
    assert "reasoning_content" not in prepared[2]
