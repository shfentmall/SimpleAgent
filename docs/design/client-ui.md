# 客户端界面设计（W 里程碑：个人 AI 工作台）

> 状态：W1、W2 已实现；W3 客户端骨架待做。W1/W2 只改 `src/`，不动核心 loop 的默认行为。
> 前置：M3（会话 JSONL 持久化 + 权限），M4（daemon / 定时任务）只影响“控制面板”的填充内容，不阻塞骨架。

## 1. 目标与非目标

**目标**

1. 一个两栏桌面客户端：左栏功能区（导航 + 空间列表），右栏工作区（当前会话）。
2. 引入**空间（Space）**概念：一类任务的固定容器，绑定工作目录和执行它的 agent。
3. 空间里只显示最近 5 个运行过的 session，其余折叠隐藏，避免左栏越长越乱。
4. 两类任务定义：通用任务（无专有目录，用 tmp）、绑定外部 agent 的执行空间（进目录、认得 claude code / opencode），并显示**验证状态**，方便在不同任务间切换。

**非目标（这一版不做）**

- 不做多窗口 / 分屏对比。
- 不做云端同步、多人协作。
- 不做插件市场。
- 客户端不承载业务逻辑：所有状态在 Python 侧，客户端只渲染事件 + 发命令（沿用 [ARCHITECTURE.md](../ARCHITECTURE.md#m2-起就要守住的接口约定)）。

## 2. 术语与概念模型

```
Space（空间）──1:N── Session（会话/一次运行实例）
   │                    │
   │ 绑定 cwd + agent    │ 消息流、用量、验证状态
   │                    │
   └── 控制面板 / 知识库是全局入口，不属于任何空间
```

| 术语 | 含义 | 对应现在的类/文件 |
|---|---|---|
| **Space** | 一类任务的容器：名字 + 目录 + 用哪个 agent 跑 + 验证方式 | 新增 `spaces/space.py` |
| **Session** | 一次具体的运行，等价于现在的 `Session`（消息历史 + 用量） | `agent/session.py` |
| **Task** | 用户口中的“任务”，在本设计里**不单独建模**，就是 Session。一个空间=一类任务的集合=该空间下所有 session | — |
| **Agent 绑定** | 空间选谁来执行：`simpleagent`（内置 loop）/ `claude-code` / `opencode` | 新增 `spaces/agent_binding.py` |
| **验证状态** | 这个 session 跑完后，验证命令的结果：未验证 / 验证中 / 已验证 / 失败 / 已失效 | 新增 `spaces/verify.py` |

**一句话**：空间决定“在哪儿、用什么跑”，session 决定“这一轮具体跑什么、跑得对不对”。

## 3. 界面结构

```
┌──────────────────────┬────────────────────────────────────────────────┐
│ 左栏 功能区 (300px)   │ 右栏 工作区 (flex)                              │
│                      │                                                │
│ ── 固定入口 ──        │ ┌ 面包屑：空间名 / session 标题                 │
│ [+ 新建 Agent]       │ │   [CC]  ~/dev/SimpleAgent                     │
│   控制面板            │ │   已验证 ✓   3.2k tokens   [停止][重跑][验证] │
│   知识库              │ ├──────────────────────────────────────────────┤
│                      │ │ 对话 | 变更(2) | 文件 | 日志                  │
│ ── 打开的空间 (3) ──  │ ├──────────────────────────────────────────────┤
│ ▾ 临时整理      [通用]│ │                                              │
│   ~/…/spaces/a1/tmp  │ │  消息流：                                     │
│   ● 整理下载目录  运行中│ │   👤 把 tests/ 下重复 fixture 提出来         │
│   ✓ 统计 py 行数  2h  │ │   🤖 我先看一下目录结构…                      │
│   ✗ 批量重命名   昨天 │ │   ⚙ list_dir(depth=2)          12 行  [展开]  │
│   ○ 试算 token   周一 │ │   ⚙ edit_file(...)             diff [展开]   │
│   查看全部 (12)      │ │   ✅ 验证通过 uv run pytest -q · 1.8s         │
│                      │ │                                              │
│ ▾ SimpleAgent   [CC] │ ├──────────────────────────────────────────────┤
│   /Users/…/SimpleAgent│ │ [ deepseek ▾ ]  输入…  @引用  /命令   [发送] │
│   ● 修 pytest 临时目录│ └──────────────────────────────────────────────┘
│   ○ 写 changelog 3h  │
│ ▾ 实验区        [OC] │
│   /tmp/opencode-lab  │
└──────────────────────┴────────────────────────────────────────────────┘
```

### 3.1 左栏上半：三个固定入口

| 入口 | 作用 | 状态 |
|---|---|---|
| **新建 Agent**（主按钮） | 打开新建空间向导：① 名字 ② 类型（通用 / 绑定 agent）③ 目录（通用=自动分配 tmp，绑定=选目录）④ agent（claude code / opencode / simpleagent）⑤ profile ⑥ 可选验证命令 | 本次设计 |
| **控制面板** | 全局视图：正在跑的 session（跨空间聚合）、定时任务（`schedules.toml`，M4）、模型 profile 与用量统计、最近错误 / trace 入口、设置 | 骨架本次设计，内容随 M4/M6 填 |
| **知识库** | 记忆（`memory/` + `MEMORY.md`，M7）、skills 列表、导入的文档与索引 | M7 再实现，本次只留入口和空态 |

### 3.2 左栏下半：打开的空间

- 只列**已打开**的空间（`space.toml` 里 `opened=true`），关闭（×）只是从列表移除，不删数据。
- 每个空间卡片显示：
  - 名称 + 类型徽标：`通用` / `CC`(claude code) / `OC`(opencode) / `SA`(内置)
  - 工作目录（末尾截断，hover 显示全路径，点击在 Finder 打开）
  - 验证汇总：`✓ 3 / 5`（最近 5 个里已验证的数量）
- 卡片内列出**最近 5 个运行 session**（规则见 3.3），每条：状态点 + 标题 + 相对时间 + 验证标记。
- 超出的折叠为「查看全部 (N)」，点开展开完整列表（带搜索和按状态筛选）。
- 排序：运行中置顶，其余按 `updated_at` 倒序；手动置顶（pin）的常驻，不占 5 个名额。
- 顶部一个搜索框，跨空间搜 session 标题和消息内容（v1 只搜标题，`grep` 全文留到后面）。

### 3.3 最近 5 个 session 的保留规则

| 规则 | 说明 |
|---|---|
| 默认展示 | 每个空间最多 5 条，按 `updated_at` 倒序 |
| 运行中 | 永远置顶且不被挤出；跑完仍在列表里 |
| 置顶 | 用户 pin 的 session 常驻，不占用 5 个名额 |
| 隐藏 | 第 6 条起收进「查看全部」，**不是删除** |
| 清理 | 空间可配 `keep_sessions`（默认 50），超出只归档不删；`sa spaces gc` 手动清理 N 天前的 |

### 3.4 右栏：工作区

- **头部**：面包屑（空间 / session）+ agent 徽标 + 工作目录 + 验证状态 + token 用量 + 操作（新建 session、停止、重跑、跑验证、导出 markdown）。
- **Tab**：对话（默认）/ 变更（文件 diff 列表，来自 `edit_file` / `write_file` 事件）/ 文件（当前 cwd 树）/ 日志（trace、请求用量、错误）。
- **消息流**：用户消息、助手文本（流式）、思考内容（可折叠）、工具调用卡（默认折叠，显示工具名+关键参数+结果行数，展开看完整输出）、审批卡（允许 / 拒绝 / 本次会话始终允许）、错误卡。
- **输入区**：多行输入、模型 profile 切换、`@` 引用文件、`/` 命令（`/compact`、`/model`、`/verify` 等）、发送 / 停止。

## 4. 空间的两类任务定义

### 4.1 通用任务空间（`kind = "generic"`）

- **没有专有目录**：工作目录自动分配为 `~/.simpleagent/spaces/<space-id>/tmp/`，空间创建时建好。
- 适用：临时整理、一次性计算、随手一问。产物默认留在 tmp 里，随时可清空（`清空工作目录` 按钮）。
- 执行者固定是内置 `simpleagent` loop，不外调 CLI。
- 可选 `pin_dir`：如果用户想把产物放别处（比如下载目录），单独指定，仍然算通用空间。

### 4.2 绑定外部 agent 的空间（`kind = "agent"`）

- 绑定**一个真实项目目录**（`cwd`），进入该目录执行。
- 绑定**一个 agent**：`claude-code` / `opencode` / `simpleagent`。v1 用无头 CLI（ARCHITECTURE 已定：`claude -p --output-format stream-json`、`opencode run --format json`），保存 agent 返回的 session id，下次可以接着追问；ACP 协议以后再说。
- 右栏头部明确显示“当前是谁在跑”，切换 agent 时把 session id 一起换掉（不同 agent 的 session 不互通）。
- 同一个目录可以开多个空间（例如一个跑 claude code、一个跑 opencode 做对比），互不干扰。

### 4.3 验证状态（verification）

**来源**，两条，优先级从高到低：

1. **自动**：空间配了 `[verify].command`（如 `uv run pytest -q && uv run ruff check`），在 cwd 里跑，用**退出码**判定。触发时机 `on_stop`（session 结束时，默认）/ `on_turn`（每轮结束）/ `off`。
2. **手动**：用户点「标记为已验证」；或外部 agent 跑完测试后由 hook 上报（M8）。

**取值**

| 值 | 含义 | 显示 |
|---|---|---|
| `unknown` | 没跑过验证 | 灰色 ○ 未验证 |
| `running` | 正在跑 | 蓝色 ◐ 验证中 |
| `passed` | 退出码 0 | 绿色 ✓ 已验证 |
| `failed` | 非 0 | 红色 ✗ 未通过 |
| `stale` | 验证通过后又有新的文件写入 | 黄色 ⚠ 已失效 |

`stale` 是关键：它让“验证”不会骗人。判定方式——记录验证时的文件指纹（cwd 下 tracked 文件的 mtime+size 的哈希），之后有写工具改动文件就置为 `stale`。

**字段**

```python
@dataclass
class Verification:
    status: str  # unknown|running|passed|failed|stale
    command: str | None
    exit_code: int | None
    started_at: str | None
    finished_at: str | None
    output_ref: str | None  # 完整输出落盘路径（复用 tool_outputs 那套）
    fingerprint: str | None  # 验证通过时的目录指纹，用于判 stale
    source: str  # auto|manual
```

左栏 session 行右侧一个小标记、右栏头部一个大 chip、右栏「日志」tab 里有完整输出，三处同源。

## 5. 数据模型与落盘

### 5.1 目录

```
~/.simpleagent/
  config.toml
  spaces/
    <space-id>/
      space.toml              # 空间定义（可变，唯一真值）
      tmp/                    # 通用空间的工作目录（kind=generic）
      sessions/
        <session-id>.jsonl    # 消息流，只追加
        <session-id>.meta.json# 可变元信息：标题/状态/验证/时间戳/pin
  tool_outputs/
  traces/
```

**为什么 meta 拆成单独文件**：jsonl 只追加，但标题、状态、验证结果会变。改 jsonl 中间行要重写整个文件，长期跑下来代价太大，所以可变字段走 sidecar，消息流保持纯追加。

### 5.2 `space.toml`

```toml
id = "sp_a1b2c3"
name = "SimpleAgent 重构"
kind = "agent"                 # generic | agent
profile = "deepseek"           # 用哪个 model profile
opened = true                  # 是否在左栏显示
pinned = false
created_at = "2026-09-17T14:00:00+08:00"
last_opened_at = "2026-09-17T14:52:00+08:00"
keep_sessions = 50

[agent]
name = "claude-code"           # claude-code | opencode | simpleagent
command = "claude"
args = ["-p", "--output-format", "stream-json"]
cwd = "~/dev_code/SimpleAgent"
resume_flag = "--resume"       # 用于追问，配合 meta 里的 agent_session_id

# kind = "generic" 时改为：
# [generic]
# tmp_dir = "auto"             # auto → spaces/<id>/tmp
# pin_dir = ""                 # 可选，想固定产物位置时填

[verify]
command = "uv run pytest -q && uv run ruff check"
trigger = "on_stop"            # off | on_stop | on_turn
timeout = 300
```

### 5.3 `<session-id>.meta.json`

```json
{
  "id": "se_9f2a",
  "space_id": "sp_a1b2c3",
  "title": "修 pytest 临时目录权限问题",
  "status": "running",        // running|idle|done|error|cancelled
  "pinned": false,
  "agent": "claude-code",
  "agent_session_id": "0c1f...",  // 外部 agent 的会话 id，用于追问
  "created_at": "2026-09-17T14:10:00+08:00",
  "updated_at": "2026-09-17T14:48:00+08:00",
  "usage": {"prompt_tokens": 12034, "completion_tokens": 2201, "cached_tokens": 8192},
  "verification": {
    "status": "passed", "command": "uv run pytest -q",
    "exit_code": 0, "finished_at": "2026-09-17T14:47:10+08:00",
    "fingerprint": "sha1:...", "source": "auto"
  }
}
```

标题来源（可切，默认 b）：a) 首条用户消息截断 40 字；b) 第一轮结束后用一次便宜的 LLM 调用生成 12 字以内摘要（异步写回，失败就退回 a）。

