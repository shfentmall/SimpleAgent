# M0 + M1：项目脚手架与流式对话 REPL

- 日期：2026-09-17
- 对比基线：`6da6d9e`（Initial commit，仓库里只有 README）
- 对应里程碑：M0、M1

## 功能变化

- 新增：uv 项目脚手架（Python 3.12，依赖 openai、pydantic；开发依赖 pytest、pytest-asyncio、ruff）
- 新增：`sa init` 生成配置文件，`sa` 进入交互式对话，`-m` 指定模型 profile
- 新增：OpenAI 兼容协议的流式客户端，支持思考内容、usage 统计（包括缓存命中数）、厂商私有参数 `extra_body`、各家差异配置 `quirks`
- 新增：REPL 命令 `/model`、`/clear`、`/usage`、`/help`、`/exit`，支持 `"""` 多行输入；Ctrl+C 中断时保留已输出的部分回复
- 新增：trace 落盘，每次请求的完整请求体和响应（包括失败、中断）写到 `~/.simpleagent/traces/`
- 新增：API key 可以放在 `~/.simpleagent/.env`；`api_key_env` 误填 key 本身时直接报错，且报错不会回显 key
- 修复：`base_url` 是回环地址时不读代理环境变量，避免本机代理（如 Clash）把 Ollama 等本地请求转走并返回 502
- 修复：DeepSeek 默认模型 ID 改为 `deepseek-flash`（原先的 `deepseek-v4-flash` 不存在）
- 文档：架构设计、路线图、M0/M1 学习笔记、变更记录规范、`AGENTS.md` 协作约定（`CLAUDE.md` 通过 `@AGENTS.md` 引用它）
- 文档：`docs/plan.md`，用户记录的待规划功能想法（持久化记忆、知识库、websearch）

## 函数级改动

以下全部为新增。

### `src/simpleagent/config.py`

| 函数 / 类 | 说明 |
|---|---|
| `ConfigError` | 配置相关错误，CLI 捕获后打印并以退出码 1 退出 |
| `home_dir()` | 数据目录，默认 `~/.simpleagent`，可用 `SIMPLEAGENT_HOME` 覆盖 |
| `read_env_file()` | 解析 `.env`（支持注释、`export` 前缀和引号）；只返回字典，不写入 `os.environ` |
| `Quirks` | 各家兼容差异：`reasoning_field`、`reasoning_echo`、`stream_usage`、`parallel_tool_calls` |
| `Profile` | 单个模型配置：base_url、model、api_key_env、extra_body、quirks 等 |
| `Profile._check_env_name()` | 校验 `api_key_env` 是合法的变量名，防止把 key 本身填进来 |
| `Profile.api_key()` | 读取 key：先查环境变量，再查 `.env`；本地服务不需要 key |
| `TraceConfig` | trace 开关，以及是否记录原始 SSE chunk |
| `Config` / `Config._check_default_profile()` | 顶层配置；校验 `default_profile` 必须存在 |
| `config_path()` / `load_config()` | 加载 TOML 并校验；校验失败时的报错不带输入值，避免泄露 key |
| `example_config()` / `init_config()` | 读取包内模板，生成配置文件（不覆盖已有文件） |

### `src/simpleagent/config.example.toml`

预置 deepseek、glm、qwen、local（Ollama）四个 profile 的配置模板。

### `src/simpleagent/events.py`

| 函数 / 类 | 说明 |
|---|---|
| `TextDelta` / `ReasoningDelta` | 正文和思考内容的流式增量事件 |
| `Usage` / `Usage.from_dict()` / `Usage.__add__()` | token 用量；同时兼容 OpenAI 的 `cached_tokens` 和 DeepSeek 的 `prompt_cache_hit_tokens`，支持累加 |
| `MessageDone` | 一次调用结束：拼好的 assistant 消息、finish_reason、usage、首字延迟、耗时 |

### `src/simpleagent/trace.py`

| 函数 / 类 | 说明 |
|---|---|
| `new_session_id()` | 生成 `时间戳-随机后缀` 格式的会话 ID |
| `Tracer` / `Tracer.record()` | 按会话目录顺序写 `0001.json`、`0002.json`…… |

### `src/simpleagent/llm/client.py`

