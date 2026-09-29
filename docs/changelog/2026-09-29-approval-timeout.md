# 客户端审批加超时：没人答的审批 5 分钟后按拒绝，不再一直占着会话

- 日期：2026-09-29
- 对比基线：`7b9fe71`（版本号 0.2.11）
- 对应里程碑：M3 缺口⑥（见 [design/permission-mode.md](../design/permission-mode.md) 第 7 节）

## 功能变化

- 修复：`sa serve` 的审批卡以前一直等人答。弹了审批就关掉页面、又没人回来处理的话，这一轮不结束，
  会话占用不放，之后每次发消息都是 `SessionBusy`（在等它的调度会话也跟着卡住），只能重启 serve。
  现在等满 `[permissions].approval_timeout`（默认 300 秒）按拒绝处理，这一轮照常收口。
- 新增：SSE 帧 `approval_timeout`。还开着的审批卡收到它就去掉按钮，改成「已超时 · bash（5 分钟没人确认，按拒绝处理）」。
- 升级：超时回给模型的原因是「等了 5 分钟没人确认，按拒绝处理」，不再是「已被拒绝」，记录里不会像是人按了拒绝。
- 升级：`POST /api/approvals/{id}` 对已经不在的审批（超时了结、别处处理过）返回 404 和中文原因。以前一律 200，
  点过期的卡看着「已处理：允许」、其实没人接。控制面板任务卡上的审批按钮提交失败时也会弹出原因。

## 函数级改动

### `src/simpleagent/serve/approval.py`

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `APIApprover.__init__()` | 修改 | 新增关键字参数 `timeout`（秒，None / 0 = 一直等） |
| `APIApprover.request()` | 修改 | `await future` 改成 `asyncio.wait_for(future, timeout)`；超时清登记、推 `approval_timeout` 帧、返回带 `note` 的拒绝 |
| `PendingApprovals.drop_session()` | 新增 | 把某个会话还挂着的审批全部按拒绝了结 |
| `PendingApprovals.has()` | 新增 | 这个审批 id 还在不在等 |

### `src/simpleagent/serve/runner.py`

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `Runner.approve()` | 修改 | 审批 id 已经不在就返回 False（HTTP 层据此回 404） |
| `Runner._finalize()` | 修改 | 收口时调 `pending.drop_session()` 兜底，不留点了也没人接的卡 |
| `Runner._build_agent()` / `_build_commander()` | 修改 | 构造 `APIApprover` 时传 `config.permissions.approval_timeout`（计划卡也受它约束） |

### `src/simpleagent/serve/frames.py`

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `approval_timeout_frame()` | 新增 | `{approval_id, timeout}` |

### `src/simpleagent/serve/app.py`

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `Server._resolve_approval()` | 修改 | 404 的错误信息改成中文，会直接弹给点按钮的人看 |

### `src/simpleagent/permissions.py`

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `ApprovalDecision` | 修改 | 新增 `note: str = ""`：不是人按的拒绝要说清楚原因 |

### `src/simpleagent/tools/registry.py`

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `ToolRegistry` 执行审批的那段 | 修改 | 拒绝原因用 `answer.note`，没有才用「已被拒绝」 |

### `src/simpleagent/config.py`

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `PermissionsConfig.approval_timeout` | 新增 | `float`，默认 300，`>= 0` |

### `src/simpleagent/web/app.js`

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `FRAME_TYPES` | 修改 | 加 `approval_timeout` |
| `settleApprovalCard()` | 新增 | 审批卡了结收进过程，点按钮和超时共用（从点击处理里抽出来） |
| `expireApprovalCard()` | 新增 | 处理 `approval_timeout` 帧 |
| 控制面板任务卡的审批按钮 | 修改 | 提交失败 toast 原因，先收掉卡片，下一轮轮询还在等会再补回来 |

## 配置与依赖

- `config.toml` 的 `[permissions]` 新增可选项 `approval_timeout`（秒，默认 300，0 = 一直等）。不用手动改。
- 依赖无变化。

## 测试

- `tests/serve/test_serve.py`：新增审批器超时、`drop_session` 只清指定会话、挂着审批不管能超时收口并放开会话三个用例
- 测试结果：890 passed；`ruff check` 通过
- 手动：临时脚本起 `sa serve`（FakeLLM，超时 6 秒），浏览器里弹审批不点 → 卡片变「已超时」、这一轮完成；
  弹审批后离开页面再回来 → 这一轮已经结束、能再发；POST 过期 id 返回 404 和中文原因
