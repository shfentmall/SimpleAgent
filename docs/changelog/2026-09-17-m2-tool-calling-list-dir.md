# M2 第一段：工具抽象 + list_dir + 带工具调用的 Agent loop

- 日期：2026-09-17
- 对比基线：`2b074f0`（Add changelog-writer subagent for pre-push changelogs）
- 对应里程碑：M2（工具调用 + Agent Loop，本次完成第一段，进度标 🚧）

## 功能变化

- 新增：模型可以在 REPL 对话中自己调用工具，目前只有 `list_dir`。输出为树形缩进，目录在前、文件带大小；`.git`/`node_modules`/`.venv` 等目录列出但不展开，标 `[已忽略]`；符号链接显示 `name -> target` 且不跟进；无权限的子目录标 `[无权限]`；参数 `path`（相对工作目录解析）/`depth`（默认 2，1–5）/`limit`（默认 200，1–1000，按层 BFS 截断并在末尾追加提示行）；目录扫描放到 `asyncio.to_thread` 里，避免阻塞事件循环。
- 新增：Tool 抽象和注册表——用 pydantic 参数模型生成 JSON Schema（并去掉 pydantic 自动加的 `title`）；未知工具、非法 JSON、空字符串参数（当作 `{}`）、参数校验失败、`ToolError`、工具自身抛出的其他异常，统一转成 `错误：...` 文本回给模型，不让整个 loop 崩掉。
- 新增：Agent loop（`agent/loop.py`）——请求模型 → 执行 `tool_calls`（同一条消息里的多个调用用 `asyncio.gather` 并行执行，结果按原顺序回传）→ 再次请求模型，直到模型不再调用工具；有 `max_steps` 上限；`MessageDone` 先写入历史再对外发出事件。
- 新增：中断或出错后修复历史（`Agent._repair`）——给没有结果的 `tool_call` 补一条“错误：执行被中断，没有结果”；已经输出的部分正文会保留；如果这一轮什么都没留下（比如第一次请求就出错），撤回这条用户消息。
- 升级：状态（消息历史、用量、请求次数）从 `Repl` 迁到 `Agent` / `Session`，符合 ARCHITECTURE 里“状态归 Agent/Session”的接口约定。
- 升级：REPL 终端渲染——灰色显示工具调用 `→ name args` 和结果预览（最多 `TOOL_PREVIEW_LINES`＝5 行，超过显示“…（共 N 行）”）；出错的工具结果红色显示；并行调用时，结果前如果不紧跟着自己的调用行，会先加一行 `← name` 标注是谁的结果；达到 `max_steps` 时打印提示，建议用户输入“继续”。
- 行为变化：API 请求在输出途中出错时，已经输出的部分正文现在会保留在历史里（以前 REPL 遇到 `APIError` 一律撤回用户消息，只有 `CancelledError` 才保留部分回复；现在两种情况都统一在 `Agent._repair` 里处理，行为一致）。
- 行为变化：每次模型响应结束后立即打印统计行（`[model-a · 输入 ... · 输出 ...]`），有工具调用时一轮对话里会看到多行统计（对应每次请求模型）。

## 函数级改动

### `src/simpleagent/tools/base.py`（新增）

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `ToolContext` | 新增 | 工具执行上下文，目前只有 `cwd`；`resolve(path)` 把相对路径基于 `ctx.cwd` 解析（不用进程当前目录，daemon 场景下两者不一样） |
| `ToolError` | 新增 | 可预期的失败，消息原样回给模型 |
| `clean_schema(schema)` | 新增 | 递归去掉 pydantic 自动生成的 `title` 字段（区分 `properties`/`$defs`/`anyOf` 等子结构，避免误删同名的业务字段） |
| `Tool`（dataclass）/ `Tool.schema()` | 新增 | 封装 name/description/args_model/fn；`schema()` 生成 OpenAI function-calling 格式的一项 |
| `tool(name, description)` | 新增 | 装饰器，把 `async def fn(args, ctx) -> str` 包装成 `Tool`；从函数第一个参数的类型注解推出 pydantic 参数模型，校验必须是 `BaseModel` 子类且函数必须是协程 |

