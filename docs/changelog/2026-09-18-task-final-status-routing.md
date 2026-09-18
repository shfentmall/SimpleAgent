# 任务终态分流：只有失败进消息，完成/取消只在指挥台显示；指挥台刷新后从后台恢复

- 日期：2026-09-18
- 对比基线：`cf42c86`（Merge pull request #5 from shfentmall/claude/project-cli-packaging-8a483d）
- 对应里程碑：W5（控制面板）

## 功能变化

- 升级：`Runner` 按终态级别分流通知。完成（success）、取消（warn）不再往控制面板的消息（inbox）里落一条，只有失败（error）才落；此前每次任务完成都会在消息里多一条和指挥台任务卡重复的记录，左栏未读角标被刷高，真正的失败反而被淹没。验证未通过的消息仍然不变，照旧进消息。
- 升级：失败原因跟着收口点走。内置 loop 的异常文案、外部 CLI 的报错（登录失效、找不到可执行文件、退出码 + stderr 末尾等）现在同时写进 status 帧的 `reason` 字段和消息正文，之前消息正文只有一句「运行出错，去那个会话的日志 tab 看原因」。
- 修复：启动失败（profile 不存在、没配 API key）原来绕开了收口点 `_finalize`，自己落盘、自己发帧，导致这类最该提醒的失败一条消息都不会进 inbox。现在统一走 `_finalize`，会正常落消息。
- 升级：控制面板指挥台的任务卡不再只活在页面内存里。打开面板、刷新统计时，会用 `/api/panel/summary` 返回的 `running` + `recent`（24 小时内的终态）把缺的卡片补回来，刷新页面不再丢失任务卡；补回来的任务也包括在对话视图里直接跑的 session，不局限于本页 `@` 下发的。
- 升级：任务卡第二行改为先写明终态（完成 / 已取消 / 出错）再接摘要，不再只靠左边的色带区分状态。
- 升级：指挥台空状态文案从「还没有下发任务」改为「24 小时内没有任务」，和恢复窗口口径一致。
- 修复：`dispatch()` 插入新任务卡前，先从 `panelState.dispatch` 里剔除同一 session 已存在的卡片，避免两次异步请求之间被轮询/恢复逻辑重复插入。
- 文档：`docs/design/client-ui.md` 新增 10.12 节，记录本次终态分流和指挥台恢复的设计口径与取舍（门槛未做成配置项、卡片没有手动清除按钮）。
- 文档：`README.md` 补充项目背景（个人日常用 AI 的两种方式：按目录启动 CLI、随时网页 chat）和更明确的目标描述，替换原先的简短「目标」段落。此改动来自用户此前在本地 `main` 分支上的提交（`5c6d339`「update」+ 合并提交 `105a4f4`），本次一并推送。

## 函数级改动

### `src/simpleagent/serve/runner.py`

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `FINAL_NOTICE`（模块级字典） | 新增 | 终态 → (消息级别, 标题用词) 的映射：`done` → success/完成，`cancelled` → warn/已取消，`error` → error/失败。 |
| `INBOX_LEVELS`（模块级常量） | 新增 | `frozenset({"error"})`，只有落在这个集合里的级别才进控制面板消息，是本次分流的唯一门槛。 |
| `Runner._run_input()`（启动失败分支） | 修改 | 原来自己调 `store.update_meta` + 发 `status_frame` 绕开收口点，现在改为调用 `self._finalize(..., "error", reason=message)`，统一走收口逻辑。 |
| `Runner._run_input()`（主体） | 修改 | 新增局部变量 `reason: str | None`；内置 loop 路径固定 `reason=None`；外部 CLI 路径改为接收 `_run_cli` 返回的 `(status, reason)` 二元组；`_finalize` 调用统一带上 `reason=reason`。 |
| `Runner._finalize()` | 修改 | 收口逻辑不变（落盘终态 + 广播 status 帧），但通知判断从 `status in ("done", "error", "cancelled")` 改为 `status in FINAL_NOTICE`，且调用 `_notify` 时不再传 `session`，改传 `status` 和 `reason`。文档字符串同步更新为「失败时往控制面板的消息里落一条」。 |
| `Runner._notify()` | 修改 | 签名从 `(space_id, session_id, session, status)` 改为 `(space_id, session_id, status, reason)`。新增分流判断：`level, head = FINAL_NOTICE[status]`，`level not in INBOX_LEVELS` 时直接返回，不再落消息；不再调用 `panel.summary` 的 `one_line`/`summarize` 生成完成摘要（这部分逻辑随之从本文件的 import 中移除），失败消息正文改为直接使用 `reason`（没有则兜底为原来的固定文案）。 |
| `Runner._run_cli()` | 修改 | 返回类型从 `str` 改为 `tuple[str, str | None]`（状态, 失败原因）。`FileNotFoundError`、`OSError`、非零退出码三处失败分支都把拼好的错误文案通过第二个返回值带出去；`cancelled` 分支返回 `("cancelled", None)`；成功分支返回 `("done", None)`。文档字符串同步说明返回值包含失败原因。 |

