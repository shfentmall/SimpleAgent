"""web/turns.js：一轮回复怎么分「过程」和「最终回答」。用 node 跑前端文件，在 Python 里断言。

机器上没有 node 就整体跳过（和 test_markdown.py 一样）。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

import simpleagent.web

NODE = shutil.which("node")
SCRIPT = Path(simpleagent.web.__file__).parent / "turns.js"

pytestmark = pytest.mark.skipif(NODE is None, reason="需要 node 来跑前端 JS")


def call(fn: str, *args: Any) -> Any:
    js = (
        f"const T = require({json.dumps(str(SCRIPT))});"
        "const args = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
        f"process.stdout.write(JSON.stringify(T[{json.dumps(fn)}](...args)));"
    )
    out = subprocess.run(
        [NODE, "-e", js],
        input=json.dumps(list(args)),
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    return json.loads(out.stdout)


def tc(call_id: str, name: str, **arguments: Any) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)},
    }


def kinds(turn: dict) -> list[str]:
    return [s["kind"] if s["kind"] != "tool" else s["name"] for s in turn["steps"]]


# --------------------------------------------------------------- groupTurns
def test_builtin_loop_history():
    """内置 loop：说的话和工具调用在同一条助手消息里。"""
    (turn,) = call(
        "groupTurns",
        [
            {"role": "user", "content": "测试为什么挂了"},
            {
                "role": "assistant",
                "content": "我先跑一遍测试。",
                "reasoning_content": "先跑 pytest",
                "tool_calls": [tc("c1", "bash", command="uv run pytest -x")],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "1 failed\n[exit code 1]"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [tc("c2", "grep", pattern="make_title"), tc("c3", "read_file")],
            },
            {"role": "tool", "tool_call_id": "c2", "content": "runner.py:142"},
            {"role": "tool", "tool_call_id": "c3", "content": "def make_title(): ..."},
            {"role": "assistant", "content": "## 原因\n标题截断规则改了。"},
        ],
    )
    assert turn["user"] == "测试为什么挂了"
    assert kinds(turn) == ["think", "text", "bash", "grep", "read_file"]
    assert turn["steps"][1]["text"] == "我先跑一遍测试。"
    bash = turn["steps"][2]
    assert bash["done"] is True and bash["result"].startswith("1 failed")
    assert json.loads(bash["args"]) == {"command": "uv run pytest -x"}
    assert turn["answer"] == "## 原因\n标题截断规则改了。"


def test_claude_code_history_blocks_arrive_separately():
    """Claude Code：文字、工具调用各自一条助手消息（Runner 单独落盘的工具调用）。"""
    (turn,) = call(
        "groupTurns",
        [
            {"role": "user", "content": "total() 会不会出错？"},
            {"role": "assistant", "content": "我的查法：先找定义。"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [tc("t1", "Glob", pattern="**/*")],
            },
            {"role": "tool", "tool_call_id": "t1", "content": "README.md\nsrc/ledger.py"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [tc("t2", "Read", file_path="/x")],
            },
            {"role": "tool", "tool_call_id": "t2", "content": "import csv"},
            {"role": "assistant", "content": "结论：第一行就炸。"},
        ],
    )
    assert kinds(turn) == ["text", "Glob", "Read"]
    assert turn["answer"] == "结论：第一行就炸。"


def test_old_cli_history_without_tool_calls_still_groups():
    """老会话：外部执行者以前只存了工具结果。名字参数没了，但归类照样对。"""
    (turn,) = call(
        "groupTurns",
        [
            {"role": "user", "content": "看看"},
            {"role": "assistant", "content": "我先看看目录。"},
            {"role": "tool", "tool_call_id": "t1", "content": "a.py"},
            {"role": "tool", "tool_call_id": "t2", "content": "print(1)"},
            {"role": "assistant", "content": "看完了。"},
        ],
    )
    assert kinds(turn) == ["text", "工具", "工具"]
    assert turn["answer"] == "看完了。"
    assert call("summarize", turn["steps"], CWD) == "调用工具 2 次"


def test_turn_without_final_text_has_no_answer():
    """停在工具调用上（中断、达到步数上限）：没有回答，说过的话都在过程里。"""
    (turn,) = call(
        "groupTurns",
        [
            {"role": "user", "content": "改一下"},
            {"role": "assistant", "content": "开始改。", "tool_calls": [tc("c1", "edit_file")]},
        ],
    )
    assert kinds(turn) == ["text", "edit_file"]
    assert turn["steps"][1]["done"] is False
    assert turn["answer"] == ""


def test_plain_chat_and_turn_split():
    turns = call(
        "groupTurns",
        [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好！"},
            {"role": "user", "content": "再见"},
            {"role": "assistant", "content": "第一段"},
            {"role": "assistant", "content": "第二段"},
        ],
    )
    assert [t["user"] for t in turns] == ["你好", "再见"]
    assert turns[0]["steps"] == [] and turns[0]["answer"] == "你好！"
    # 最后一次工具调用之后（这里是整轮）的文字都算回答
    assert turns[1]["answer"] == "第一段\n\n第二段"


def test_error_looking_results_are_marked():
    (turn,) = call(
        "groupTurns",
        [
            {"role": "user", "content": "x"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [tc("a", "Read"), tc("b", "bash")],
            },
            {"role": "tool", "tool_call_id": "a", "content": "<tool_use_error>File not found"},
            {"role": "tool", "tool_call_id": "b", "content": "ok"},
        ],
    )
    assert [s["isError"] for s in turn["steps"]] == [True, False]


# --------------------------------------------------------------- toolBrief
CWD = "/tmp/sa-demo"


@pytest.mark.parametrize(
    ("name", "args", "brief"),
    [
        ("bash", {"command": "uv run pytest\n  -x -q", "timeout": 120}, "uv run pytest -x -q"),
        ("Bash", {"command": "ls", "description": "列目录"}, "ls"),
        ("Read", {"file_path": "/tmp/sa-demo/src/ledger.py"}, "src/ledger.py"),
        ("read_file", {"path": "/etc/hosts", "offset": 3}, "/etc/hosts"),
        ("Grep", {"pattern": "def total", "path": "/tmp/sa-demo/src"}, "def total · src"),
        ("grep", {"pattern": "TODO"}, "TODO"),
        ("grep", {"pattern": "TODO", "path": "/tmp/sa-demo"}, "TODO"),
        ("list_dir", {"path": "/tmp/sa-demo"}, "."),
        ("Glob", {"pattern": "**/*.py"}, "**/*.py"),
        ("glob", {"pattern": "*.md", "path": "/tmp/sa-demo/docs"}, "docs · *.md"),
        ("TodoWrite", {"todos": [{"content": "a"}, {"content": "b"}]}, "2 项"),
        ("WebFetch", {"url": "https://example.com", "prompt": "总结"}, "https://example.com"),
        ("mcp__filesystem__read_file", {"path": "/tmp/sa-demo/a.txt"}, "a.txt"),
        ("dispatch", {"space": "理财", "task": "查一下余额"}, "查一下余额"),
        ("whatever", {"n": 1, "label": "第一个字符串"}, "第一个字符串"),
    ],
)
def test_tool_brief(name, args, brief):
    assert call("toolBrief", name, json.dumps(args, ensure_ascii=False), CWD) == brief


def test_tool_brief_non_json_args_shown_as_is():
    assert call("toolBrief", "x", "not json\nat all", CWD) == "not json at all"


def test_tool_label_strips_mcp_prefix():
    assert call("toolLabel", "mcp__filesystem__read_file") == "filesystem · read_file"
    assert call("toolLabel", "Read") == "Read"


# --------------------------------------------------------------- summarize
def step(name: str, **args: Any) -> dict[str, Any]:
    return {"kind": "tool", "id": name, "name": name, "args": json.dumps(args)}


def test_summarize_in_product_words():
    steps = [
        {"kind": "text", "text": "先看看"},
        step("Glob", pattern="**/*"),
        step("Read", file_path="/tmp/sa-demo/src/ledger.py"),
        step("Read", file_path="/tmp/sa-demo/ledger.csv"),
        step("Read", file_path="/tmp/sa-demo/src/ledger.py"),  # 同一个文件读两遍算一个
    ]
    assert call("summarize", steps, CWD) == "找文件 1 次、读了 2 个文件"


def test_summarize_caps_at_three_kinds():
    steps = [step("bash", command="ls"), step("grep", pattern="x"), step("edit_file", path="a")]
    steps += [step("mcp__cal__list_events", day="today")]
    assert call("summarize", steps, CWD) == "跑了 1 条命令、搜索 1 次、改了 1 个文件 等"
    assert call("summarize", [steps[-1]], CWD) == "调用 cal · list_events 1 次"


def test_summarize_without_tools_is_empty():
    assert call("summarize", [{"kind": "think", "text": "嗯"}], CWD) == ""


def test_rel_path_handles_private_tmp_alias():
    assert call("relPath", "/private/tmp/sa-demo/a.py", "/tmp/sa-demo") == "a.py"
    assert call("relPath", "/tmp/sa-demo/a.py", "/private/tmp/sa-demo/") == "a.py"
    assert call("relPath", "/tmp/sa-demo", "/tmp/sa-demo") == "."
    assert call("relPath", "/tmp/sa-demo2/a.py", "/tmp/sa-demo") == "/tmp/sa-demo2/a.py"
