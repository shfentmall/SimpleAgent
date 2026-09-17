import asyncio
import io
from collections.abc import Iterable

import httpx2
import openai

from simpleagent.config import Config, ConfigError, Profile
from simpleagent.llm.fake import FakeLLM, Script
from simpleagent.ui.repl import Repl


class Harness:
    """用 FakeLLM 驱动 REPL：每个 profile 一份脚本。"""

    def __init__(
        self,
        config: Config,
        scripts: dict[str, list[Script]],
        inputs: Iterable[str] = (),
        broken: set[str] = frozenset(),
    ):
        self.out = io.StringIO()
        self.fakes: dict[str, FakeLLM] = {}
        remaining = iter(inputs)

        def factory(name: str, profile: Profile) -> FakeLLM:
            if name in broken:
                raise ConfigError(f"{name} 缺少 API key")
            self.fakes[name] = FakeLLM(scripts.get(name, []), name=name, profile=profile)
            return self.fakes[name]

        def input_fn(prompt: str) -> str:
            try:
                return next(remaining)
            except StopIteration:
                raise EOFError from None

        self.repl = Repl(config, llm_factory=factory, out=self.out, input_fn=input_fn)

    @property
    def output(self) -> str:
        return self.out.getvalue()


async def test_chat_turn_records_history_and_prints_stats(config: Config):
    h = Harness(
        config,
        {
            "a": [
                {
                    "content": "你好！",
                    "reasoning": "用户在打招呼",
                    "usage": {"prompt_tokens": 20, "completion_tokens": 8},
                },
                "第二轮",
            ]
        },
    )
    await h.repl.handle("你好")
    await h.repl.handle("再来")

    assert h.repl.messages == [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好！", "reasoning_content": "用户在打招呼"},
        {"role": "user", "content": "再来"},
        {"role": "assistant", "content": "第二轮"},
    ]
    second_request = h.fakes["a"].requests[1]["messages"]
    assert second_request[0]["role"] == "system"
    assert "工作目录" in second_request[0]["content"]
    assert second_request[1:] == h.repl.messages[:3]

    assert "思考：用户在打招呼" in h.output
    assert "你好！" in h.output
    assert "[model-a · 输入 20 · 输出 8" in h.output
    assert h.repl.requests == 2
    assert h.repl.usage.prompt_tokens == 20


async def test_model_switch_keeps_history(config: Config):
    h = Harness(config, {"a": ["来自 a"], "b": ["来自 b"]})
    await h.repl.handle("q1")
    await h.repl.handle("/model")
    assert "* a" in h.output and "  b" in h.output

    await h.repl.handle("/model b")
    assert h.fakes["a"].closed
    assert h.repl.llm.name == "b"
    await h.repl.handle("q2")
    assert h.fakes["b"].requests[0]["messages"][1:] == [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "来自 a"},
        {"role": "user", "content": "q2"},
    ]


async def test_model_switch_failures_keep_current_model(config: Config):
    h = Harness(config, {}, broken={"b"})
    await h.repl.handle("/model nope")
    await h.repl.handle("/model b")
    assert h.repl.llm.name == "a"
    assert "没有名为 'nope' 的 profile" in h.output
    assert "b 缺少 API key" in h.output


async def test_api_error_rolls_back_user_message(config: Config):
    error = openai.APIConnectionError(request=httpx2.Request("POST", "http://a.invalid/v1"))
    h = Harness(config, {"a": [error]})
    await h.repl.handle("hi")
    assert h.repl.messages == []
    assert "请求失败" in h.output
    assert "检查 API key" not in h.output


async def test_auth_error_points_to_key_location(config: Config, sa_home):
    response = httpx2.Response(401, request=httpx2.Request("POST", "http://b.invalid/v1"))
    error = openai.AuthenticationError("invalid key", response=response, body=None)
    config.profiles["a"].api_key_env = "SA_TEST_KEY"
    h = Harness(config, {"a": [error]})
    await h.repl.handle("hi")
    assert "HTTP 401" in h.output
    assert f"检查 API key：SA_TEST_KEY，环境变量优先，其次 {sa_home / '.env'}" in h.output


async def test_cancel_keeps_partial_reply(config: Config):
    slow = {"content": "这是一段很长很长很长的回复", "delay": 0.01}
    h = Harness(config, {"a": [slow, {"content": "不会输出", "delay": 1}]})

    async def cancel_when(predicate):
        task = asyncio.create_task(h.repl.handle("hi"))
        while not predicate():
            await asyncio.sleep(0.005)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # 已有输出：保留部分回复
    await cancel_when(lambda: "这是" in h.output)
    assert h.repl.messages[0] == {"role": "user", "content": "hi"}
    assert h.repl.messages[1]["role"] == "assistant"
    assert h.repl.messages[1]["content"].startswith("这是")

    # 还没输出：撤回这条用户消息
    await cancel_when(lambda: len(h.fakes["a"].requests) == 2)
    assert len(h.repl.messages) == 2


async def test_commands(config: Config):
    h = Harness(config, {"a": ["ok"]})
    await h.repl.handle("hi")
    assert await h.repl.handle("/usage")
    assert "请求 1 次" in h.output
    assert await h.repl.handle("/clear")
    assert h.repl.messages == []
    assert await h.repl.handle("/whatever")
    assert "未知命令 /whatever" in h.output
    assert await h.repl.handle("/exit") is False


def test_run_loop_with_multiline_input(config: Config):
    h = Harness(config, {"a": ["收到"]}, inputs=["", "/help", '"""', "第一行", "第二行", '"""'])
    assert h.repl.run() == 0  # 输入耗尽 → EOFError → 正常退出
    assert h.fakes["a"].requests[0]["messages"][-1] == {
        "role": "user",
        "content": "第一行\n第二行",
    }
    assert "/model [name]" in h.output
    assert h.fakes["a"].closed