### `src/simpleagent/tools/registry.py`（新增）

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `format_validation_error(error)` | 新增 | 把 pydantic `ValidationError` 转成给模型看的一行文本，不带 `input`（参数可能很长） |
| `ToolRegistry.register(tool)` | 新增 | 注册工具，重名抛 `ValueError` |
| `ToolRegistry.schemas()` | 新增 | 按注册顺序输出 schema 列表，保证请求前缀稳定、能命中缓存 |
| `ToolRegistry.execute(tool_call, ctx)` | 新增 | 执行一个 `tool_call`：查工具→解析 JSON 参数（空串当 `{}`）→校验参数→调用→任何失败都转成 `is_error=True` 的 `ToolResult`，不抛异常（`CancelledError` 除外，属于 `BaseException`，交给上层 loop 处理） |

### `src/simpleagent/tools/list_dir.py`（新增）

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `ListDirArgs` | 新增 | 参数模型：`path`（默认 `.`）、`depth`（默认 2，1–5）、`limit`（默认 200，1–1000），`extra="forbid"` |
| `format_size(size)` | 新增 | 字节数转 `B/K/M/G/T` 可读格式 |
| `_read_children(path)` | 新增 | 读一层目录，目录在前、其余在后，各自按名字排序（不分大小写）；符号链接不 `stat` 跟进，避免链接成环 |
| `_scan(root, depth, limit)` | 新增 | 按层 BFS 扫描，优先保证浅层列完整，截断时丢掉的总是最深部分；忽略目录列出但完全不展开，标 `[已忽略]`；返回 `(顶层条目, 是否截断)` |
| `_render(nodes, indent)` | 新增 | 把节点树渲染成缩进文本行 |
| `_list(root, depth, limit)` | 新增 | 组装完整输出：根路径 + 渲染结果 + 空目录/截断提示 |
| `list_dir(args, ctx)` | 新增 | 工具入口：校验路径存在且是目录，扫描放 `asyncio.to_thread`，`PermissionError` 转 `ToolError` |

### `src/simpleagent/tools/__init__.py`（新增）

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `builtin_tools()` | 新增 | 目前只返回 `[list_dir]`；导出 `Tool`/`ToolContext`/`ToolError`/`ToolRegistry`/`tool` |

### `src/simpleagent/agent/session.py`（新增）

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `Session`（dataclass） | 新增 | 会话状态：`id`、`messages`（不含 system prompt）、`usage`、`requests`；M3 再加 JSONL 持久化 |

### `src/simpleagent/agent/loop.py`（新增）

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `Agent.__init__(llm, tools, system_prompt, cwd, max_steps=20)` | 新增 | 持有 LLM、工具注册表、system prompt、工作目录、`max_steps` |
| `Agent.run(session, user_input)` | 新增 | 核心循环：追加用户消息→请求模型→按 `tool_calls` 执行工具（并行）→回传结果→重复，直到无 `tool_calls` 或达到 `max_steps`；`MessageDone` 先写历史再 `yield`；`BaseException` 时调用 `_repair` 后原样抛出 |
| `Agent._repair(session, turn_start, partial_text)`（staticmethod） | 新增 | 给缺结果的 `tool_call` 补“执行被中断”结果；保留已输出的部分正文；本轮完全没有新增内容时撤回用户消息 |

### `src/simpleagent/events.py`

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `ToolCallStart` | 新增 | 模型要求调用工具时发出，携带 `call_id`/`name`/原始 JSON 字符串 `arguments`，怎么显示由前端决定 |
| `ToolResult` / `ToolResult.as_message()` | 新增 | 一次工具调用的结果；`as_message()` 转成 `{"role": "tool", "tool_call_id": ..., "content": ...}` |
| `MaxStepsReached` | 新增 | 一轮对话请求模型次数达到上限时发出 |
| `Event`（联合类型） | 修改 | 从 `TextDelta \| ReasoningDelta \| MessageDone` 扩展为再加 `ToolCallStart \| ToolResult \| MaxStepsReached` |

### `src/simpleagent/config.py`

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `Config.max_steps` | 新增 | `int = Field(20, ge=1)`，一轮对话最多请求模型几次，防止模型无限调用工具 |

### `src/simpleagent/config.example.toml`

- 新增注释示例 `# max_steps = 20   # 一轮对话最多请求模型几次，防止模型无限调用工具`（默认值走代码，不强制用户改配置文件）。

