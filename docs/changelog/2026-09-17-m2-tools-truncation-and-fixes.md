# M2 完成：其余 6 个内置工具 + 输出截断落盘 + 只读并行/含写串行

- 日期：2026-09-17
- 对比基线：`a3e5043`（Add tool calling agent loop with list_dir tool (M2 part 1)）
- 对应里程碑：M2（工具调用 + Agent Loop，本次完成全部内容，进度标 ✅）

## 功能变化

- 新增：`read_file` 工具——按行号读取文本文件（格式 `行号<Tab>内容`），支持 `offset`/`limit` 续读；自己流式逐行读取并按约 3 万字符 / limit 行分页，不受 `tool_output` 截断配置影响；单行超过 2000 字符截断；不接受二进制文件；不再有文件大小上限（旧版本超过 2MB 直接拒绝）。
- 新增：`write_file` 工具——把 `content` 整篇写入文件，文件不存在就新建（自动建父目录），已存在就覆盖；返回写入行数和字节数；标记为 `readonly=False`。
- 新增：`edit_file` 工具——把 `old_string` 替换成 `new_string`，默认要求 `old_string` 在文件里唯一，否则报错提示加长 `old_string` 或传 `replace_all=true`；返回 unified diff；保留文件原有换行符（LF/CRLF）；标记为 `readonly=False`。
- 新增：`glob` 工具——支持 `*`（一层内任意字符）、`?`（单字符）、`**`（任意层目录）通配符，自己转正则实现（不用 stdlib `glob`），跳过 `.git`/`node_modules`/`.venv` 等目录；结果按路径排序，超过 `limit` 提示还有多少没列出。
- 新增：`grep` 工具——按正则表达式搜索文件内容，输出“相对路径:行号:该行内容”；支持 `include` 按文件名过滤、`case_sensitive`、`limit`；跳过二进制文件和超过 2MB 的文件，并在结果里报告跳过了多少个；边遍历边搜，凑够 `limit` 条就停止遍历。
- 新增：`bash` 工具——执行 shell 命令，合并 stdout/stderr，`stdin=DEVNULL`（避免命令等待输入卡住整个 agent），支持 `timeout`（默认 60s）和 `cwd` 参数；超时或被取消（Ctrl+C）时杀掉整个进程组（不只是杀 `/bin/sh`）；输出最多读取 10MB，超过就停止读取并终止命令；启动子进程的环境变量会去掉各 profile 的 `api_key_env`，避免用户自己 `export` 的 key 被子进程（比如执行 `env`）读到；标记为 `readonly=False`。
- 新增：`tools/walk.py` 提供公共的目录遍历逻辑：忽略目录清单 `IGNORED_DIRS`、隐藏文件清单 `HIDDEN_FILES`（从 `list_dir.py` 挪过来，供 `list_dir`/`glob`/`grep` 共用）、`iter_files()`（栈式遍历、跳过忽略目录和失效/指向目录的符号链接）、`run_in_thread()`（阻塞遍历放线程里跑，取消时通过 `threading.Event` 让遍历尽快退出）、`is_binary()`（按前 8KB 有没有 NUL 字节判断）、`iter_lines()`（只按 `\n` 分行，流式产出 `(行号, bytes)`，`read_file`/`grep` 共用保证行号一致）、`relative()`。
- 新增：`tools/output.py` 提供统一的输出截断：`clip()` 同时按行数和字符数两个上限截断；`truncate()` 在截断时把完整内容落盘（通过传入的 `save` 回调）并在提示里给出路径，提示模型用 `read_file` 加 `offset`/`limit` 读取被截掉的部分；落盘失败时提示“完整内容找不回来了”。
- 新增：`ToolContext.output_dir`（过长输出的落盘目录）、`ToolContext.hidden_env`（子进程要去掉的环境变量）、`ToolContext.subprocess_env()`（返回去掉 `hidden_env` 的环境变量字典）、`ToolContext.save_output()`（把内容落盘到 `<output_dir>/<prefix>-<时间戳>-<内容哈希>.txt`，写完顺手清理超过 7 天的旧文件，写失败返回 `None` 不影响本次调用）。
- 新增：`Tool.readonly`（默认 `True`，标记会不会改动外部状态）、`Tool.truncate_output`（默认 `True`，是否由注册表统一截断；`read_file` 设为 `False` 自己分页）；`@tool()` 装饰器新增对应的 `readonly`、`truncate_output` 关键字参数。
- 新增：`ToolRegistry.execute_many()`——同一条消息里的多个 `tool_call`：全是只读工具就用 `asyncio.gather` 并行，一批产出；只要有一个写操作（`readonly=False`）就按 `tool_calls` 原顺序逐个执行、每执行完一个就产出一批（`AsyncIterator[list[ToolResult]]`），避免“先写 A 再读 A”读到旧内容、两个写互相覆盖，也让中途被中断时已完成的调用能如实记进历史。`ToolRegistry.is_readonly()` 判断某个工具名是否只读（未知工具当只读处理）。`ToolRegistry.trim()` 对开启了 `truncate_output` 的工具结果做截断。`ToolRegistry.__init__` 新增 `max_output_chars`/`max_output_lines` 参数（默认 30000 字符 / 500 行）。
- 升级：`Agent.run()` 改用 `execute_many()` 按批消费结果，每批先写入 `session.messages` 再对外 `yield`，中断时已完成的写操作结果不会再被 `_repair` 误判为“执行被中断”。`Agent.__init__` 新增 `output_dir`、`hidden_env` 参数，透传给每次调用工具时新建的 `ToolContext`。
- 新增：`Config.tool_output`（`ToolOutputConfig`：`max_chars` 默认 30000、`max_lines` 默认 500，两者都要满足才不截断）、`Config.api_key_env_names()`（收集所有 profile 用到的 `api_key_env` 环境变量名，供 `Agent(hidden_env=...)` 使用）；`config.example.toml` 新增 `[tool_output]` 示例段落。
- 新增：REPL `/tools` 命令，列出当前注册的工具名和描述；`Repl.__init__` 用 `home_dir() / TOOL_OUTPUT_DIRNAME` 作为 `output_dir` 传给 `Agent`，并把 `config.tool_output.max_chars/max_lines` 传给 `ToolRegistry`、把 `config.api_key_env_names()` 传给 `Agent(hidden_env=...)`。
- 行为变化：`read_file` 不再因为文件过大而拒绝读取，输出长度也不受 `[tool_output]` 配置影响（它自己按约 3 万字符 / limit 行分页）。
- 行为变化：`bash` 超时时的提示文案变为“已终止命令及其子进程”（旧版本只杀 `/bin/sh`，子进程可能继续占用管道）。
- 行为变化：`grep`/`read_file` 的行号统一只按 `\n` 计算（不再用 `str.splitlines()`，避免在 `\f`、`\u2028` 等字符处多分出行，导致两个工具报的行号对不上）。

