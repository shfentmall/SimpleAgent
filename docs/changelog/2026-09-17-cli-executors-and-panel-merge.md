# 外部 CLI 执行者（Claude Code / OpenCode）接入空间 + 合并近期活动流分支

- 日期：2026-09-17
- 对比基线：`15bb136`（Merge pull request #1 from shfentmall/workbuddy/main-ee05b98a）
- 对应里程碑：M8（提前做掉一部分）、W5 补强（合并冲突处理）

> 本次推送包含 3 个提交，其中 `835363f`（M3 权限/会话/headless + 工作台 W3~W5 + 新建空间拆成两维）
> **已有自己的记录** [2026-09-17-m3-workbench-w3-w5-and-space-wizard.md](2026-09-17-m3-workbench-w3-w5-and-space-wizard.md)，
> 不在此重复。本记录只覆盖 `729b444`（外部 CLI 执行者）和 `e0e5fa2`（合并 GitHub 上的 PR #1，
> 该 PR 对应的功能已有自己的记录 [2026-09-17-panel-recent-activity-and-done-frame.md](2026-09-17-panel-recent-activity-and-done-frame.md)，
> 这里只写合并冲突是怎么解决的）。

## 功能变化

**新增**

- **外部 CLI 执行者真正跑起来了**：空间的 `executor` 选 `claude-code` / `opencode` 时，Runner
  会以无头模式（`claude -p --output-format stream-json --verbose`、`opencode run --format json`）
  拉起对应 CLI 子进程，把它吐出来的 NDJSON 翻译成本项目自己的事件（`text_delta` /
  `tool_call_start` / `tool_result` / `message_done` 等），继续走原来那一套「先落盘再广播」的
  路径——总线、存储、SSE、前端一行都不用改，界面上感知不到对面是谁在跑。
- **`Space.permission` 权限档**（新增字段，只在外部执行者上有意义，默认 `safe`）：
  - `safe`（只读）：claude 侧砍到 `--tools Read,Glob,Grep` 并配 `--permission-mode dontAsk
    --permission-prompts none`；opencode 侧用 `OPENCODE_CONFIG_CONTENT` 注入
    `permission: {"*": "deny", read/glob/grep/lsp: "allow"}`。两者共同点：能看不能改，且**不会
    挂住等人**（无头模式没法把审批实时问回来）。
  - `full`（全放行）：claude 加 `--dangerously-skip-permissions`，opencode 加 `--auto`；不经确认
    就能改文件、跑命令，左栏卡片会挂一个红色「全放行」标记提醒风险。
  - 旧的 `space.toml` 没有 `permission` 字段时自动按 `safe` 处理，不需要手工迁移。
- **取消外部 CLI = 杀整个进程组**：CLI 自身会再 fork（opencode 每次 `run` 都起一个本地
  server），只 terminate 父进程的话子进程还攥着 stdout 管道，取消会像没生效一样；3 秒后还没退
  就升级成 `SIGKILL`。
- **对面会话可续接**：拿到对面第一次返回的 session id 后存进 `agent_session_id`，下次追问带
  `--resume` / `--session` 接上；我们自己的 jsonl 只是给人看的展示层，不是喂给对面的上下文。
- 新建空间向导新增「权限」下拉（仅外部执行者可见），`/api/meta` 的执行者列表带上
  `permissions` 选项与 `default_permission`。
- 新增学习素材：`tests/fixtures/cli/`（脱敏后的 claude / opencode 真实录制样本 + 假 CLI
  脚本），供离线回归解析器和 Runner 的 CLI 路径。
- `docs/design/client-ui.md` 新增 10.10 节，记录事件映射表、权限档对照表和几条实测踩出来的
  坑（claude 的 `result.subtype` 是 `success` 时 `is_error` 也可能是 `true`；opencode 的
  token/cost 是本步增量要自己累加等）；`docs/ROADMAP.md` 补记 M8 提前完成的这部分。

**修复（合并冲突处理）**