### 5.4 Python 侧接口

```python
# spaces/store.py
class SpaceStore:
    def list_spaces(self, opened_only: bool = True) -> list[Space]: ...
    def create_space(self, spec: SpaceSpec) -> Space: ...
    def get_space(self, space_id: str) -> Space: ...
    def update_space(self, space_id: str, **fields) -> Space: ...
    def close_space(self, space_id: str) -> None:          # opened=false，不删数据
    def delete_space(self, space_id: str) -> None:         # 显式删除，需二次确认

    def list_sessions(self, space_id: str, limit: int = 5,
                      include_pinned: bool = True) -> list[SessionMeta]: ...
    def create_session(self, space_id: str, agent: str | None = None) -> Session: ...
    def load_session(self, session_id: str) -> Session: ...   # 读 jsonl 重建消息
    def append_message(self, session_id: str, msg: dict) -> None: ...
    def update_meta(self, session_id: str, **fields) -> None: ...
```

`Session` 仍是 `agent/session.py` 那个 dataclass，只是多了持久化和 meta，核心 loop 不用改。

## 6. 与核心的接口（本地 API）

客户端通过本地 HTTP + SSE 跟后台引擎通信。**不用 WebSocket**：客户端→服务端都是普通请求（发消息、取消、审批），服务端→客户端是单向事件流，SSE 足够，还省一个依赖、断线重连可以直接用 `Last-Event-ID`。