### code review 后的修复（本批改动内一并完成）

1. `bash` 超时对带子进程的命令无效——`start_new_session=True` 让命令自成进程组，超时/取消时 `_kill_group()` 用 `os.killpg` 杀整组，不只是杀 `/bin/sh`。
2. Ctrl+C 取消 `bash` 时命令会留在后台继续跑——取消分支里也调用 `_kill_group()`。
3. 用户 `export` 的 API key 会被 `bash` 子进程继承——新增 `Config.api_key_env_names()` → `Agent(hidden_env=...)` → `ToolContext.hidden_env`/`subprocess_env()`，`bash` 用 `env=ctx.subprocess_env()` 启动子进程。
4. `output.clip()` 超过行数上限后不再检查字符数——改成两个上限都要满足；截断提示改成单独另起一行，避免和被截断的最后一行内容粘在一起。
5. `read_file` 默认 2000 行、注册表统一截断 500 行两者冲突（导致续读提示被截、模型被引导去读一份错位的另存副本）——`read_file` 用 `@tool(truncate_output=False)` 跳过统一截断，改成自己按行数和约 3 万字符两个预算分页，单行超过 `MAX_LINE_CHARS=2000` 截断。
6. 含写操作的一批调用中途被 Ctrl+C 中断时，已经执行完的写操作被 `_repair` 报成“执行被中断”——`execute_many` 从“凑齐结果一次性返回”改成 `AsyncIterator[list[ToolResult]]`，串行分支每执行完一个就产出一批，`Agent.run` 每批先写历史再 `yield`。
7. `read_file` 超过 2MB 直接拒绝，但错误提示建议的 `offset`/`limit` 分段读同样会被拒绝——去掉 `MAX_FILE_BYTES` 限制，改用 `walk.iter_lines()` 流式逐行读，只解码要返回的那部分行。
8. `is_binary()` 遇到 `OSError`（比如没有读权限）一律返回 `True`，导致无权限文件被报成“二进制文件”而不是权限错误——改成直接把 `OSError` 抛出去，由调用方给出真实原因。
9. `edit_file` 会把 CRLF 文件的换行符全部改成 LF——改用 `newline=""` 原样读写文件；当文件是 CRLF 风格且模型给的 `old_string` 不含 `\r` 时，把 `old_string`/`new_string` 转成 CRLF 再匹配；展示的 diff 统一转成 `\n` 显示。
10. `bash` 输出没有内存上限——新增 `_Capture` 类边读边限量（`MAX_OUTPUT_BYTES=10MB`），超过就停止读取并终止命令（`KILL_GRACE_SECONDS=2` 秒收尾等待）。
11. `walk.iter_files()` 会把指向目录的、或已经失效的符号链接当成文件返回——增加判断跳过。