| 函数 / 类 | 说明 |
|---|---|
| `LLM` | 模型客户端协议（`stream`、`close`），真实客户端和 FakeLLM 都实现它 |
| `StreamAccumulator` / `feed()` / `message()` | 把流式 chunk 拼成完整 assistant 消息，同时产出增量事件 |
| `StreamAccumulator._merge_tool_call()` | 按 `index` 拼接分片到达的 tool_calls；`name` 只取首次出现的值 |
| `prepare_messages()` | 按 `reasoning_echo` 策略去掉或改名历史里的思考内容，不修改原始历史 |
| `is_loopback()` | 判断 base_url 是否为本机地址 |
| `LLMClient.__init__()` | 创建 `AsyncOpenAI`；本机地址时不读代理环境变量 |
| `LLMClient.build_request()` | 组装请求体：`stream_options`、`max_tokens`、`tools`、`parallel_tool_calls` |
| `LLMClient.stream()` | 流式调用并产出事件；成功、失败、中断都写 trace |
| `LLMClient._trace()` / `close()` | 写 trace 记录（请求体已合并 extra_body）；关闭连接 |

### `src/simpleagent/llm/fake.py`

| 函数 / 类 | 说明 |
|---|---|
| `script_to_chunks()` / `_pieces()` | 把脚本化回复（文本、思考、tool_calls、usage）切成 chunk |
| `FakeLLM` / `stream()` / `close()` | 测试用假模型：记录收到的请求，按脚本回复或抛出异常，复用 `StreamAccumulator` |

### `src/simpleagent/agent/prompt.py`

| 函数 / 类 | 说明 |
|---|---|
| `build_system_prompt()` | 基础提示词 + 环境信息（日期、系统、工作目录）；只精确到日期，保证会话内前缀缓存能命中 |

### `src/simpleagent/ui/repl.py`

| 函数 / 类 | 说明 |
|---|---|
| `format_stats()` | 每轮结束的统计行：输入/缓存/输出/思考 token、首字延迟、耗时、截断提示 |
| `describe_error()` | API 错误的友好描述；401 时提示去哪里检查 key |
| `_supports_color()` | 终端是否支持颜色（尊重 `NO_COLOR`） |
| `Renderer` / `on_event()` / `end()` | 渲染事件流：思考内容灰色显示，并记录已输出的正文 |
| `Repl.__init__()` / `_make_llm()` / `print()` | 初始化会话、trace、system prompt 和模型客户端 |
| `Repl.run()` | 主循环：用 `asyncio.Runner` 复用事件循环，处理 Ctrl+C / Ctrl+D |
| `Repl._read_input()` | 读取输入，支持 `"""` 多行 |
| `Repl.handle()` / `command()` / `_switch_model()` | 分发普通消息和斜杠命令；切换模型时保留历史 |
| `Repl.chat()` | 一轮对话：API 错误时撤回用户消息；中断时保留部分回复 |

### `src/simpleagent/cli.py`、`__main__.py`

| 函数 / 类 | 说明 |
|---|---|
| `main()` | argparse 入口：`sa`、`sa init`、`-m/--profile`；配置错误时退出码为 1 |

## 配置与依赖

- 新增 `pyproject.toml`、`uv.lock`、`.python-version`（3.12）、`.gitignore`（忽略 `.venv`、缓存、`.env`、`.workbuddy/`）
- openai SDK 为 3.x，底层 HTTP 库是 `httpx2`
- 数据目录 `~/.simpleagent/`：`config.toml`、`.env`（建议 `chmod 600`）、`traces/`
- **需要手动处理**：首次使用先运行 `uv run sa init`，再把 key 写进 `~/.simpleagent/.env`（格式 `DEEPSEEK_API_KEY=sk-...`）

## 测试

- `tests/conftest.py`：`sa_home` fixture 把数据目录指向临时目录；`config` fixture 提供两个 profile 的配置
- `tests/test_config.py`（9）：模板可加载、不覆盖已有文件、未知配置项报错、`.env` 读取和优先级、误填 key 时报错不泄露
- `tests/test_stream_accumulator.py`（7）：文本/思考/usage 拼接、并行 tool_calls 分片、缓存字段兼容、三种回传策略
- `tests/test_llm_client.py`（6）：用 `httpx2.MockTransport` 模拟 SSE，经过真实的 SDK 解析；请求体、trace、HTTP 错误、提前退出、本机地址不走代理
- `tests/test_fake_llm.py`（3）：切片、tool_calls、脚本化异常
- `tests/test_repl.py`（8）：多轮历史、切换模型、错误回滚、401 提示、中断保留部分回复、命令、多行输入
- 测试结果：33 passed；`ruff check`、`ruff format --check` 通过
- 手动验证：用本地模拟的 SSE 服务端到端跑通 `sa`，包括给真实进程发 SIGINT；真实 DeepSeek 请求因 key 无效返回 401，有效 key 下的完整对话尚未验证

## 相关笔记

- [docs/notes/M0-M1-streaming-chat.md](../notes/M0-M1-streaming-chat.md)
