# 工作台 W1 + W2：Space/会话持久化 + 本地 HTTP + SSE API

- 日期：2026-09-17
- 对比基线：`aca3e1b`（Complete M2: file, search and bash tools with output truncation）
- 对应里程碑：W 个人 AI 工作台（W1 空间/会话持久化、W2 本地 API 已实现；W3 客户端骨架待做）

## 功能变化

- 新增：`spaces` 模块（W1）——落地设计文档里的持久化层。`Space`（一类任务的容器：名字 + 工作目录 + 用哪个 agent + 验证方式）存成 `spaces/<id>/space.toml`（唯一真值，标准库 `tomllib` 读、手写字符串写，无新依赖）；会话消息只追加写进 `spaces/<id>/sessions/<sid>.jsonl`；可变的会话元信息（标题/状态/置顶/验证状态）单独存 `spaces/<id>/sessions/<sid>.meta.json` sidecar；`SpaceStore.list_sessions()` 默认只返回最近 5 个 session，置顶和正在运行的不占名额、永远显示。
- 新增：`serve` 模块（W2）——`sa serve` 启动本地 HTTP + SSE API，供未来的桌面客户端连接。技术选型走“零新依赖”方案：标准库 `http.server`（`ThreadingHTTPServer`）手写路由和 SSE，不引入 FastAPI/uvicorn 之类框架。
  - 事件总线（`EventBus`）按 session 分发事件帧，`seq` 单调递增作为 SSE 的 `id`，客户端断线后带 `Last-Event-ID` 重连可以重放期间错过的帧（历史帧每 session 最多保留 2000 条）。
  - `Runner` 在独立线程里跑 asyncio 事件循环，按空间配置构造 `Agent` 并执行一轮对话，事件先落盘（消息追加进 jsonl）再广播到总线，避免“刷新页面消息丢了”的竞态；支持取消（调用 `Agent.cancel()`）和手动触发验证命令（`subprocess.run` 跑在线程池里）。
  - 审批桥（`APIApprover`/`PendingApprovals`）：写操作执行前通过总线推一帧 `approval_request`，挂起一个 `asyncio.Future` 等客户端 `POST /api/approvals/{id}` 回决策（allow/deny/always），`always` 按 session 维度跨轮记忆。
  - HTTP 路由覆盖空间的增删查改、会话的创建/取消/发消息/验证/标记已验证、SSE 事件流、审批列表与决策、控制面板汇总（`/api/panel/summary`）。
- 升级：`Agent` 新增 `approver` 参数和 `cancel()` 方法，`ToolContext` 新增 `session_id`、`approver` 字段，`ToolRegistry` 新增 `approver` 字段，非只读工具执行前先经审批器确认（不传审批器时行为和原来一样）。这是 W2 复用核心 loop 的接入点，不改变 REPL 现有行为。
- 升级：`sa` CLI 新增 `serve` 子命令（`--host` 默认 `127.0.0.1`，`--port` 默认 `8384`），`Ctrl+C` 停止。
- 新增：设计文档 `docs/design/client-ui.md`（两栏桌面客户端的界面结构、术语模型、本地 API 6.1/6.2 协议设计）和配套的静态 HTML 原型 `docs/design/client-ui-mockup.html`。
- 修复：`space.toml` 和 `<sid>.meta.json` 改成原子写入（先写临时文件再 `os.replace`）。原来用 `write_text` 覆盖写，文件会先被清空，`sa serve` 里 HTTP 线程读 meta 时如果 runner 线程正在写，就会读到空文件报 `JSONDecodeError`；`test_runner_fake_llm_stream` 偶发失败就是这个原因。

## 函数级改动

### `src/simpleagent/spaces/models.py`（新增文件）

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `GenericConfig` | 新增 | 通用任务空间配置：`tmp_dir`（默认 `"auto"`，即 `spaces/<id>/tmp`） |
| `AgentBinding` | 新增 | 绑定外部 agent 的配置：`name`/`command`/`args`/`cwd`/`resume_flag` |
| `VerifyConfig` | 新增 | 验证命令配置：`command`/`trigger`（`off`/`on_stop`/`on_turn`）/`timeout` |
| `Verification` | 新增 | 单次验证结果：`status`（`unknown`/`running`/`passed`/`failed`/`stale`）、`exit_code`、时间戳、输出落盘引用、目录指纹、来源（`auto`/`manual`） |
| `SessionMeta` | 新增 | 会话可变元信息：标题、状态、置顶、绑定 agent、用量、验证状态 |
| `SpaceSpec` | 新增 | 新建空间的入参（来自向导），覆盖 generic/agent 两类空间的全部字段 |
| `Space` | 新增 | 空间定义：id/名字/kind/profile/是否打开/置顶/保留 session 数等；`Space.from_spec()` 按 `kind` 构造对应的 `generic`/`agent` 配置；`to_dict()` 用于序列化 |