小修：`edit_file` 里 `replace_all` 参数的文档说明改成和实际行为一致；`grep` 改成边遍历边搜（`_candidates()` 生成器，凑够 `limit` 条就提前停止遍历），跳过提示的文案改为“已跳过 N 个二进制、超过 2MB 或读不了的文件”；新增 `walk.run_in_thread()`（取消时设置 `threading.Event`，让 `glob`/`grep` 的遍历尽快退出）；`ToolContext.save_output()` 落盘后清理超过 7 天（`OUTPUT_RETENTION_SECONDS`）的旧文件；`list_dir.py` 去掉多余的 `noqa` 和空注释（`IGNORED_DIRS`/`HIDDEN_FILES` 挪到 `walk.py`）。

## 函数级改动

### `src/simpleagent/tools/walk.py`（新增文件）

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `IGNORED_DIRS` / `HIDDEN_FILES` | 新增（从 `list_dir.py` 挪来） | 遍历时跳过的目录名 / 完全不显示的文件名，`list_dir`/`glob`/`grep` 共用 |
| `iter_files(root, stop=None)` | 新增 | 栈式递归列出 `root` 下的文件，跳过忽略目录、无权限目录、不跟进符号链接；`stop` 被设置时提前返回 |
| `run_in_thread(fn)` | 新增 | 在线程里跑阻塞的遍历/搜索函数，`await` 被取消时设置 `threading.Event` 让 `fn` 尽快退出 |
| `is_binary(path)` | 新增 | 按前 8KB 有没有 NUL 字节判断是否为二进制；`OSError` 直接抛出，不吞掉权限错误 |
| `iter_lines(path)` | 新增 | 流式逐行读取，产出 `(行号, 去掉行尾换行符的 bytes)`，只按 `\n` 分行 |
| `relative(path, root)` | 新增 | 相对 `root` 的 POSIX 风格路径，取不到相对路径时回退成绝对路径 |

### `src/simpleagent/tools/output.py`（新增文件）

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `DEFAULT_MAX_CHARS` / `DEFAULT_MAX_LINES` | 新增 | 默认截断上限：30000 字符 / 500 行 |
| `Saver` | 新增 | 落盘回调的类型别名 `Callable[[str], Path \| None]` |
| `clip(text, max_chars, max_lines)` | 新增 | 同时按行数和字符数截断，返回 `(保留部分, 是否截断)` |
| `truncate(content, max_chars, max_lines, save=None)` | 新增 | 截断后调用 `save` 落盘完整内容，拼上提示文案（含落盘路径或“找不回来了”） |