- `e0e5fa2` 把本地的「外部 CLI 执行者」分支和 GitHub 上刚合并的 PR #1（面板近期活动流 + 状态帧
  广播，功能本身已在
  [2026-09-17-panel-recent-activity-and-done-frame.md](2026-09-17-panel-recent-activity-and-done-frame.md)
  记过）合到了一起，冲突集中在两个文件，双方逻辑都保留：
  - `Runner._run_input()`：保留本地的「先落盘用户消息再构造 agent / 启动失败报 `error` +
    `status(error)` 两帧 / 外部 CLI 路径 `_run_cli`」，同时并入远端「开跑后广播一帧
    `status=running`（带 `space_id`）」；启动失败的 `status=error` 帧也补上了 `space_id` 和
    `reason`。
  - `Runner._finalize()`：采用远端的签名（新增仅关键字参数 `reason: str | None = None`），终态帧
    `payload` 带 `space_id` / `usage`，出错时带 `reason`（错误文案）；同时保留本地「跑完往控制面板
    发一条系统消息」（`_notify`）不变。
  - `Server._panel_summary()`：保留本地的 `today_tokens` / `unread` / `todos` 三个字段，
    并入远端的 `recent`（24 小时内结束的会话，最多 10 条，按 `updated_at` 倒序）；`running`
    列表里的每一条现在也带上 `status` / `verification`（这两个字段是远端引入的，具体实现见
    上面链接的记录，这里只是让它们和本地字段共存）。
  - 两个本地测试跟着改了预期：`tests/serve/test_web.py::test_external_executor_never_falls_back_to_builtin_loop`
    多等一帧 `status=running` 再断言 `error`；`tests/serve/test_cli_runner.py::test_cli_missing_binary_reports_clearly`
    从「断言第一帧是 error」改成「从所有帧里筛出 error 类型再断言」，因为前面可能先来一帧
    `status=running`。

## 函数级改动

### `src/simpleagent/agents/base.py`（新增）

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `SAFE` / `FULL` / `PERMISSIONS` / `PERMISSION_LABELS` | 新增 | 权限档常量与展示文案 |
| `CliTurn` | 新增 | 一行 NDJSON 翻译出来的结果：`events` / `finished` / `ok` / `error` |
| `CliAdapter`（Protocol） | 新增 | 适配器协议：`command()` 拼命令行、`env()` 给额外环境变量、`parse()` 翻译一行事件 |

### `src/simpleagent/agents/claude.py`（新增）

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `ClaudeAdapter` | 新增 | claude 的适配器实现，跨事件状态（`session_id` / `usage` / `cost_usd` / 已流式吐出的文本）挂在实例上 |
| `ClaudeAdapter.command()` | 新增 | 拼 `claude --output-format stream-json --verbose --include-partial-messages [权限参数] -p -- <prompt>` |
| `ClaudeAdapter.env()` | 新增 | 目前恒返回 `{}`（权限全靠命令行参数） |
| `ClaudeAdapter.parse()` / `_system()` / `_stream_event()` / `_assistant()` / `_tool_results()` / `_result()` | 新增 | 按事件类型分发解析；`_result()` 只看 `is_error` 判成败，不看 `subtype`（没登录时 `subtype` 仍是 `success`） |
| `_text_of()` | 新增 | `tool_result.content` 兼容字符串和 text 块数组两种形态 |

### `src/simpleagent/agents/opencode.py`（新增）

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `OpenCodeAdapter` | 新增 | opencode 的适配器实现，`_buffer` 攒当前步的文本，`step_finish` 时收口 |
| `OpenCodeAdapter.command()` | 新增 | 拼 `opencode run --format json [--auto] [--model ...] [--session ...] -- <prompt>` |
| `OpenCodeAdapter.env()` | 新增 | `safe` 档注入 `OPENCODE_CONFIG_CONTENT`（只覆盖 `permission`，provider/模型配置照旧读用户自己的） |
| `OpenCodeAdapter.parse()` / `_text()` / `_tool_use()` / `_step_finish()` / `_error()` | 新增 | `_tool_use()` 一个事件拆成 `tool_call_start` + `tool_result` 两个帧；`_step_finish()` 把本步增量的 token/cost 累加进实例状态 |