### `src/simpleagent/spaces/store.py`（新增文件）

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `new_id(prefix)` | 新增 | 生成 `<prefix>_<毫秒时间戳>_<3 字节随机 hex>` 形式的 id |
| `_write_atomic(path, text)` | 新增 | 先写同目录临时文件（`<name>.<随机>.tmp`，不会被 `*.meta.json` 匹配到）再 `os.replace`，读者只会看到旧内容或新内容；`_write_space_toml`、`_write_meta` 都走它 |
| `_toml_str` / `_space_to_toml` / `_space_from_toml` | 新增 | 手写 TOML 序列化/反序列化（标准库无写 TOML 能力，读用 `tomllib`） |
| `_user_text(msg)` | 新增 | 从消息里提取纯文本，兼容字符串和多 block 的 `content` |
| `SpaceStore.list_spaces(opened_only=True)` | 新增 | 列出空间，按 `last_opened_at` 倒序 |
| `SpaceStore.get_space` / `create_space` / `update_space` / `close_space` / `delete_space` | 新增 | 空间的增删查改；`close_space` 只是把 `opened` 置 `False`，不删数据 |
| `SpaceStore.list_sessions(space_id, limit=5, include_pinned=True)` | 新增 | 置顶和 `running` 状态的 session 永远在列表里、不占 `limit` 名额，其余按 `updated_at` 倒序取前 `limit` 个 |
| `SpaceStore.create_session` / `get_session_meta` | 新增 | 创建会话（写 meta + 建空 jsonl）、按 id 读 meta |
| `SpaceStore.find_session_space(session_id)` | 新增 | 跨空间按 session id 反查所属空间（session id 全局唯一），供 `GET /api/sessions/{id}` 之类不带 space id 的接口用 |
| `SpaceStore.load_session(space_id, session_id)` | 新增 | 从 jsonl 重建 `agent.session.Session`（消息历史 + `Usage`） |
| `SpaceStore.append_message(space_id, session_id, msg)` | 新增 | 追加写消息到 jsonl，顺手更新 meta 的 `updated_at`，首条用户消息自动截断成标题（40 字） |
| `SpaceStore.update_meta(space_id, session_id, **fields)` | 新增 | 更新 meta 里允许的字段（标题/状态/置顶/agent/agent_session_id/用量/验证），未知字段报错 |

### `src/simpleagent/spaces/__init__.py`（新增文件）

模块导出入口，`__all__` 暴露 `SpaceStore`、`Space`、`SpaceSpec`、`SessionMeta`、`Verification` 等 models 里的类型。

### `src/simpleagent/serve/bus.py`（新增文件）

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `Frame` | 新增 | 一帧事件：`session_id`/`type`/`payload`，`seq`/`ts` 由总线在 `publish` 时填写；`to_json()` 转成可序列化 dict |
| `EventBus.subscribe(session_id, last_event_id=None)` | 新增 | 订阅一个 session，返回 `(队列, 重放帧列表)`；重放帧 = 历史里 `seq` 大于 `last_event_id` 的帧 |
| `EventBus.unsubscribe` | 新增 | 从订阅列表移除对应队列 |
| `EventBus.publish(frame)` | 新增 | 分配自增 `seq`/`ts`，记入历史（超过 `HISTORY_LIMIT=2000` 条清掉最旧的），投递给所有订阅队列 |
| `EventBus.next_seq(session_id)` | 新增 | 只读预览下一个 `seq`，供测试/提示用 |
| `frame_to_sse(frame)` | 新增 | 把一帧渲染成 SSE 文本（`id`/`event`/`data` 三行），`seq` 作为事件 `id` |

### `src/simpleagent/serve/frames.py`（新增文件）

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `event_to_frame(event, session_id)` | 新增 | 把核心 `Event`（`TextDelta`/`ReasoningDelta`/`MessageDone`/`ToolCallStart`/`ToolResult`/`MaxStepsReached`）映射成总线帧；未识别类型兜底为 `unknown` 帧 |
| `status_frame` / `error_frame` / `approval_request_frame` / `verification_frame` | 新增 | 服务端自己发出的补充帧（会话状态变化、出错、等待审批、验证完成） |