### `src/simpleagent/tools/base.py`

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `OUTPUT_RETENTION_SECONDS` | 新增 | 落盘输出保留 7 天 |
| `ToolContext.output_dir` | 新增字段 | 过长输出落盘目录，`None` 表示不落盘 |
| `ToolContext.hidden_env` | 新增字段 | 启动子进程时要去掉的环境变量名集合 |
| `ToolContext.subprocess_env()` | 新增 | 返回去掉 `hidden_env` 后的环境变量字典 |
| `ToolContext.save_output(content, prefix="output")` | 新增 | 把内容写到 `<output_dir>/<prefix>-<时间戳>-<哈希>.txt`，写失败返回 `None` |
| `ToolContext._prune_outputs(keep)` | 新增 | 删除超过保留期的旧落盘文件，清理失败不影响本次调用 |
| `Tool.readonly` | 新增字段 | 默认 `True`，标记工具是否只读 |
| `Tool.truncate_output` | 新增字段 | 默认 `True`，是否由注册表统一截断 |
| `tool(name, description, *, readonly=True, truncate_output=True)` | 修改 | 新增 `readonly`/`truncate_output` 关键字参数，透传给生成的 `Tool` |

### `src/simpleagent/tools/registry.py`

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `ToolRegistry.__init__(tools, max_output_chars=..., max_output_lines=...)` | 修改 | 新增两个截断上限参数并保存 |
| `ToolRegistry.is_readonly(name)` | 新增 | 判断某工具是否只读；未知工具当只读处理 |
| `ToolRegistry.execute(tool_call, ctx)` | 修改 | 成功结果如果 `tool.truncate_output` 为真，调用 `self.trim()` 截断后再返回 |
| `ToolRegistry.execute_many(tool_calls, ctx)` | 新增 | 异步生成器：全只读则 `asyncio.gather` 并行、一批产出；含写操作则按原顺序逐个执行、每个结果单独产出一批 |
| `ToolRegistry.trim(content, name, ctx)` | 新增 | 调用 `tools.output.truncate()`，落盘回调绑定到 `ctx.save_output(text, name)` |

### `src/simpleagent/agent/loop.py`

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `Agent.__init__` | 修改 | 新增 `output_dir: Path \| None = None`、`hidden_env: frozenset[str] = frozenset()` 参数并保存 |
| `Agent.run(session, user_input)` | 修改 | 构造 `ToolContext` 时带上 `output_dir`/`hidden_env`；把 `asyncio.gather` 直接并行执行改成 `async for batch in self.tools.execute_many(...)`，每批先写历史再 `yield` |

### `src/simpleagent/tools/list_dir.py`

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `IGNORED_DIRS` / `HIDDEN_FILES` | 删除（改为从 `walk.py` 导入） | 和 `glob`/`grep` 共用同一份忽略清单 |

### `src/simpleagent/tools/read_file.py`（新增文件）

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `ReadFileArgs` | 新增 | 参数：`path`、`offset`（默认 1）、`limit`（默认 2000，1–5000） |
| `MAX_OUTPUT_CHARS` / `MAX_LINE_CHARS` | 新增 | 单次最多返回约 3 万字符；单行超过 2000 字符截断 |
| `_read(path, offset, limit)` | 新增 | 流式逐行读取（`walk.iter_lines`），只解码要返回的行；按字符预算提前停止并在末尾提示 `offset=N` 续读；`offset` 超出总行数报 `ToolError` |
| `read_file(args, ctx)` | 新增 | 工具入口：校验文件存在且不是目录，`is_binary` 拒绝二进制，`PermissionError` 转 `ToolError`；`@tool(..., truncate_output=False)` |

### `src/simpleagent/tools/write_file.py`（新增文件）

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `WriteFileArgs` | 新增 | 参数：`path`、`content` |
| `_write(path, content)` | 新增 | 自动建父目录后整篇写入 |
| `write_file(args, ctx)` | 新增 | 工具入口：目录路径报错，写入放 `asyncio.to_thread`，`OSError` 转 `ToolError`；返回“已新建/已覆盖 + 行数/字节数”；`@tool(..., readonly=False)` |