### `src/simpleagent/agents/__init__.py`（新增）

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `ADAPTERS` | 新增 | `executor` 名到适配器类的映射 |
| `adapter_for()` | 新增 | 按执行者名造一个适配器实例（一次运行一份，未知执行者抛 `ValueError`） |

### `src/simpleagent/serve/runner.py`

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `Runner.__init__()` | 修改 | 新增 `_procs`（外部 CLI 正在跑的子进程）、`_cancelled`（主动取消标记）两个实例字典/集合 |
| `Runner.cancel()` | 修改 | 内置 loop 之外新增外部 CLI 分支：标记 `_cancelled` 后调度 `_terminate` |
| `Runner._terminate()` | 新增 | 先 `SIGTERM` 整个进程组，3 秒后若还没退出再 `SIGKILL` |
| `Runner._killpg()` | 新增（静态） | `os.killpg` 失败时退化为直接给进程发信号 |
| `Runner._run_input()` | 修改 | 按 `space.executor` 分两条路径：`simpleagent` 走原内置 loop，其余走新的 `_run_cli()`；两条路径产出统一走 `_on_event` 落盘/广播；（合并冲突处理见上）额外广播 `status=running` 帧、错误帧带 `space_id`/`reason` |
| `Runner._run_cli()` | 新增 | 拉起外部 CLI 子进程（独立进程组、4MB 行长上限），逐行 `adapter.parse()`，把翻译出来的事件转给 `_on_event`；记录对面 session id 用于 resume；进程无最终事件时按退出码兜底判成败 |
| `Runner._drain()` | 新增（静态） | 把子进程 stderr 读干净（不读会写满管道卡死子进程），只留最后 4000 字节用于报错文案 |
| `Runner._build_agent()` | 修改 | 报错文案微调：明确「该执行者要走 `_run_cli`，`_build_agent` 只服务内置 loop」 |
| `Runner._finalize()` | 修改（合并冲突处理） | 新增仅关键字参数 `reason: str \| None = None`，`error` 终态帧带上这个原因 |

### `src/simpleagent/serve/app.py`

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `Server._meta()` | 修改 | 执行者列表每项新增 `permissions`（外部执行者两档）与 `default_permission`（恒为 `safe`） |
| `Server._create_space()` | 修改 | 新建空间的白名单字段加入 `permission` |
| `Server._panel_summary()` | 修改（合并冲突处理） | 保留本地字段 `today_tokens` / `unread` / `todos`，并入远端引入的 `recent` 列表；`running` 条目补上 `status` / `verification`（这两项的实现细节已在 panel-recent-activity 记录里写过，这里不重复） |

### `src/simpleagent/spaces/models.py`

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `SpaceSpec.permission` / `Space.permission` | 新增字段 | 权限档，默认 `SAFE` |
| `Space.from_spec()` | 修改 | 新增校验：内置执行者不许填非 `SAFE` 的 `permission`；`permission` 必须在 `PERMISSIONS` 里 |

### `src/simpleagent/spaces/store.py`

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `DEFAULT_PERMISSION` | 新增（模块常量） | 值为 `"safe"`，旧文件缺省按它处理 |
| `_space_to_toml()` | 修改 | 外部执行者才写 `permission` 字段 |
| `_space_from_toml()` | 修改 | `permission = data.get("permission") or DEFAULT_PERMISSION` |

### `src/simpleagent/web/`（前端，无独立构建/测试，按函数列出关键改动）

