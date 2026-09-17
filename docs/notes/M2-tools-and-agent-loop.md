# M2：工具生态与 Agent Loop

日期：2026-09-17 · 对应里程碑 M2（工具调用 + Agent Loop）

这一篇记的是“工具从 1 个变成 7 个”之后才暴露出来的问题：
单个工具怎么写都行，一旦模型能自己选工具，工具的输出形态、失败方式和执行顺序就开始互相影响了。

## 1. Function calling 里真正要自己处理的三件事

协议本身很薄：请求带 `tools`（每个是 JSON Schema），模型回 `tool_calls`，你把结果以
`{"role": "tool", "tool_call_id": ...}` 塞回历史。麻烦的都在细节里：

- **参数可能不是合法 JSON**。小模型尤其常见（`{"depth": ` 这种半截）。不能抛异常，
  把错误当 tool 结果回给它，它下一轮通常会自己改对——这就是“错误自愈”的全部机制。
- **工具结果没有错误标记**。Chat Completions 的 tool 消息只有 content，
  所以错误只能写成文本（`错误：...`）。模型靠“看到错误文本”来决定重试还是换工具。
- **中断时历史会不合法**。有 tool_call 就必须有对应的 tool 消息，否则下一次请求被 API 拒绝。
  M2 第一段已经做了 `_repair`，这一轮没有改。

## 2. 工具的输出形态比功能更重要

模型看不到终端，它只看到你返回的字符串。所以每个工具的输出都在回答三个问题：
“找到了吗”“在哪”“下一步怎么拿更多”。

| 工具 | 输出里刻意带上的信息 | 为什么 |
|---|---|---|
| `read_file` | 行号（右对齐）+ `共 N 行` + `[还有 N 行没读；用 offset=7 继续]` | 让模型能照抄 offset 继续读，而不是重读整个文件 |
| `glob` / `grep` | `匹配 N 个` + 相对路径 + `[还有 N 个没列出：调大 limit 或把 pattern 写精确]` | 告诉模型“结果被截断了，而且有办法拿到更多” |
| `edit_file` | unified diff（前后各 2 行上下文） | 人和模型都能一眼确认改的是不是那一处 |
| `bash` | `$ 命令` + 输出 + `[退出码 N]` | 有退出码，模型才知道命令其实失败了（很多命令失败时 stdout 是空的） |

反过来，报错文本也要能指导下一步：`old_string 出现 3 次` 后面跟着
`把 old_string 写长一点让它唯一，或者用 replace_all=true`——这不是给用户看的，是给模型看的。
写工具描述时我一直在想：“如果只能看到这一行字，我知道该怎么改吗？”

## 3. 唯一匹配：宁可报错，不要猜

`edit_file` 要求 `old_string` 在文件里唯一（否则必须 `replace_all=true`）。
放弃的方案是“改第一处”或者“改所有”：模型常常没意识到文件里有三处一样的
`return 1`，静默改动会让它以为自己改的是看过的那一处，后面的推理全错。
报错的成本只是多一轮对话，猜错的成本是一次看不见的错误修改。

`write_file` 则是整篇写入、不做局部补丁：整篇写入的结果最容易判断（写完就这内容），
代价是改一个字也要重写全文，所以大文件应该走 `edit_file`。

## 4. 输出截断：统一在注册表里做，不在工具里做

工具结果可以无限长（`grep .` 一下几 MB），必须截断。放在 `ToolRegistry.trim` 里的好处是
新增工具自动获得这个能力，不用记得加。策略：

1. 行数（默认 500）和字符数（默认 30,000 ≈ 8k token）两个上限**都要满足**——只按行数截断的话，
   500 行压缩过的 json 照样是几百万字符；
2. 只保留开头，**不保留结尾**——模型下一步通常是“再去查”，提示里给出落盘路径比塞一段没有上下文的尾部有用；
3. 完整内容写到 `~/.simpleagent/tool_outputs/<tool>-<时间>-<hash>.txt`，
   提示里明确说“用 read_file 加 offset/limit 读它”，形成闭环；
4. 落盘失败（没配目录、磁盘不可写）不能让工具调用失败，只在提示里说明。

错误结果不截断：错误文本本身就是给模型看的关键信息，而且通常不长。

**例外：自己会分页的工具不走统一截断**（`@tool(truncate_output=False)`，目前只有 `read_file`）。
统一截断只留开头，会把 `read_file` 结尾的“用 offset=N 继续”截掉，还会另存一份带行号的副本、
让模型去读副本（offset 错位，读副本又被截断再存一份）。`read_file` 自己按行数和字符数两个预算停下，
告诉模型从哪一行继续，更直接。落盘文件保留 7 天，每次落盘时顺手清理。

## 5. 并行还是串行：看 readonly

M2 第一段是无脑 `asyncio.gather` 并行。加了写工具之后就得区分：
模型经常一口气发出 `[write_file(a), read_file(a)]`，并行的话读到的可能是旧内容；
两个写操作并行更危险。所以 `Tool` 加了 `readonly` 标记，
`ToolRegistry.execute_many` 的规则是：**全是只读才并行，含一个写操作就按原顺序串行**。
结果顺序始终和 `tool_calls` 一致（协议要求 tool 消息和 call 一一对应）。

串行时 `execute_many` 每执行完一个就产出一批结果，loop 立刻写进历史。
如果等全部执行完再写，中途按 Ctrl+C 时，已经写完文件的 `write_file` 会被 `_repair` 当成
“执行被中断，没有结果”，模型很可能再写一遍。

