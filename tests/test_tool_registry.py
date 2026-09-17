from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict, Field

from simpleagent.tools import ToolContext, ToolError, ToolRegistry, tool
from simpleagent.tools.base import clean_schema


class EchoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(description="要回显的文本")
    times: int = Field(1, ge=1, le=3)


@tool(name="echo", description="回显文本")
async def echo(args: EchoArgs, ctx: ToolContext) -> str:
    if args.text == "boom":
        raise ToolError("故意失败")
    if args.text == "bug":
        raise KeyError("oops")
    return args.text * args.times


def call(name: str, arguments: str, call_id: str = "c1") -> dict:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


@pytest.fixture
def registry() -> ToolRegistry:
    return ToolRegistry([echo])


@pytest.fixture
def ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(cwd=tmp_path)


def test_schema_is_openai_function_without_titles(registry: ToolRegistry):
    assert registry.schemas() == [
        {
            "type": "function",
            "function": {
                "name": "echo",
                "description": "回显文本",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "要回显的文本"},
                        "times": {"type": "integer", "default": 1, "minimum": 1, "maximum": 3},
                    },
                    "required": ["text"],
                    "additionalProperties": False,
                },
            },
        }
    ]


def test_clean_schema_keeps_property_named_title():
    schema = {
        "title": "Model",
        "type": "object",
        "properties": {"title": {"title": "Title", "type": "string", "default": {"title": "x"}}},
    }
    assert clean_schema(schema) == {
        "type": "object",
        "properties": {"title": {"type": "string", "default": {"title": "x"}}},
    }


def test_tool_decorator_checks_signature():
    with pytest.raises(TypeError, match="BaseModel"):

        @tool(name="bad", description="")
        async def bad(args: dict, ctx: ToolContext) -> str:
            return ""

    with pytest.raises(TypeError, match="async"):

        @tool(name="sync", description="")
        def sync(args: EchoArgs, ctx: ToolContext) -> str:
            return ""


def test_duplicate_tool_name_rejected():
    with pytest.raises(ValueError, match="重名"):
        ToolRegistry([echo, echo])


def test_context_resolves_paths_against_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir("/")
    monkeypatch.setenv("HOME", str(tmp_path))
    ctx = ToolContext(cwd=tmp_path)
    assert ctx.resolve("a/../b") == tmp_path.resolve() / "b"
    assert ctx.resolve("~/x") == tmp_path.resolve() / "x"
    assert ctx.resolve("/") == Path("/")


async def test_execute_success(registry: ToolRegistry, ctx: ToolContext):
    result = await registry.execute(call("echo", '{"text": "hi", "times": 2}'), ctx)
    assert not result.is_error
    assert result.name == "echo"
    assert result.as_message() == {"role": "tool", "tool_call_id": "c1", "content": "hihi"}


@pytest.mark.parametrize(
    ("name", "arguments", "expected"),
    [
        ("nope", "{}", "未知工具 nope。可用工具：echo"),
        ("echo", '{"text": ', "参数不是合法 JSON"),
        ("echo", "", "参数校验失败：text: Field required"),  # 空串当成 {}
        ("echo", '{"text": "hi", "times": 9}', "times: Input should be less than or equal to 3"),
        ("echo", '{"text": "hi", "extra": 1}', "extra: Extra inputs are not permitted"),
        ("echo", "[1]", "参数: Input should be a valid dictionary"),
        ("echo", '{"text": "boom"}', "错误：故意失败"),
        ("echo", '{"text": "bug"}', "工具执行异常 KeyError"),
    ],
)
async def test_execute_failures_become_error_results(
    registry: ToolRegistry, ctx: ToolContext, name: str, arguments: str, expected: str
):
    result = await registry.execute(call(name, arguments), ctx)
    assert result.is_error
    assert result.call_id == "c1"
    assert result.content.startswith("错误：")
    assert expected in result.content