| 文件 / 函数 | 变化 | 说明 |
|---|---|---|
| `app.js` `renderSpaces()` / `renderHeader()` | 修改 | 外部执行者且 `permission=full` 时挂一个红色「全放行」标记（带提示文案） |
| `app.js` `syncModalFields()` | 修改 | 新建空间向导：外部执行者时显示权限下拉，按 `/api/meta` 返回的 `permissions` 渲染选项 |
| `app.js` `createSpace()` / `boot()` | 修改 | 提交时带上 `permission` 字段；给权限下拉挂上 `onchange` 联动 |
| `index.html` | 修改 | 新增权限字段的 `<select>` 与提示文案 |
| `styles.css` | 修改 | 新增 `.warn-chip` 样式（红底红字的「全放行」标记） |
| **待确认** | — | 目前 `app.js` 还没有读取 `/api/panel/summary` 返回的 `recent` 字段，控制面板的近期活动流暂未接到前端界面上 |

## 配置与依赖

- **依赖：无新增**（`pyproject.toml` / `uv.lock` 未变）。
- **`space.toml` 格式扩展**：外部执行者的空间新增 `permission` 字段（`safe` / `full`）；旧文件
  没有这个字段时自动按 `safe`（只读）处理，**不需要手工迁移**。
- **需要手动处理**：
  - 想用 `claude-code` / `opencode` 作为空间执行者，需要本机已经装好对应命令行工具
    （`claude` / `opencode`）并完成登录/鉴权（比如 claude 要先 `/login`），否则会收到
    「找不到可执行文件」或对应 CLI 报的运行失败错误帧，不会自动回退到内置 loop。
  - 新建这类空间时默认权限档是 `safe`（只读，不会写文件/跑命令，也不会因为等审批而卡住）；
    需要外部 agent 能改文件、跑命令时要在新建空间向导里手动选「全放行」，界面会用红色标记
    提示这个空间不受我们审批卡约束。
  - 没有发现疑似 API key 或密钥字符串被写入本次改动的代码或测试样本（`tests/fixtures/cli/`
    的录制样本已按 README 说明脱敏：临时目录、session id、uuid、本机用户名与 home 路径都替换
    成了固定值）。

## 测试

- 新增：`tests/test_cli_adapters.py`——`ClaudeAdapter` / `OpenCodeAdapter` 的解析器单测（19 个用例）：
  未登录场景判定为失败而非成功、`assistant` 各类内容块翻译、工具结果藏在 `user` 消息里的解析、
  流式文本不被整段消息重复、命令行拼装、cache token 计入 prompt、opencode 端到端样本回放、
  `step_finish` 收口、`safe` 档权限配置、命令与 resume 参数、`adapter_for()` 拒绝内置执行者。
- 新增：`tests/serve/test_cli_runner.py`——用假 CLI 脚本 + 录制样本跑 Runner 的 CLI 路径端到端
  （8 个用例）：事件翻译且全部落盘、续问带上对面 session id、失败样本转成 error 帧、
  可执行文件缺失时报错清晰、取消会真的杀掉子进程、`full` 档带上危险参数、产物只写进空间目录
  不碰真实 `~/.simpleagent`。
- 修改：`tests/serve/test_web.py`——`test_meta` 补充权限选项断言；
  `test_external_executor_fails_loudly` 改造成 `test_external_executor_never_falls_back_to_builtin_loop`
  （用不存在的可执行文件验证内置 loop 完全没被调用），合并后再加一帧 `status=running` 的预期。
- 新增（非代码）：`tests/fixtures/cli/`——`README.md`（录制与脱敏方法说明）、
  `claude-not-logged-in.jsonl`、`opencode-bash-pwd.jsonl`（脱敏后的真实录制样本）、
  `fake_cli.sh`（离线假 CLI，用环境变量控制吐哪份样本/延迟/退出码）。
- 测试结果：`uv run pytest -q` **297 passed**；`uv run ruff check` 全部通过；
  `uv run ruff format --check` 96 个文件均已是期望格式，无需改动。工作区干净
  （`git status` 显示已提交 3 个未推送的提交，无未跟踪文件）。

## 相关笔记

无（外部 CLI 执行者的机制细节已写进 `docs/design/client-ui.md` 10.10 节，不单独开学习笔记）。
