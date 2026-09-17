# M3（权限 / 会话 / headless）+ 工作台 W3~W5 + 新建空间拆成「目录形态 × 执行者」

- 日期：2026-09-17
- 对比基线：`f508913`（Add changelog for client UI mockup name fix）
- 对应里程碑：M3、W（个人 AI 工作台 W3~W5）、W 追加：新建空间改造

## 功能变化

**新增**

- **M3 权限系统**：写操作默认询问、只读直接放行、越界与危险命令直接拒绝。权限拆成
  「判定（`Policy`）」和「询问（`Approver`）」两层：终端用 `ConsoleApprover` 交互确认，
  无人值守用 `WhitelistApprover`（不在白名单里的一律拒绝，而不是卡住等人）。
- **M3 会话持久化**：`sa --resume` 恢复上次会话；`sa sessions` 列出历史；`sa run "..."`
  单次无人值守执行。消息写 `<home>/sessions/<id>.jsonl`，只追加。
- **W3 客户端骨架**：两栏工作台（左栏空间列表 + 右栏会话），零依赖原生 JS，
  浏览器开 `http://127.0.0.1:8384/` 即用；消息流式渲染、刷新页面回到上次的会话。
- **W4 工具卡 / 审批卡 / 验证状态**：工具调用卡（可展开看输出）、审批卡
  （允许 / 拒绝 / 本次会话始终允许）、验证命令跑完出 `passed` / `failed`，写工具改动文件后
  自动降级为 `stale`（目录指纹判定，见 `spaces/verify.py`）。
- **W5 控制面板**：指挥台（`@空间名 任务` 下发 / 查状态）、消息（inbox）、备忘（todos）。
  面板任务卡靠轮询 `/api/sessions/{id}/summary` 刷新，摘要纯结构化、不额外调模型。
- **新建空间拆成两个正交维度**：`kind` 只管目录形态（generic 用 tmp / agent 进 cwd），
  `executor` 只管谁跑（`simpleagent` / `claude-code` / `opencode`），四种组合都合法；
  模型也分两栏：内置看 `profile`，外部 CLI 看 `cli_model`（缺省 = 本机默认，不注入配置）。
- 新增学习笔记 [docs/notes/M3-permissions-sessions.md](../notes/M3-permissions-sessions.md)。

**升级**

- `Agent` 的历史修改全部改走 `Session` 的方法（`add` / `add_many` / `truncate` / `record_stats`），
  写顺序会记进 JSONL，恢复时能重放出同一个结果。
- 空间定义落盘格式扩展：顶层新增 `executor` / `cli_model`，`cwd` 从 `[agent]` 段提到顶层，
  `[generic]` 与 `[agent]` 两张表可以并存（通用任务 + 外部 CLI）。旧文件自动回填，无需手工迁移。
- 验证命令两类空间都支持（generic 也能跑 `pytest`）；`Verification` 增加 `output` 字段。

**修复**

- `sa serve` 端口被占用时报 `OSError [Errno 48]` 的裸 traceback → 现在打印人话（谁占的、
  怎么查、怎么换端口），并且先抢端口再启动 Runner，不留半启动状态。
- 客户端断连（关标签页 / 断网）会在服务端刷一整段 traceback → 现在兜住
  `BrokenPipeError` / `ConnectionResetError`，SSE 连接一锤子买卖（`close_connection = True`
  必须在 `Connection` 头之后设置）。
- **启动失败会静默**：`_build_agent()` 在 try 之外时，异常被 asyncio future 吞掉，
  界面永远停在「运行中」且一条帧都收不到 → 现在发 `error` + `status(error)` 两帧并落盘状态。
- `uv run sa` 循环导入崩溃。
- 正常结束时不发 `status` 帧，客户端发送按钮一直不解禁 → `Runner._finalize()` 收口处统一广播。
- 审批帧错过就找不回（SSE 首次连接不重放）→ `PendingApprovals` 除 Future 外保留请求详情，
  `GET /api/approvals` 返回 `session_id` / `tool_name` / `arguments`，面板与会话两处都能补卡。
- 新建空间：`POST /api/spaces` 的校验异常会逃出去把连接掐断（客户端只看到
  `RemoteDisconnected` 而不是 400）→ `store.create_space()` 一起包进 try；
  `SpaceStore.create_space()` 改成**先构造再落盘**，非法组合不再留下半个空间目录。
- 新建空间：`_run_input()` 里「落盘用户消息」挪到构造 Agent 之前——起不来的时候
  用户刚发的那句话会凭空消失，现在会留在历史里，配好之后还能「重跑」。

## 函数级改动