### `src/simpleagent/serve/approval.py`（新增文件）

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `ApprovalDecision` | 新增 | 决策结果：`allow`/`always` |
| `Approver` | 新增 | 审批器的异步 `Protocol`：`async def request(*, session_id, tool_name, arguments) -> ApprovalDecision` |
| `_new_id(prefix)` | 新增 | 生成审批 id |
| `PendingApprovals.add/resolve/pending_ids` | 新增 | 在 runner 的 asyncio 线程里管理挂起的审批 `Future`：`add` 建 Future，`resolve` 由 HTTP 层收到客户端决策后调用来 `set_result` |
| `APIApprover.request(...)` | 新增 | 若该工具已在本 session 被标记“始终允许”直接放行；否则生成审批 id、推 `approval_request` 帧、`await` Future 等决策；被取消时把挂起的审批标记拒绝，避免泄漏；`always` 决策写入按 session 维度共享的 `always_store` |

### `src/simpleagent/serve/runner.py`（新增文件）

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `_action_to_decision(action)` | 新增 | 把 HTTP 层传来的 `allow`/`deny`/`always` 字符串转成 `ApprovalDecision` |
| `Runner.start/_run_loop/shutdown` | 新增 | 起一个独立线程跑 `asyncio` 事件循环（`run_forever`），`shutdown` 时 `call_soon_threadsafe(loop.stop)` 并 join 线程 |
| `Runner._schedule(coro)` | 新增 | 用 `run_coroutine_threadsafe` 把协程扔进 runner 的事件循环 |
| `Runner.run_input/cancel/approve/verify` | 新增 | 对 HTTP 层暴露的同步接口：分别调度一次对话、取消对应 session 的 `Agent`、把审批决策灌进 `PendingApprovals`、调度一次验证 |
| `Runner._run_input(space_id, session_id, user_input)` | 新增 | 核心协程：按空间构造 `Agent`，落盘用户消息并把状态置 `running`，逐个事件转发给 `_on_event`；正常结束/被取消/异常三种收尾分别落盘状态为 `done`/`cancelled`/`error` 并发对应帧 |
| `Runner._on_event(space_id, session_id, event)` | 新增 | 先落盘（`MessageDone`/`ToolResult` 追加进 jsonl）再广播到总线，避免竞态 |
| `Runner._finalize(space_id, session_id, session, status)` | 新增 | 把最终状态和用量写回 meta |
| `Runner._build_agent(space, session)` | 新增 | 按空间的 `profile` 造 LLM、`ToolRegistry`（内置工具 + 审批器）、`cwd`、`system_prompt`，组装成一个新的 `Agent` |
| `Runner._cwd_for(space)` | 新增 | agent 空间用绑定的 `cwd`；generic 空间用 `spaces/<id>/tmp` |
| `Runner._verify(space_id, session_id)` | 新增 | 用 `asyncio.to_thread(subprocess.run, ...)` 跑验证命令（放线程池避免 asyncio 子进程 watcher 在非主线程 loop 上的问题），超时/异常都发 `error` 帧，成功则按退出码判定 `passed`/`failed`，落盘并发 `verification` 帧 |
| `Runner._default_llm_factory(name, profile)` | 新增 | 默认的 LLM 构造函数，`Tracer` 目录名固定为 `"serve"`（和 REPL 的 trace 分开存） |

### `src/simpleagent/serve/app.py`（新增文件）

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `Response` | 新增 | 统一的响应结构：状态码 + JSON body 或 SSE 帧生成器 |
| `Server.__init__/start` | 新增 | 组装 `SpaceStore`/`Runner`，`start()` 启动 runner 线程 |
| `Server.handle(method, path, headers, body)` | 新增 | 正则匹配路由分发：空间增删查改（`/api/spaces[...]`）、会话创建/列表/消息/输入/取消/验证/标记已验证（`/api/sessions[...]`）、SSE 事件流（`/api/sessions/{id}/events`）、审批列表与决策（`/api/approvals[...]`）、控制面板汇总（`/api/panel/summary`），未匹配到返回 404 |
| `Server._list_spaces` / `_space_view` / `_create_space` / `_space_detail` / `_update_space` / `_delete_space` | 新增 | 空间相关的路由处理函数；`_space_view` 附带该空间最近 5 个 session 的摘要 |
| `Server._list_sessions` / `_create_session` / `_session_messages` / `_session_input` / `_session_cancel` / `_session_verify` / `_mark_verified` | 新增 | 会话相关的路由处理函数；`_session_input` 校验 `text` 非空后异步调度、立即返回 202；`_mark_verified` 用于客户端手动确认验证结果（`source="manual"`） |
| `Server._sse(session_id, headers)` | 新增 | 建立 SSE 连接：按 `Last-Event-ID` 订阅并先重放历史帧，之后阻塞从队列取新帧（15 秒无消息发一次 keepalive 注释行），连接断开时 `finally` 里退订 |
| `Server._list_approvals` / `_resolve_approval` | 新增 | 列出挂起的审批 id、把客户端决策转发给 runner |
| `Server._panel_summary` | 新增 | 汇总所有空间里状态为 `running` 的 session，以及已打开空间数 |
| `Server._safe_json(body)` | 新增 | 安全解析请求体 JSON，失败返回 `None` |
| `_make_handler(app)` / `Handler._dispatch/do_GET/do_POST/do_PATCH/do_DELETE/log_message` | 新增 | 适配 `http.server.BaseHTTPRequestHandler`：读请求体、调 `Server.handle`、按是否为 SSE 流式或一次性写回响应；`log_message` 静音默认的访问日志 |
| `make_server(config, host="127.0.0.1", port=8384, llm_factory=None)` | 新增 | 构造并 `start()` 一个 `Server`，包一层 `ThreadingHTTPServer` 返回（调用方负责 `serve_forever()`） |