（`from simpleagent.panel.summary import one_line, summarize` 这一行 import 被删除，因为完成时不再需要生成一句话摘要写进消息。）

### `src/simpleagent/web/app.js`

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `restoreDispatch(summary)` | 新增 | 从 `/api/panel/summary` 的 `running` + `recent` 里挑出不在 `panelState.dispatch` 里的 session，各拉一次 `/api/sessions/{id}/summary` 补摘要后插入指挥台任务卡列表，按 `at`（时间戳）倒序排列；插入前后都去重，避免和并发的下发/轮询冲突。无新卡片时返回 `false`。 |
| `dispatchLine(d, cls)` | 新增 | 拼任务卡第二行的状态文案：等待批准 / 运行中… / `终态 · 摘要`（终态取自 `STATUS_TEXT`）。 |
| `loadPanel()` | 修改 | 渲染统计和备忘之后新增 `await restoreDispatch(summary)` 调用，再渲染指挥台，使打开面板时能恢复任务卡。 |
| `loadStatsOnly()` | 修改 | 拉取面板统计后调用 `restoreDispatch`，有新卡片时才重新 `renderDispatch()`。 |
| `renderDispatch()` | 修改 | 空状态文案从「还没有下发任务」改为「24 小时内没有任务」；卡片第二行改为调用新增的 `dispatchLine()`；`when` 字段的计算兼容 `startedAt` 为 `null`（恢复出来的运行中卡片不知道起始时间，不显示已用时）。 |
| `dispatch()` | 修改 | 新建卡片对象里增加 `at` 字段（排序用的统一时间戳，区别于 `startedAt` 已用时计算）；建会话、发输入两次请求完成后、插入新卡片前，从 `panelState.dispatch` 里先剔除同一 `sessionId` 的旧卡片，避免和恢复/轮询逻辑产生重复卡片；只 `@` 不带任务描述的状态卡分支不再手工拼 `line` 字段，交给 `dispatchLine()` 统一处理。 |

## 配置与依赖

- 无配置项、依赖、数据目录变化，无需手动处理。

## 测试

- `tests/serve/test_serve.py`：`test_runner_emits_status_done` 补充断言完成后 `runner.panel.list_messages() == []`；`test_runner_emits_status_cancelled` 补充同样的空消息断言；新增 `test_runner_startup_error_goes_to_inbox`，验证启动失败（profile 不存在）会走 `_finalize`、status 帧带 `reason`、消息正文和标题符合预期。
- `tests/serve/test_cli_runner.py`：`test_cli_failure_becomes_an_error_frame` 补充断言 status 帧的 `reason` 包含错误信息，且控制面板消息的级别是 `error`、正文包含失败原因。
- 另有浏览器手动验证（临时 `SIMPLEAGENT_HOME` + `tests/fixtures/cli` 下的假 CLI，不联网）：成功任务只出现在指挥台，失败任务进消息且正文是「Not logged in · Please run /login」，刷新页面后任务卡从后台恢复，运行中的任务刷新页面后恢复为运行中状态，跑完后正确变为完成。
- 测试结果：`uv run pytest -q` → 314 passed；`uv run ruff check` → All checks passed；`uv run ruff format --check` → 101 files already formatted（全部通过）。

## 相关笔记

- 无