## 6. 文件系统工具上踩到的坑

- **不能信扩展名判断二进制**：按前 8KB 里有没有 NUL 字节判断，比维护一份扩展名清单可靠。
- **`glob` 用 stdlib 会卡死在 node_modules**：`glob.glob("**/*.py", recursive=True)`
  会真的遍历 `.git` 和 `node_modules`。自己写了 `walk.py`：栈式遍历 + 忽略目录剪枝 + 符号链接不跟进。
- **`**` 的语义要自己定**：把 `**/` 编译成 `(?:.*/)?`（匹配 0 层或多层）、`*`/`?` 编译成不跨目录的
  `[^/]*` / `[^/]`，行为比 shell glob 好解释，也能直接写单测。
- **大文件靠流式读取，不靠一刀切拒绝**：最初 `read_file` 超过 2MB 直接拒绝，报错里却建议“用 offset/limit 分段读”，
  而分段读同样被拒——模型会在这里死循环。现在逐行流式读取，只解码要返回的行，单行超过 2000 字符截断。
  `grep` 仍跳过超过 2MB 和二进制的文件，并在结果里报告“已跳过 N 个”，否则模型会以为真的没匹配。
- **行号只按 `\n` 算**：`str.splitlines()` 还会在 `\f`、`\u2028` 等字符处分行，
  行号就和 `grep -n`、编辑器对不上。`read_file` 和 `grep` 共用 `walk.iter_lines`，两边行号一致。
- **符号链接**：`entry.is_dir(follow_symlinks=False)` 对指向目录的链接返回 False，
  不单独处理就会被当成文件列出来；指向目录和已失效的链接都跳过。
- **错误原因别吞掉**：`is_binary` 最初遇到任何 `OSError` 都返回 True，没有读权限的文件被报成“二进制文件”。
- **CRLF 文件**：`read_text` / `write_text` 默认会把 `\r\n` 转成 `\n`，`edit_file` 改一行就把整个文件的换行符换了。
  要用 `newline=""` 原样读写，并把模型给的 `\n` 换行的 old_string 转成文件的换行风格再匹配。
- **线程停不下来**：`asyncio.to_thread` 被取消时线程还在跑，`grep path=/` 会在后台跑完。
  `walk.run_in_thread` 在取消时设置一个 `threading.Event`，遍历循环检查它、尽快退出。

## 7. bash 的几个必须项

- `stdin=DEVNULL`：没有终端输入，命令卡在等 stdin 时会把整个 agent 挂住（`cat` 一秒变永久）。
- `stderr=STDOUT`：分开读的话两边顺序是乱的，模型看到的输出和真实执行顺序不一致。
- **要杀整个进程组，不是只杀 sh**：`create_subprocess_shell` 实际启动的是 `/bin/sh -c`，
  `process.kill()` 只杀掉 sh，它启动的 `sleep`、服务进程还占着输出管道，读输出会一直等下去——
  最初的实现里 `sleep 4; echo hi` 设 `timeout=1`，实际 4 秒才返回。
  现在用 `start_new_session=True` 让命令自成进程组，超时、输出过多、Ctrl+C 时都 `os.killpg` 整组杀掉。
- **Ctrl+C 也要杀**：只处理 `TimeoutError` 不够，工具调用被取消（`CancelledError`）时命令会留在后台继续跑。
- 超时用 `wait_for(shield(task))`：超时只放弃等待、不取消读取任务，杀掉进程后还能拿到中断前的部分输出。
- **输出边读边限量**：`communicate()` 把输出全部攒在内存里，`yes` 跑 60 秒能攒几 GB；
  现在最多读 10MB，超过就停止读取并终止命令。
- 退出码一定要输出：命令失败时 stdout 常常是空的，没有 `[退出码 1]` 模型会以为命令成功了。
- **API key 不能靠“不写进 os.environ”来防**：这只对 `.env` 里的 key 成立，用户自己 `export` 的 key
  照样被子进程继承，模型执行一下 `env` 就进了上下文和 trace。现在启动子进程时显式去掉配置里
  所有 profile 的 `api_key_env`（`Config.api_key_env_names()` → `ToolContext.hidden_env`）。

M2 不做危险命令识别——那是 M3 权限系统的事，这里只把 `bash` 标成 `readonly=False` 留好接口。

## 8. 测试里的两个小坑

- `@tool` 返回的是 `Tool` 对象，不是函数：测试里要 `read_file.fn(args, ctx)`。
  直接 `await read_file(...)` 会得到 `'Tool' object is not callable`。
- `monkeypatch.setattr("simpleagent.tools.read_file.MAX_OUTPUT_CHARS", 10)` 会拿到 Tool 对象——
  因为 `tools/__init__.py` 里 `from simpleagent.tools.read_file import read_file`
  把包属性 `read_file` 覆盖成了工具对象。要改模块常量就走 `sys.modules["simpleagent.tools.read_file"]`。

## 9. 现在的缺口（M3 要补的）

- 没有权限系统：`write_file` / `edit_file` / `bash` 会直接执行，没有确认、没有工作目录边界；
- 没有危险命令识别：`rm -rf` 这类命令目前畅通无阻；
- 工具结果只截断、不清理：多轮之后历史里的旧工具结果会一直堆着（M6 的上下文工程）；
- `bash` 没有输出流式上报：长命令执行期间用户看不到任何进展（`ToolContext.emit` 还没做）。