### `src/simpleagent/serve/__init__.py`（新增文件）

模块导出入口，`__all__` 暴露 `APIApprover`/`ApprovalDecision`/`Approver`/`EventBus`/`Frame`/`PendingApprovals`/`Runner`。

### `src/simpleagent/agent/loop.py`

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `Agent.__init__` | 修改 | 新增 `approver: Approver \| None = None` 参数并保存；新增 `self._task: asyncio.Task \| None = None` |
| `Agent.cancel()` | 新增 | 取消当前正在跑的这轮对话（对应 HTTP 的 `/cancel`）：`self._task.cancel()`，loop 内部 `except BaseException` 会先修好历史再把 `CancelledError` 原样抛出 |
| `Agent.run(session, user_input)` | 修改 | 记录 `self._task = asyncio.current_task()`；构造 `ToolContext` 时新增 `session_id=session.id`、`approver=self.approver` |

### `src/simpleagent/tools/base.py`

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `ToolContext` | 修改 | 新增字段 `session_id: str \| None = None`（持久化/事件路由用）、`approver: Approver \| None = None`（`None` 表示不审批，沿用旧行为） |

### `src/simpleagent/tools/registry.py`

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `ToolRegistry.__init__` | 修改 | 新增 `approver: Approver \| None = None` 参数并保存 |
| `ToolRegistry.execute(tool_call, ctx)` | 修改 | 参数校验通过后，若 `self.approver` 不为 `None` 且工具不是只读（`readonly=False`），先 `await self.approver.request(...)`，被拒绝则直接返回错误结果“工具 {name} 需要人工审批，已被拒绝”，不执行工具 |

### `src/simpleagent/cli.py`

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `main(argv)` | 修改 | `argparse` 新增 `serve` 子命令（`--host` 默认 `127.0.0.1`、`--port` 默认 `8384`）；命中时延迟导入 `simpleagent.serve.app.make_server` 构造并 `serve_forever()`，`KeyboardInterrupt` 时打印“已停止”并正常退出 |

## 配置与依赖

- `pyproject.toml`、`uv.lock` 没有变化，无新增第三方依赖，无需 `uv sync`。
- 新数据目录 `<SIMPLEAGENT_HOME>/spaces/`：首次创建空间时自动生成，不需要手动处理。
- 无需改 `~/.simpleagent/config.toml` 或 `.env`；`sa serve` 复用现有 `config.toml` 里的 profile 配置。

## 测试

- 新增：`tests/spaces/test_store.py`（13 个测试函数，覆盖 `new_id` 唯一性、generic/agent 两类空间创建及目录布局、`opened_only` 过滤、置顶+运行中不占最近 5 个名额、标题从首条用户消息截断、多 block content 取标题、`load_session` 重建消息、同一 `cwd` 可被多个空间复用、`delete_space`、重启后从磁盘重建（持久化跨进程有效）、`update_meta` 拒绝未知字段；以及回归测试 `test_meta_readers_never_see_partial_write`：一个线程反复 `update_meta`、另一个线程同时读，断言读不到半截文件、不残留 `.tmp`）
- 新增：`tests/serve/test_serve.py`（6 个测试函数，覆盖事件总线的顺序性与 `Last-Event-ID` 重放、审批请求/决策的异步往返、`Runner` 配合 `FakeLLM` 跑一轮流式对话并核对落盘、审批拒绝/允许两种流程、手动触发验证命令、完整走一遍 HTTP + SSE（真实起 `ThreadingHTTPServer`、用 `urllib` 发请求）；全部不联网）
- 测试结果：`uv run pytest -q` → **177 passed**（连跑 3 次一致）。
- 并发修复的验证：回归测试在修复前的代码上 3 次运行 3 次失败，修复后 3 次都通过；`tests/serve` + `tests/spaces` 在修复前连跑 40 次失败 1 次（`JSONDecodeError`），修复后连跑 40 次 0 失败。
- `uv run ruff check` → All checks passed
- `uv run ruff format` → 68 files left unchanged

## 相关笔记

- 无（W1/W2 的学习笔记按仓库约定在整个 W 里程碑做完后统一写）
