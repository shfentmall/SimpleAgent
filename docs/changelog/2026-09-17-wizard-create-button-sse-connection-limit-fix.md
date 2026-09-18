# 修复新建空间「创建」按钮点了没反应（浏览器 SSE 连接被占满）

- 日期：2026-09-17
- 对比基线：`06b9692`（Merge pull request #2 from shfentmall/claude/space-creator-permission-dropdown-7f6a1e）
- 对应里程碑：工作台（W3 前端的连接管理修复）

## 功能变化

- 修复：新建空间向导里点「创建」没反应。根因不在向导逻辑本身：每个工作台标签页都常驻一条
  SSE 长连接，Chrome 对同一个 host 最多允许 6 条 HTTP/1.1 连接，开到 6 个标签页后
  `POST /api/spaces` 在浏览器里排队等连接槽位，既不报错也不超时，界面上就是「点了没反应」。
  用户刷新页面让出连接后，排队的几次点击一起发出去，还建出了两个同名空间（同一毫秒创建）。
  已在内置浏览器里复现：旧代码 6 个标签页时 `POST` 挂起 8 秒以上；新代码 8 个标签页时只占
  1 条连接，创建照常成功。
- 升级：所有 `fetch` 请求加 20s 超时，排队卡住时至少能看到一条中文错误提示，而不是永远转圈。
- 升级：后台（不可见）标签页主动断开 SSE，让出连接槽位；切回前台后按 `lastSeq` 续传，不丢帧。
- 升级：创建空间时按钮禁用并显示「创建中…」，防止连点重复提交建出同名空间。

## 函数级改动

### `src/simpleagent/web/app.js`

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `req(method, path, data)` | 修改 | 请求加 `AbortSignal.timeout(20000)`；捕获 `TimeoutError` / `AbortError`（Chromium 在读 body 阶段超时报的是后者）统一换成中文提示，说明可能是连接被占满；原来的 204 判断挪进 `try` 块 |
| `subscribe(sessionId, { resume })` | 修改 | 新增 `resume` 选项：`resume` 为真时保留 `state.lastSeq` 并用 `?last_event_id=` 续传（`EventSource` 设不了 `Last-Event-ID` 头，只能走 query）；`document.hidden` 时直接不建连接 |
| `boot()` | 修改 | 新增 `visibilitychange` 监听：标签页切到后台调 `closeStream()` 让出连接槽位，切回前台且当前有 session 又没有活跃连接时 `subscribe(..., { resume: true })` |
| `createSpace()` | 修改 | 请求发出前禁用「创建」按钮并显示「创建中…」，`finally` 里恢复；请求失败仍写 `modal-err`；创建成功、向导已关闭后 `loadSpaces` / `newSession` 若出错，因为 `modal-err` 已经看不见了，改用 `toast` 提示 |

### `src/simpleagent/serve/app.py`

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `Server._sse(session_id, headers)` | 修改 | `last_event_id` 除了读 `Last-Event-ID` 头，也读 query 里的 `last_event_id`（复用已有的 `x-query` 内部约定）；两者都有时以头为准，因为浏览器自动重连带的头更新 |

### 文档

- `docs/design/client-ui.md`：API 表里 `/api/sessions/{id}/events` 补上 `?last_event_id=N`；前端说明补一条「后台标签页不占 SSE」的原因和做法。

## 配置与依赖

- 无变化。
- 需要手动处理：重启 `sa serve` 并刷新（或直接关闭）所有已打开的工作台标签页——旧页面加载
  的是修复前的 JS，仍然会一直占着 SSE 连接，不刷新的话新标签页照样会被顶到连接上限。

## 测试

- 新增 `tests/serve/test_serve.py::test_sse_resume_via_query`：验证 `?last_event_id=N` 能从
  指定 seq 之后重放；同时验证 `Last-Event-ID` 头和 query 都存在时以头为准。
- `uv run pytest -q`：298 passed。
- `uv run ruff check`：通过。
- `uv run ruff format --check`：通过（98 个文件均已符合格式，无需改动）。
- 手动验证：在内置浏览器里开 6 个工作台标签页，旧代码 `POST /api/spaces` 挂起 8 秒以上；开
  8 个标签页时新代码只占 1 条连接，创建正常。

## 相关笔记

无