### `src/simpleagent/permissions.py`（新增）

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `Decision` / `Scope` / `Judgment` | 新增 | 三档判定结果、工具作用域、判定输出 |
| `ApprovalRequest` / `ApprovalDecision` / `Approver` | 新增 | 审批的请求/结果数据类与异步协议（原来这些散在 `serve/approval.py`，现在下沉到核心，终端也能用） |
| `WhitelistApprover` | 新增 | 无人值守用：命中白名单放行，否则直接拒绝（不阻塞） |
| `inspect_command()` | 新增 | 危险命令识别：按 `&&`/`;`/管道拆段 → 解析 token 与目标路径 → 判是否落在 cwd / home 之外 |
| `Policy` | 新增 | 三档判定的入口，读工具声明的 `readonly` / `scope` 决定 allow / ask / deny |

### `src/simpleagent/agent/session.py`

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `SessionStore` | 新增 | JSONL 追加与会话恢复：`start` / `load` / `latest` / `list` / `delete`，`_ends_with_newline()` 处理断电写一半的行 |
| `SessionInfo` | 新增 | 会话列表项（id / 标题 / 时间 / 条数） |
| `Session.attach()` / `persisted()` | 新增 | 挂上存储后，之后每条消息都落盘 |
| `Session.add()` / `add_many()` / `truncate()` / `record_stats()` / `title()` | 新增 | 历史修改的唯一入口，保证写顺序可重放 |

### `src/simpleagent/cli.py`

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `CliError` | 新增 | 面向用户的参数错误 |
| `session_store()` | 新增 | 构造 `<home>/sessions` 下的 `SessionStore` |
| `parse_allowed()` / `resolve_resume()` / `list_sessions()` | 新增 | `sa run --allow` 解析、`--resume` 定位会话、`sa sessions` 列表 |

### `src/simpleagent/ui/headless.py`（新增）

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `Headless` | 新增 | `sa run` 的单次无人值守执行：白名单审批 + 退出码 |
| `format_usage()` | 新增 | 跑完打一行用量 |

### `src/simpleagent/ui/approve.py`（新增）

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `ConsoleApprover` | 新增 | 终端里的审批：允许 / 拒绝 / 本会话始终允许 |

### `src/simpleagent/serve/app.py`

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `Server._static()` | 新增 | 托管 `index.html` 与 `/assets/<name>` |
| `Server._meta()` | 新增 | profile 列表、默认 profile、执行者名单（含 `label` / `external` / `models`）、`max_steps` |
| `Server._limit()` / `_list_sessions()` | 新增 | `?limit=N` 取最近 N 条（默认 5，上限 200） |
| `Server._update_session()` | 新增 | 会话重命名 / 置顶（只允许 `title` / `pinned`） |
| `Server._space_files()` / `file_tree()` | 新增 | 工作目录浅层文件树（两层、封顶 300） |
| `Server._session_rerun()` | 新增 | 用最后一条用户消息重跑 |
| `Server._session_summary()` | 新增 | 任务卡的结构化摘要 |
| `Server._inbox_list/add/read`、`_todo_list/add/update/delete` | 新增 | 控制面板的消息与备忘接口 |
| `Server._list_approvals()` / `_resolve_approval()` | 新增 | 待审批列表（带详情）与审批回转 |
| `Server._create_space()` | 修改 | 白名单加 `executor` / `cli_model`；`store.create_space()` 一起包进 try，校验错误转 400 |
| `_make_handler()._dispatch()` | 修改 | SSE 断连处理、`close_connection` 顺序、keepalive |

### `src/simpleagent/serve/runner.py`

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `Runner._build_agent()` | 修改 | 非 `simpleagent` 执行者直接报错（外部 CLI 规划在 M8），**不静默回退内置 loop**；profile 不存在时给可读提示 |
| `Runner._run_input()` | 修改 | 先落用户消息再构造 Agent（起不来也不丢消息） |
| `Runner.cwd_for()` | 修改 | 不再看 `kind`：有 `cwd` 用它，否则用 `<space>/tmp` |
| `Runner._maybe_mark_stale()` | 新增 | 写工具改动文件后把已通过的验证降级为 `stale` |
| `Runner._notify()` | 新增 | 跑完往控制面板落一条系统消息 |
| `_log_future_error()` | 新增 | 兜底日志：协程里漏出的异常不再被静默吞掉 |

### `src/simpleagent/spaces/models.py`

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `EXECUTORS` / `EXECUTOR_LABELS` / `DEFAULT_CLI_COMMAND` | 新增 | 执行者名单、显示名、默认可执行文件 |
| `Space.executor` / `Space.cli_model` / `Space.cwd` | 新增 | 执行者、外部 CLI 的模型 preset、工作目录提到顶层 |
| `Space.from_spec()` | 修改 | 校验集中在这里：未知执行者 / 绑定目录缺 cwd / 通用任务却给 cwd / 内置却填 `cli_model` → `ValueError` |
| `AgentBinding` | 修改 | 缩成 `{command, args, resume_flag}`（去掉与顶层重复的 `name` / `cwd`） |
| `SpaceSpec.agent_name` → `executor` | 修改 | 入参与概念对齐 |