### 6.1 事件帧格式

现有 `Event` 都是 dataclass，加一个统一的 `to_frame()`：

```json
{"seq": 128, "session_id": "se_9f2a", "type": "text_delta", "ts": "...", "payload": {"text": "..."}}
```

`type` 取值：`text_delta` / `reasoning_delta` / `message_done` / `tool_call_start` / `tool_result` / `approval_request` / `verification` / `status` / `usage` / `error` / `max_steps` / `turn_end`。
`seq` 单 session 内单调递增，客户端按 seq 去重、断线后按 `Last-Event-ID` 续传。

### 6.2 路由

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/spaces` | 空间列表（含每个空间最近 5 个 session） |
| POST | `/api/spaces` | 新建空间（向导提交） |
| PATCH/DELETE | `/api/spaces/{id}` | 改配置 / 关闭或删除 |
| GET | `/api/spaces/{id}/sessions?limit=5` | session 列表 |
| POST | `/api/spaces/{id}/sessions` | 新建 session |
| GET | `/api/sessions/{id}` | 消息历史 |
| POST | `/api/sessions/{id}/input` | 发一条用户输入，触发 run（202） |
| POST | `/api/sessions/{id}/cancel` | 对应 `agent.cancel()` |
| POST | `/api/sessions/{id}/verify` | 手动跑验证 |
| PATCH | `/api/sessions/{id}/verification` | 手动标记已验证 |
| GET | `/api/sessions/{id}/events` | SSE 事件流，支持 `Last-Event-ID` |
| GET | `/api/approvals?status=pending` | 待审批列表 |
| POST | `/api/approvals/{id}` | `{action: allow|deny|always}` |
| GET | `/api/panel/summary` | 控制面板：运行中任务、定时任务、用量 |
| GET | `/api/knowledge/...` | 知识库（M7） |

审批走异步 `approve()` 接口：后台需要确认时推 `approval_request` 事件并挂起，客户端回 POST 后继续；客户端不在线则按无人值守策略拒绝（M3 已定的行为）。

### 6.3 技术选型（待确认）

| 层 | 方案 | 说明 |
|---|---|---|
| 后台 API | A. 标准库 `http.server` + 手写 SSE | 零新依赖，符合“依赖尽量少”原则，学习价值高；路由要自己写几十行 |
| | B. FastAPI + uvicorn（推荐备选） | 两个依赖，SSE、校验、OpenAPI 文档白送；以后换过去接口不变 |
| 客户端壳 | A. 浏览器打开 `localhost:8384`（**建议先做这个**） | 零新语言，交互调得快，能先把布局和事件渲染验证透 |
| | B. Tauri v2 + React/TS | 体积小、用系统 WebView，适合长期；等交互稳定后再包 |
| | C. Electron | 成熟但重（~150MB），本项目没必要 |
| | D. PyWebView | 同进程直接用 Python，省 IPC；打包和前端生态弱 |

建议路径：**先 A+A**（标准库后台 + 浏览器前端），把 UI 结构和事件协议定下来；等布局不再大改，再决定要不要包 Tauri。核心代码不受影响。

## 7. 分期实现计划

| 期 | 内容 | 验证方式 |
|---|---|---|
| **W1** | 持久化层：`spaces/` 目录结构、`space.toml`、`meta.json`、`SpaceStore`、session 追加/恢复、最近 5 条规则、置顶 | ✅ 已实现 + 单测（13 个），详见 `src/simpleagent/spaces/` 与 `tests/spaces/test_store.py` |
| **W2** | 本地 API：`sa serve` + 路由 + SSE 事件帧 + 取消 + 审批回转 | ✅ 已实现 + 单测（6 个）。`src/simpleagent/serve/`：bus（seq+重放）/ frames / approval（审批桥）/ runner（后台 asyncio 线程）/ app（http.server 路由+SSE）。用 FakeLLM 起服务，`curl -N` 看 SSE 帧序；断言审批挂起→POST→继续 均通过 |
| **W3** | 客户端骨架：两栏布局、左栏三个入口 + 空间列表（最近 5 + 查看全部）、右栏头部/对话/输入，能发消息并流式渲染 | 手动：新建空间→发一句话→看到流式输出；刷新页面后消息还在 |
| **W4** | 工具调用卡、审批卡、停止/重跑、验证状态（verify 命令执行、stale 判定、三处同源显示） | 手动：跑一个会改文件的任务→验证通过→再改一个文件→徽标变“已失效” |
| **W5** | 控制面板（运行中任务、定时任务 M4、用量、trace 入口）、知识库（M7）、设置 | 手动：控制面板能看到跨空间正在跑的 session 并能跳转 |

W1 不依赖任何 UI，可以现在就做；W2 之后每一步都能单独跑起来看效果。

## 8. 待确认

1. **客户端技术栈**：先浏览器 + 后续 Tauri，还是直接上 Tauri/Electron？（我建议前者）
2. **“验证”的口径**：只要命令退出码，还是要解析测试输出（如 `12 passed`）？需不需要人工确认也算？
3. **通用空间的 tmp**：每个空间一个 tmp 目录（好清理、但产物分散），还是共用一个全局 tmp？
4. **空间与目录的关系**：允许同一目录开多个空间（对比不同 agent）吗？我默认允许。
5. **“打开的空间”是否持久化**：重启客户端后恢复上次打开的空间，我默认是（写 `opened` 字段）。
6. **session 标题**：首条消息截断，还是额外调一次模型生成摘要（多花一次调用）？
7. **并行**：要不要支持多个空间的 session 同时跑？（核心是全异步，能做；UI 上要考虑运行中任务的全局提示）
8. **左栏下半除了空间，要不要放“最近文件/最近改动”**：先不放，避免左栏过载；需要的话放控制面板。

## 9. W1 / W2 实现备注

### 9.1 核心层的极薄注入点（默认行为不变）
W2 给核心 loop 加了三个注入点，**不传则完全保持旧行为**（REPL 不感知）：
- `ToolContext` 增加 `session_id` 与 `approver` 两个字段（默认 `None`）。
- `ToolRegistry` 增加 `approver` 参数；执行**写操作**（`readonly=False`）前先 `await approver.request(...)`，被拒绝则返回错误结果、不真正执行。只读工具直接放行。
- `Agent` 增加 `approver` 参数，把 `session_id`/`approver` 透传进 `ToolContext`；并新增 `cancel()`（取消底层 asyncio 任务，loop 会先修好历史再抛 `CancelledError`）。

这三个点是 ARCHITECTURE 里早就规划的「审批器是异步接口」，M3 也会复用，不是为 W2 临时加的。

### 9.2 后台线程模型
- `Runner` 自己起一个线程跑独立的 asyncio 事件循环；HTTP 层（`http.server` + `ThreadingHTTPServer`）在另一线程。
- 两边通过**线程安全的事件总线**（`queue.Queue` + `Lock`，不依赖 asyncio）解耦：runner 发布帧，SSE handler 阻塞取帧。
- 审批的「挂起 / 恢复」用 asyncio `Future`：`APIApprover.request()` 推一帧 `approval_request` 后 `await future`；HTTP 层 `POST /api/approvals/{id}` 通过 `loop.call_soon_threadsafe(future.set_result, ...)` 在 runner 线程上唤醒它。
- 验证命令用 `subprocess.run` + `asyncio.to_thread`，避免 asyncio 子进程 watcher 在非主线程 loop 上不好使。

### 9.3 与原始草案的差异
- 草案里验证命令只挂在 agent 类空间。实现中 `Space.from_spec` 给**两类空间都支持** `verify`（generic 也能跑 `pytest` 之类），修复了原 `from_spec` 在 generic 分支提前 return 漏掉 verify 的 bug。
- 帧类型在草案 6.1 的基础上补了 `status` / `error` / `verification` 三种服务端补充帧（`approval_request` 沿用草案命名）。
- `Space` 新增 `to_dict()`、`SpaceStore` 新增 `find_session_space()`（按 session id 跨空间定位），供 API 直接按 session id 访问。