### `src/simpleagent/tools/edit_file.py`（新增文件）

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `EditFileArgs` | 新增 | 参数：`path`、`old_string`、`new_string`（默认空串）、`replace_all`（默认 `False`） |
| `_replace(text, old, new, replace_all)` | 新增 | 统计匹配次数，0 次或多次且未开 `replace_all` 都报 `ToolError` |
| `_to_crlf(text)` | 新增 | 把 `\n` 换行的文本转成 `\r\n` |
| `_diff(before, after, path)` | 新增 | 生成 unified diff（统一换行成 `\n` 展示，`CONTEXT_LINES=2`） |
| `_edit(path, old, new, replace_all)` | 新增 | `is_binary` 拒绝二进制；`newline=""` 原样读写保留换行风格；CRLF 文件且 `old_string` 不含 `\r` 时把 old/new 转成 CRLF 再匹配 |
| `edit_file(args, ctx)` | 新增 | 工具入口：文件不存在/是目录/`old_string` 为空都报错；`PermissionError` 转 `ToolError`；`@tool(..., readonly=False)` |

### `src/simpleagent/tools/glob.py`（新增文件）

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `GlobArgs` | 新增 | 参数：`pattern`、`path`（默认 `.`）、`limit`（默认 200，1–2000） |
| `compile_pattern(pattern)` | 新增 | 把 `*`/`?`/`**`/`**/` 通配符转成正则（`**/` → `(?:.*/)?`，`**` → `.*`，`*` → `[^/]*`，`?` → `[^/]`） |
| `_glob(root, pattern, limit, stop)` | 新增 | 遍历 `walk.iter_files` 得到的相对路径，用编译好的正则过滤并排序 |
| `glob(args, ctx)` | 新增 | 工具入口：路径不存在报错，遍历放 `walk.run_in_thread` |

### `src/simpleagent/tools/grep.py`（新增文件）

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `GrepArgs` | 新增 | 参数：`pattern`、`path`（默认 `.`）、`include`（可选）、`case_sensitive`（默认 `True`）、`limit`（默认 50，1–500） |
| `MAX_FILE_BYTES` / `MAX_LINE_CHARS` | 新增 | 跳过超过 2MB 的文件；单条结果超过 300 字符截断 |
| `_candidates(root, include, stop)` | 新增 | 生成器，边遍历边按 `include` 过滤候选文件 |
| `_search(root, args, matcher, stop)` | 新增 | 边遍历边搜索，凑够 `limit` 条提前停止；跳过二进制/超大/读不了的文件并计数 |
| `grep(args, ctx)` | 新增 | 工具入口：路径不存在、正则不合法都报 `ToolError`；搜索放 `walk.run_in_thread` |

### `src/simpleagent/tools/bash.py`（新增文件）

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `BashArgs` | 新增 | 参数：`command`、`timeout`（默认 60，1–600）、`cwd`（可选） |
| `MAX_OUTPUT_BYTES` / `KILL_GRACE_SECONDS` | 新增 | 输出最多读取 10MB；杀进程组后最多再等 2 秒收尾 |
| `_Capture.pump(stream)` / `_Capture.text()` | 新增 | 边读边攒输出到 `bytearray`，超过 `limit` 标记 `overflow` 并停止读取 |
| `_kill_group(process)` | 新增 | `os.killpg` 杀掉整个进程组，忽略“已经退出” |
| `bash(args, ctx)` | 新增 | `create_subprocess_shell(..., start_new_session=True, env=ctx.subprocess_env())`；`wait_for(shield(task))` 实现超时不取消读取任务；超时/输出溢出/取消都调用 `_kill_group`；返回命令、输出、退出码或终止原因；`@tool(..., readonly=False)` |

### `src/simpleagent/tools/__init__.py`

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `builtin_tools()` | 修改 | 从 `[list_dir]` 扩展为 `[list_dir, read_file, write_file, edit_file, glob, grep, bash]`（顺序即请求 `tools` 数组顺序，保持稳定命中前缀缓存） |