### `src/simpleagent/ui/repl.py`

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `_clip(line, width=200)` | 新增 | 单行超过 `width` 字符时截断并加省略号，防止工具参数/结果把终端刷屏 |
| `Renderer.__init__` | 修改 | 删除 `self.text`（不再自己攒正文，正文历史归 `Session`）；新增 `self.last_call_id`，记录“刚显示过调用行、还没显示结果”的 `tool_call`，用于并行结果的标注判断 |
| `Renderer.on_event(event)` | 修改 | 事件类型扩展为处理 `ToolCallStart`/`ToolResult`，分发到新方法；正文分支不再累积 `self.text` |
| `Renderer.tool_start(event)` | 新增 | 灰色打印 `→ name args`（截断），记录 `last_call_id` |
| `Renderer.tool_result(event)` | 新增 | 如果这条结果不紧跟着自己的调用行，先打印 `← name` 标注；再打印结果预览（最多 `TOOL_PREVIEW_LINES` 行，超出提示“…（共 N 行）”），出错红色、否则灰色 |
| `Repl.__init__` | 修改 | 用 `self.session = Session(new_session_id())`、`self.agent = Agent(...)` 替换原来的 `self.messages`/`self.usage`/`self.requests`/`self.llm`/`self.system_prompt`；`ToolRegistry(builtin_tools())` 注入 Agent；`build_system_prompt` 显式传入 `cwd=Path.cwd()`（`agent/prompt.py` 本身未改，之前也是默认取 `Path.cwd()`） |
| `Repl.run()` | 修改 | 通过 `self.agent.llm` 访问模型信息和 `close()` |
| `Repl.chat(text)` | 修改 | 不再自己维护 `messages`/`usage`/`requests`，改成消费 `self.agent.run(session, text)` 产出的事件流；新增对 `MaxStepsReached` 的处理（打印提示）；`MessageDone` 时打印统计行；`APIError` 分支不再手动 `pop()` 用户消息（已由 `Agent._repair` 处理），也不再 `return`（改为顺序执行到函数末尾） |
| `Repl.command()` 的 `/clear`、`/usage` | 修改 | 分别操作 `self.session.messages` 和 `self.session.usage`/`self.session.requests` |
| `Repl._switch_model(name)` | 修改 | 通过 `self.agent.llm` 读取/替换当前模型 |
| 删除的属性 | 删除 | `Repl.messages`/`Repl.usage`/`Repl.requests`/`Repl.llm`/`Repl.system_prompt`/`Repl.session_id`（迁到 `Session`/`Agent`） |

## 配置与依赖

- 无新增依赖。
- `Config.max_steps` 是可选项，默认 20，现有 `~/.simpleagent/config.toml` 不需要手动修改；想改上限的话可以在配置里加 `max_steps = N`。
- 无需重新 `uv sync`，无需改 `.env`。

## 测试

- 新增：`tests/test_tool_registry.py`（Tool 装饰器签名校验、schema 生成去 `title`、重名注册报错、`ToolContext.resolve` 相对路径解析、`execute()` 成功/各类失败转 `is_error` 结果，共 7 个测试函数，参数化后 14 个用例）
- 新增：`tests/test_list_dir.py`（目录排序与大小、`depth` 控制展开、忽略目录不展开、`limit` 按层截断且刚好达到不截断、相对路径解析、空目录、路径不存在/不是目录报 `ToolError`、符号链接不跟进、无权限子目录标注、`format_size` 参数化、schema 里 `depth`/`limit` 的边界值，共 13 个测试函数，参数化后 16 个用例）
- 新增：`tests/test_agent_loop.py`（工具调用往返、非法参数回传给模型自我纠正、多次调用保持顺序、多次调用并行执行、达到 `max_steps` 停止、工具执行中被取消时补齐缺失结果、首次请求就出错时撤回用户消息、工具步骤之后出错时保留已有工具结果，共 8 个测试函数）
- 修改：`tests/test_repl.py`（原有用例里的 `h.repl.messages`/`h.repl.usage`/`h.repl.requests`/`h.repl.llm` 改成 `h.repl.session.*`/`h.repl.agent.llm`；新增 3 个用例：工具调用的终端渲染、并行调用的结果标注与出错展示、达到 `max_steps` 时的提示文案）
- 测试结果：`uv run pytest -q` → **74 passed**（本次重新执行确认，原有 33 个 + 本次新增/受影响的用例）
- `uv run ruff check` → All checks passed
- `uv run ruff format --check` → 39 files already formatted

## 相关笔记

- 无（本次未新增 `docs/notes/` 学习笔记，待补）
