# 控制面板改为「近期活动流」，任务完成首次广播状态帧

- 日期：2026-09-17
- 对比基线：`f508913`（Add changelog for client UI mockup name fix）
- 对应里程碑：W2 补强（为 W5 控制面板打底）

## 功能变化

- 修复：任务跑完后在控制面板看不到任何状态变化。定位到三处独立缺口——① `Runner` 只在取消/出错时发帧，**正常结束只写 meta、一帧都不发**；② 事件总线按 session 分发，控制面板跨空间没有订阅通道，只能轮询；③ `/api/panel/summary` 只筛 `status == "running"`，任务一结束就从列表消失，用户看到的是「少了一条」而不是「跑完了」。本次修掉 ① 和 ③，② 的全局事件流留到 W3 / W5。
- 新增：`status` 帧现在覆盖 `running` / `done` / `error` / `cancelled` 四个状态，且带 `space_id` 与 `usage`（`error` 额外带 `reason`）。客户端据此可以显示「刚完成 ✓」这类终态，不再只能看到「最后一条消息说完就没了」。
- 升级：`GET /api/panel/summary` 从「只列运行中」改成**近期活动流**。`running` 保留原有字段并追加 `space_name` / `status` / `updated_at` / `verification`；新增 `recent` 数组，收纳 24 小时内完成的会话（最多 10 条，按 `updated_at` 倒序）。存放 `recent` 的意义在于：完成的任务不会立刻从面板消失，状态变化才看得见。
- 修复：顺带发现 `space.verify.trigger`（`off` / `on_stop` / `on_turn`）**从未被 runner 读取**，只有手动 `POST /verify` 才会执行验证，`on_stop` 目前是死配置。本次不动它，记在这里待后续处理。

## 函数级改动

### `src/simpleagent/serve/runner.py`

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `Runner._run_input()` | 修改 | 开场在 `status="running"` 落盘之后补发一帧 `status: running`（带 `space_id`），面板才知道任务开始了。`except CancelledError` 分支去掉自己那次 `status_frame` 发送，改由 `_finalize` 统一发；`except Exception` 把错误文案提成局部变量，传给 `_finalize(reason=...)` |
| `Runner._finalize()` | 修改 | 从「只落盘」升级为「一轮结束的唯一收口点」：落盘终态 + 广播一帧 `status`（带 `space_id`、`usage`，可选 `reason`）。新增仅关键字参数 `reason: str \| None`。三个终态（done / error / cancelled）现在都从这里发帧 —— 收口到一处是为了覆盖全部分支：agent loop 可能因 `max_steps` 或任意异常提前结束，逐个分支发帧必然漏 |

### `src/simpleagent/serve/app.py`

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `_within_window(ts, cutoff)` | 新增 | 判断 `updated_at` 是否晚于留存窗口。时间戳解析不出来时返回 `False`，避免一条脏 meta 让整个面板接口 500 |
| `_verification_status(meta)` | 新增 | 取验证状态，兼容 `Verification` 对象和手写进去的 dict 两种形态 |
| `RECENT_DONE_LIMIT` / `RECENT_DONE_WINDOW_MINUTES` / `FINAL_STATUSES` | 新增 | 模块常量：留存 10 条、窗口 24 小时、算作终态的 `done` / `error` / `cancelled` |
| `Server._panel_summary()` | 修改 | 从「只筛 running」改成 running + recent；每条补 `space_name`、`status`、`updated_at`、`verification`；`recent` 按 `updated_at` 倒序取前 10 条 |
| 模块导入 | 修改 | 新增 `from datetime import UTC, datetime` |

## 配置与依赖

- 无新增依赖（仍是标准库 `http.server`），无配置项变化，无数据目录变化，用户无需手动处理。
- 接口契约变化（向后兼容）：`/api/panel/summary` 的响应多了 `recent` 数组，`running` 数组里每条多了 4 个字段。**字段是追加，没有重命名或删除**，按旧结构解析的客户端不会挂。
- 事件帧契约扩展（向后兼容）：`status` 帧的 `payload` 增加了 `space_id` / `usage` / 可选 `reason`，原有的 `status` 字段语义不变。

## 测试

- `tests/serve/test_serve.py`（新增 4 个用例）：
  - `test_runner_emits_status_done` —— 正常结束发 `status: done`，排在最后一条 `message_done` 之后，带 `space_id` 与 `usage`，且 meta 落盘为 `done`
  - `test_runner_emits_status_cancelled` —— 重构收口点后取消仍然发帧，防回归
  - `test_panel_summary_lists_recent_done` —— 跑完的会话进 `recent`，带空间名与验证状态
  - `test_panel_summary_drops_stale_recent` —— 超出 24 小时窗口的完成记录掉出 `recent`
- 测试结果：`uv run pytest -q` **181 passed**（`tests/serve/test_serve.py` 由 6 个增至 10 个）；`uv run ruff check` 通过；`uv run ruff format` 无改动。
- 另做了一次真实 HTTP 冒烟（不联网、不调模型）：起 `make_server` 后打 `/api/panel/summary`，确认 `running` / `recent` / `verification` / `space_name` 归类正确，且 `idle` 的空会话两个列表都不出现。

## 相关笔记

无（本次属于 W2 的补强，机制细节以 `docs/design/client-ui.md` 6.1 / 6.2 为准）