### `src/simpleagent/config.py`

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `ToolOutputConfig` | 新增 | `max_chars`（默认 30000，`ge=1000`）、`max_lines`（默认 500，`ge=10`） |
| `TOOL_OUTPUT_DIRNAME` | 新增 | 常量 `"tool_outputs"`，落盘目录名 |
| `Config.tool_output` | 新增字段 | `Field(default_factory=ToolOutputConfig)` |
| `Config.api_key_env_names()` | 新增 | 收集所有 profile 的 `api_key_env`，返回 `frozenset[str]` |

### `src/simpleagent/ui/repl.py`

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `HELP` | 修改 | 新增 `/tools` 命令说明 |
| `Repl.__init__` | 修改 | 新增 `output_dir = home_dir() / TOOL_OUTPUT_DIRNAME`；`ToolRegistry` 构造时带上 `max_output_chars`/`max_output_lines`；`Agent` 构造时带上 `output_dir`、`hidden_env=config.api_key_env_names()` |
| `Repl.command()` 的 `/tools` 分支 | 新增 | 遍历 `self.agent.tools.schemas()`，打印每个工具的名字（加粗）和描述 |

## 配置与依赖

- 无新增依赖，无需 `uv sync`。
- 新增可选配置节 `[tool_output]`（`max_chars`/`max_lines`），都有默认值（30000 / 500），现有 `~/.simpleagent/config.toml` 不需要手动修改；想改上限时参照 `config.example.toml` 加这一节。
- 新增数据目录 `~/.simpleagent/tool_outputs/`，存放被截断的完整工具输出，保留 7 天后自动清理（在每次落盘时顺手清理，不是定时任务）。
- 无需改 `.env`。

## 测试

- 新增：`tests/test_bash.py`（11 个测试函数，覆盖正常执行/退出码、超时杀掉子进程、Ctrl+C 取消时杀掉进程组、输出超过 `MAX_OUTPUT_BYTES` 时截断并终止、`stdin=DEVNULL`、`cwd` 参数、环境变量隐藏 `hidden_env` 等）
- 新增：`tests/test_read_file.py`（16 个测试函数，覆盖行号格式、`offset`/`limit` 分页续读提示、单行超长截断、大文件流式读取不再被拒绝、二进制拒绝、`OSError`/权限错误、文件不存在/是目录等）
- 新增：`tests/test_write_edit_file.py`（12 个测试函数，覆盖新建/覆盖、自动建父目录、`edit_file` 唯一匹配/`replace_all`/多次匹配报错、CRLF 文件保留换行符、LF 文件不变、diff 内容等）
- 新增：`tests/test_glob_grep.py`（18 个测试函数，覆盖 `*`/`?`/`**` 各种模式、忽略目录跳过、`grep` 的 `include`/`case_sensitive`/`limit`、跳过二进制和大文件并报告、正则语法错误报 `ToolError` 等）
- 新增：`tests/test_output_truncation.py`（14 个测试函数，覆盖 `clip()`/`truncate()` 的行数与字符数双重上限、落盘路径提示、落盘失败提示、`ToolRegistry.trim()`、`truncate_output=False` 的工具跳过统一截断、`ToolContext._prune_outputs` 清理超过 7 天的文件等）
- 修改：`tests/test_agent_loop.py`（新增/重命名测试覆盖只读并行 `test_readonly_calls_run_concurrently`、含写操作串行 `test_write_calls_run_in_order`、串行中途取消时已完成写操作保留结果 `test_cancel_mid_batch_keeps_finished_write_results`，以及工具列表断言更新为 7 个内置工具）
- 修改：`tests/test_config.py`（新增 `test_api_key_env_names_collects_all_profiles`）
- 修改：`tests/test_repl.py`（新增 `test_tools_command_lists_builtin_tools`、`test_repl_hides_api_key_env_from_tools`）
- 测试结果：`uv run pytest -q` → **158 passed**
- `uv run ruff check` → All checks passed
- `uv run ruff format --check` → 54 files already formatted
- 未用真实模型实测（所有测试用 FakeLLM，未联网验证工具在真实模型下的调用效果）。

## 相关笔记

- [docs/notes/M2-tools-and-agent-loop.md](../notes/M2-tools-and-agent-loop.md)
