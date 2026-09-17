"""输出截断：给模型的结果不能无限长，完整内容落盘备查。"""

import os
import time
from pathlib import Path

from pydantic import BaseModel

from simpleagent.agent.loop import Agent
from simpleagent.agent.session import Session
from simpleagent.llm.fake import FakeLLM
from simpleagent.tools import ToolContext, ToolRegistry, tool
from simpleagent.tools.base import OUTPUT_RETENTION_SECONDS
from simpleagent.tools.output import clip, truncate


class NoArgs(BaseModel):
    pass


def long_text(lines: int) -> str:
    return "".join(f"line {i}\n" for i in range(lines))


def test_clip_keeps_short_text():
    assert clip("hello", 100, 10) == ("hello", False)


def test_clip_cuts_by_lines():
    kept, cut = clip(long_text(10), 1000, 3)
    assert cut
    assert kept == "line 0\nline 1\nline 2\n"


def test_clip_cuts_by_chars():
    kept, cut = clip("x" * 50, 10, 100)
    assert cut
    assert kept == "x" * 10


def test_truncate_without_saver_mentions_no_dump():
    text = truncate(long_text(10), max_chars=1000, max_lines=2)
    assert "只显示前 2/10 行" in text
    assert "没有落盘目录" in text


def test_truncate_saves_full_content(tmp_path: Path):
    full = long_text(10)

    def save(text: str) -> Path:
        (tmp_path / "out.txt").write_text(text)
        return tmp_path / "out.txt"

    text = truncate(full, max_chars=1000, max_lines=2, save=save)
    assert str(tmp_path / "out.txt") in text
    assert (tmp_path / "out.txt").read_text() == full


async def test_context_saves_output(tmp_path: Path):
    ctx = ToolContext(cwd=tmp_path, output_dir=tmp_path / "outputs")
    path = ctx.save_output("hello", "grep")
    assert path is not None
    assert path.name.startswith("grep-")
    assert path.read_text() == "hello"


async def test_context_without_output_dir(tmp_path: Path):
    assert ToolContext(cwd=tmp_path).save_output("hello") is None


async def test_registry_trims_long_results(tmp_path: Path):
    @tool(name="huge", description="返回很长的内容")
    async def huge(args: NoArgs, ctx: ToolContext) -> str:
        return long_text(10)

    ctx = ToolContext(cwd=tmp_path, output_dir=tmp_path / "outputs")
    registry = ToolRegistry([huge], max_output_chars=1000, max_output_lines=3)
    result = await registry.execute(
        {"id": "c1", "function": {"name": "huge", "arguments": "{}"}}, ctx
    )
    assert not result.is_error
    assert result.content.startswith("line 0\nline 1\nline 2\n")
    assert "只显示前 3/10 行" in result.content
    saved = next((tmp_path / "outputs").iterdir())
    assert saved.read_text() == long_text(10)


async def test_error_results_are_not_trimmed(tmp_path: Path):
    @tool(name="huge_error", description="返回很长的错误")
    async def huge_error(args: NoArgs, ctx: ToolContext) -> str:
        raise ValueError("x" * 100)

    ctx = ToolContext(cwd=tmp_path, output_dir=tmp_path / "outputs")
    registry = ToolRegistry([huge_error], max_output_chars=10, max_output_lines=1)
    result = await registry.execute(
        {"id": "c1", "function": {"name": "huge_error", "arguments": "{}"}}, ctx
    )
    assert result.is_error
    assert result.content == f"错误：工具执行异常 ValueError: {'x' * 100}"


async def test_agent_loop_trims_tool_results(tmp_path: Path):
    @tool(name="huge", description="返回很长的内容")
    async def huge(args: NoArgs, ctx: ToolContext) -> str:
        return long_text(10)

    llm = FakeLLM([{"tool_calls": [{"name": "huge", "arguments": {}}]}, "看完了"])
    agent = Agent(
        llm,
        ToolRegistry([huge], max_output_chars=1000, max_output_lines=3),
        "系统提示",
        cwd=tmp_path,
        output_dir=tmp_path / "outputs",
    )
    session = Session("s")
    async for _ in agent.run(session, "hi"):
        pass  # 只消费事件流，验证写进历史的结果已经是截断后的
    tool_message = [m for m in session.messages if m["role"] == "tool"][0]
    assert "只显示前 3/10 行" in tool_message["content"]


def test_clip_applies_char_limit_even_when_lines_are_cut():
    # 501 行、每行 1 万字符：只按行数截断的话会返回 500 万字符
    text = ("x" * 10_000 + "\n") * 501
    kept, cut = clip(text, 30_000, 500)
    assert cut
    assert len(kept) == 30_000


def test_truncate_note_starts_on_new_line():
    text = truncate("y" * 50, max_chars=10, max_lines=100)
    assert text.startswith("y" * 10 + "\n[输出过长已截断")


async def test_tool_can_opt_out_of_registry_truncation(tmp_path: Path):
    @tool(name="paged", description="自己分页", truncate_output=False)
    async def paged(args: NoArgs, ctx: ToolContext) -> str:
        return long_text(10)

    registry = ToolRegistry([paged], max_output_chars=1000, max_output_lines=3)
    result = await registry.execute(
        {"id": "c1", "function": {"name": "paged", "arguments": "{}"}},
        ToolContext(cwd=tmp_path, output_dir=tmp_path / "outputs"),
    )
    assert result.content == long_text(10)
    assert not (tmp_path / "outputs").exists()


def test_old_saved_outputs_are_pruned(tmp_path: Path):
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    old = outputs / "bash-old.txt"
    old.write_text("old")
    recent = outputs / "bash-recent.txt"
    recent.write_text("recent")
    stale = time.time() - OUTPUT_RETENTION_SECONDS - 60
    os.utime(old, (stale, stale))

    path = ToolContext(cwd=tmp_path, output_dir=outputs).save_output("new", "grep")
    assert path is not None and path.exists()
    assert not old.exists()
    assert recent.exists()