### `src/simpleagent/spaces/store.py`

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `_space_to_toml()` | 修改 | 写顶层 `executor` / `cli_model` / `cwd`，`[generic]` 与 `[agent]` 可并存 |
| `_space_from_toml()` | 修改 | 兼容旧文件：`[agent].name` 回填 `executor`，`[agent].cwd` 提到顶层 |
| `SpaceStore.create_space()` | 修改 | 先构造再落盘；没有 cwd 就建 tmp |
| `SpaceStore.create_session()` | 修改 | `agent` 取 `space.executor` |

### `src/simpleagent/spaces/verify.py`（新增）

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `fingerprint()` | 新增 | 目录指纹（相对路径 + mtime_ns + size 的哈希，复用 `tools/walk` 的忽略规则） |
| `changed_since()` | 新增 | 已冻结的指纹与当前是否不同——判 `stale` 用 |

### `src/simpleagent/panel/store.py`（新增）

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `PanelStore` | 新增 | inbox（只追加 `inbox.jsonl` + `read.json`）、todos（`todos.json` 整体原子写） |
| `InboxItem` / `TodoItem` | 新增 | 两类条目；todo 可引用某个会话 |

### `src/simpleagent/panel/summary.py`（新增）

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `summarize()` | 新增 | 纯结构化摘要：改了几个文件、工具调用数、报错条数、验证状态、最后一句 |
| `one_line()` | 新增 | 摘要压成一行结论 |

### `src/simpleagent/serve/static.py`（新增）

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `asset_bytes()` | 新增 | `importlib.resources` 读前端资源，只取文件名 + 后缀白名单，目录穿越在这一层掐掉 |

### `src/simpleagent/serve/approval.py`

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `PendingApprovals.add()` / `details()` | 新增 | 除了 Future 还留请求详情，供 `GET /api/approvals` 补卡 |
| `APIApprover.request()` | 修改 | 推 `approval_request` 帧后挂起，HTTP 线程唤醒 |

### 其他

| 文件 | 变化 | 说明 |
|---|---|---|
| `agent/loop.py` | 修改 | 历史修改走 `Session` 方法；`approver` / `policy` 注入；改用 `permissions.Approver`（不再依赖 serve 层） |
| `ui/repl.py` | 修改 | 支持 `session` / `store` / `approver` / `policy` 注入、`--resume`、profile 校验、`ConsoleApprover` |
| `tools/registry.py` | 修改 | 新增 `judge()`：写操作执行前先判定再询问 |
| `tools/base.py` / `bash.py` / `write_file.py` / `edit_file.py` | 修改 | 工具声明只读性与作用域；`bash_scope()` 解析命令影响范围 |
| `serve/__init__.py` | 修改 | 导出调整 |

### 前端（`src/simpleagent/web/`，全部新增）

| 文件 | 说明 |
|---|---|
| `index.html` | 两栏骨架 + 控制面板 + 新建空间向导 |
| `app.js` | 原生 DOM + `fetch` + `EventSource`；SSE 帧按类型注册监听、seq 去重、`badgeFor(sp)` 徽标、`syncModalFields()` 向导联动 |
| `styles.css` | 沿用设计文档里定过的浅色 CSS 变量 |

## 配置与依赖

- **依赖：无新增**（`pyproject.toml` / `uv.lock` 未变），后台 API 用标准库 `http.server` + 手写 SSE。
- **数据目录新增**（都会自动创建，不用手工建）：
  - `<home>/sessions/<id>.jsonl`：M3 的会话历史（只追加）
  - `<home>/panel/inbox.jsonl`、`read.json`、`todos.json`：控制面板
  - `<home>/spaces/<space-id>/`：W1 起就有的空间目录
- **`space.toml` 格式扩展**：新增 `executor` / `cli_model`，`cwd` 提到顶层。
  旧的 `[agent].name` 与 `[agent].cwd` 会被自动回填，**不需要手工迁移**。
- 需要手动处理的地方：无。`config.toml` 的字段没有变化。

## 测试

- 新增测试文件：`tests/test_permissions.py`、`tests/test_session_store.py`、`tests/test_headless.py`、
  `tests/test_cli_serve.py`、`tests/test_panel.py`、`tests/serve/test_disconnect.py`、
  `tests/serve/test_web.py`
- 修改：`tests/spaces/test_store.py`（空间形态 × 执行者四种组合 round-trip、旧 `space.toml`
  兼容、非法组合）、`tests/serve/test_serve.py`
- 测试结果：**275 passed**（`TMPDIR=$PWD/.tmp uv run pytest`），`ruff format` / `ruff check` 均干净

## 相关笔记

- [docs/notes/M3-permissions-sessions.md](../notes/M3-permissions-sessions.md)
