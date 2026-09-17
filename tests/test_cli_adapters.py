"""外部 CLI 适配层的测试：喂录下来的真实样本，断言翻译出来的事件对不对。

样本见 `tests/fixtures/cli/`（真实录制 + 脱敏），不联网、不依赖本机装没装那些 CLI。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from simpleagent.agents import FULL, SAFE, adapter_for
from simpleagent.agents.claude import ClaudeAdapter
from simpleagent.agents.opencode import OpenCodeAdapter
from simpleagent.events import TextDelta, ToolResult

FIXTURES = Path(__file__).parent / "fixtures" / "cli"


def feed(adapter, name: str) -> list:
    """把一份样本喂给适配器，返回所有事件（顺序保持）。"""
    out = []
    for line in (FIXTURES / name).read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.extend(adapter.parse(line).events)
    return out


# --------------------------------------------------------------------- claude
def test_claude_not_logged_in_is_a_failure_not_a_success():
    """真实样本：subtype 是 success，is_error 却是 true——成败只能看 is_error。

    本机 claude 未登录时录的，见 fixtures/cli/claude-not-logged-in.jsonl。
    """
    adapter = ClaudeAdapter()
    turns = [
        adapter.parse(line)
        for line in (FIXTURES / "claude-not-logged-in.jsonl").read_text().splitlines()
        if line.strip()
    ]
    assert adapter.session_id == "11111111-1111-4111-8111-111111111111"
    last = turns[-1]
    assert last.finished is True
    assert last.ok is False
    assert "Not logged in" in (last.error or "")


def test_claude_assistant_blocks_become_our_events():
    adapter = ClaudeAdapter()
    line = json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "thinking", "thinking": "先看目录"},
                    {"type": "text", "text": "我来看看。"},
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "Bash",
                        "input": {"command": "pwd"},
                    },
                ]
            },
        }
    )
    events = adapter.parse(line).events
    kinds = [type(e).__name__ for e in events]
    assert kinds == ["ReasoningDelta", "ToolCallStart", "MessageDone"]
    assert events[1].name == "Bash"
    assert json.loads(events[1].arguments) == {"command": "pwd"}
    assert events[2].message["content"] == "我来看看。"


def test_claude_tool_result_comes_inside_user_message():
    """claude 把工具结果塞在 user 消息里，不是单独的事件类型。"""
    adapter = ClaudeAdapter()
    line = json.dumps(
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_1",
                        "content": [{"type": "text", "text": "/tmp/sa-demo"}],
                        "is_error": False,
                    }
                ]
            },
        }
    )
    (event,) = adapter.parse(line).events
    assert isinstance(event, ToolResult)
    assert event.call_id == "toolu_1" and event.content == "/tmp/sa-demo"


def test_claude_partial_text_is_not_duplicated_by_full_message():
    """逐字流式和整段事件会拿到同一段文本，不能落两条。"""
    adapter = ClaudeAdapter()
    partial = json.dumps(
        {
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "你好"},
            },
        }
    )
    assert [type(e).__name__ for e in adapter.parse(partial).events] == ["TextDelta"]
    full = json.dumps(
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "你好"}]}}
    )
    events = adapter.parse(full).events
    assert [type(e).__name__ for e in events] == ["MessageDone"]
    assert events[0].message["content"] == "你好"
    assert not any(isinstance(e, TextDelta) for e in events)


def test_claude_command_shape():
    adapter = ClaudeAdapter()
    safe = adapter.command("你好", model="sonnet", resume="abc")
    assert safe[0] == "claude"
    assert "--verbose" in safe  # stream-json 必须配 verbose，否则硬报错
    assert "--dangerously-skip-permissions" not in safe
    assert safe[safe.index("--tools") + 1] == "Read,Glob,Grep"
    assert safe[-2:] == ["--", "你好"]  # prompt 放最后并用 -- 隔开，免得被变长参数吃掉
    assert safe[safe.index("--resume") + 1] == "abc"

    full = adapter.command("你好", mode=FULL)
    assert "--dangerously-skip-permissions" in full
    assert "--tools" not in full


def test_claude_usage_counts_cache_tokens_as_input():
    adapter = ClaudeAdapter()
    adapter.parse(
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "done",
                "total_cost_usd": 0.002,
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 3,
                    "cache_read_input_tokens": 100,
                    "cache_creation_input_tokens": 5,
                },
            }
        )
    )
    assert adapter.usage.prompt_tokens == 115
    assert adapter.usage.completion_tokens == 3
    assert adapter.usage.cached_tokens == 100
    assert adapter.cost_usd == pytest.approx(0.002)


# ------------------------------------------------------------------- opencode
def test_opencode_sample_end_to_end():
    """真实样本：bash 调用 → 结果 → 文本 → 结束。"""
    adapter = OpenCodeAdapter()
    events = feed(adapter, "opencode-bash-pwd.jsonl")
    kinds = [type(e).__name__ for e in events]
    assert kinds == ["ToolCallStart", "ToolResult", "TextDelta", "MessageDone"]

    call, result, delta, done = events
    assert call.name == "bash" and json.loads(call.arguments) == {"command": "pwd"}
    assert result.call_id == call.call_id and result.is_error is False
    assert "/tmp/sa-demo" in result.content
    assert "当前目录是" in delta.text
    assert done.message["content"] == delta.text

    assert adapter.session_id == "ses_0000000000000000000000000000"
    # 两段 step_finish 的增量要加起来，cache.read 算进 prompt
    assert adapter.usage.prompt_tokens == 8813 + 512 + 191 + 9216
    assert adapter.usage.completion_tokens == 38 + 17
    assert adapter.cost_usd == pytest.approx(0.001358886 + 0.000066498)


def test_opencode_step_finish_marks_the_end():
    adapter = OpenCodeAdapter()
    tool_step = json.dumps(
        {"type": "step_finish", "part": {"reason": "tool-calls", "tokens": {}, "cost": 0}}
    )
    assert adapter.parse(tool_step).finished is False  # 还要继续跑
    stop_step = json.dumps({"type": "step_finish", "part": {"reason": "stop", "tokens": {}}})
    turn = adapter.parse(stop_step)
    assert turn.finished is True and turn.ok is True


def test_opencode_safe_mode_keeps_everything_but_reads_denied():
    adapter = OpenCodeAdapter()
    assert adapter.env(SAFE).get("OPENCODE_CONFIG_CONTENT")
    config = json.loads(adapter.env(SAFE)["OPENCODE_CONFIG_CONTENT"])
    assert config["permission"]["*"] == "deny"
    assert config["permission"]["read"] == "allow"
    assert "edit" not in config["permission"]  # 落在 * 上，被 deny
    assert adapter.env(FULL) == {}


def test_opencode_command_and_resume():
    adapter = OpenCodeAdapter()
    argv = adapter.command("你好", model="deepseek/deepseek-v4-flash", resume="ses_x", mode=FULL)
    assert argv[:3] == ["opencode", "run", "--format"]
    assert "--output-format" not in argv  # opencode 只有 --format
    assert "--auto" in argv
    assert argv[argv.index("--model") + 1] == "deepseek/deepseek-v4-flash"
    assert argv[argv.index("--session") + 1] == "ses_x"
    assert argv[-2:] == ["--", "你好"]


def test_adapter_for_rejects_builtin_executor():
    with pytest.raises(ValueError):
        adapter_for("simpleagent")
    assert adapter_for("claude-code").name == "claude-code"
    assert adapter_for("opencode").name == "opencode"
