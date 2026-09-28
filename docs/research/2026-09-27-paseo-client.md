# Paseo 客户端调研：SimpleAgent 工作台能参考什么（2026-09）

> 调研目的：看清 Paseo 的客户端（桌面、网页、手机）和它背后的后台是怎么搭的，判断 SimpleAgent 的工作台客户端适不适合参考它，哪些能借、哪些不该学。
>
> 调研时间：2026-09-27。
>
> 信息来源：
> - **Paseo**：GitHub `getpaseo/paseo` 主干（commit `30178c4`，2026-09-27，版本 0.10.0-beta.1，Apache-2.0）。以仓库里的 `docs/`（架构、时间线同步、流式性能等）和源码为准。本机装的 Paseo.app 0.9.2 只用来看安装包结构和体积。文中 Paseo 的路径都相对这个仓库的根目录。
> - **SimpleAgent**：当前 `main`（`9ab5d64`），对照过 `src/simpleagent/serve/`、`src/simpleagent/web/`、`src/simpleagent/agents/` 的代码。

---

## 0. 核心结论

**结论一：整体不照搬。** 两边定位不同、技术栈不同，代码量差两个数量级：

- Paseo 是「远程操控多个编码 agent」的产品，自己没有 agent loop，重点是跨设备（手机、网页、桌面、CLI）和跨 provider。SimpleAgent 自己写 loop，工作台的重点是空间、指挥台调度、消息、验证状态。
- Paseo 客户端用 Expo（React Native）一套代码跑 iOS、Android 和网页，Electron 只是外壳。选它是因为要上手机；SimpleAgent 只做桌面，引入它等于多学一整套移动端技术，和「学 agent 机制」这个目标无关。
- Paseo 的复杂度大多来自 SimpleAgent 没有的需求：旧版手机 app 要能连新版后台（协议只增不减、按能力开关功能）、手机切到后台会断线、一台手机连多台机器、经中继远程访问（端到端加密）。

**结论二：它印证了 SimpleAgent 现在的路线。** Paseo 的界面本质是一个网页：后台可以直接托管它（`features.webUi`），Electron 用自定义协议加载同一份静态文件，preload 只补十来个原生能力。这和 [client-ui.md 6.3](../design/client-ui.md#63-技术选型待确认) 里「先做浏览器前端，稳定后再包壳」是同一个思路。

**结论三：值得借鉴的五件事**，按对 SimpleAgent 的价值排：

1. 外部 agent 改用 SDK / 服务模式接入，拿到实时审批（§6）→ M8
2. 按客户端在线状态路由通知的 80 行纯函数（§7）→ M4 第 ③ 步
3. 薄壳 + 桥接对象 + 「只停自己拉起的后台进程」（§3）→ 包桌面壳时
4. 事件带「代际」编号，后台重启后客户端能认出来（§4）→ 小修
5. 流式文字只重解析最后一个 Markdown 块（§5）→ 按需

**结论四：对照时发现 SimpleAgent 两处现有缺口。**

- `sa serve` 不校验 Host 头，也不看 Content-Type（§8），能被 DNS rebinding 和跨站 POST 利用。已在 2026-09-27 单独开任务修复。
- 事件的 `seq` 只存在内存里，`sa serve` 重启后从 1 重新数，开着的页面会把新帧当成重放丢掉（§4.3，按代码推断，未在浏览器里实测）。

---

## 1. 总表

| 维度 | Paseo | SimpleAgent |
|---|---|---|
| 定位 | 多个编码 agent 的遥控器（Claude Code / Codex / Copilot / OpenCode / Pi 等），自己不带 loop | 自己写 loop 的个人 agent + 工作台 |
| 后台 | Node.js daemon（`packages/server`）：管 agent 进程、WebSocket API、MCP、定时任务 | Python `sa serve`（`http.server` + 后台 asyncio 线程） |
| 客户端 | Expo（React Native）一套代码跑 iOS / Android / 网页；另有 CLI | 零依赖原生 JS（`src/simpleagent/web/`），由 `sa serve` 托管 |
| 桌面 | Electron 外壳：加载 Expo 导出的静态网页，托管或附着本机 daemon；本机安装后约 481 MB | 还没有，计划浏览器版稳定后再包 |
| 通信 | 单条 WebSocket：JSON 文本帧 + 终端二进制帧；请求 / 响应按 requestId 配对，订阅按 subscription id 路由 | HTTP 请求 + 每个会话一条 SSE |
| 事件编号 | 每个 agent 的时间线有 epoch + seq；目录数据有 daemon generation | 每个会话一个内存里的 seq，重放缓冲 2000 帧 |
| 断线恢复 | 实时流只管及时，权威历史接口管正确；按页补齐，直到没有更新的为止 | `Last-Event-ID` 重放内存缓冲 + `GET /api/sessions/{id}` 拉历史 |
| 历史的权威来源 | provider 自己的会话记录（如 `~/.claude/projects/...jsonl`），daemon 内存里只放投影 | 自己落盘两份：界面事件镜像 + 模型看到的历史（M6） |
| 流式渲染 | 后台 60ms 合并 → 前端按帧提交 → 按积压量匀速显示；Markdown 按块拆分 | 每个 `text_delta` 都重渲染整段 Markdown |
| 外部 agent | Claude Agent SDK / Codex app-server / opencode server / ACP，有实时审批 | `claude -p` / `opencode run` 无头模式，只有只读 / 全放行两档 |
| 工具调用展示 | 归一成 `ToolCallDetail`（shell / read / edit / write / search / fetch / sub_agent 等） | 工具名 + 参数 + 结果文本 |
| 通知 | 客户端上报在线状态，后台决定应用内提醒还是系统推送 | 消息（inbox）+ 左栏轮询角标；系统通知排在 M4 第 ③ 步 |
| 本地服务安全 | 每个请求校验 Host 白名单；HTML 预览沙箱；远程访问另有密码和配对 | 只绑 127.0.0.1，没有其他校验（修复中） |
| 扩展 | TS 插件：provider、侧栏、面板、命令、时间线渲染 | Python：技能、MCP、直接改代码 |
| 规模（非测试代码） | 客户端约 30 万行 TS、95 个运行依赖；后台约 18 万行 | 前端 3.3k 行，`serve/` 2.2k 行 |

---

## 2. Paseo 是怎么搭的

### 2.1 分层

```
packages/protocol   线上消息的 zod schema、二进制帧编解码、共享类型（不依赖 server）
packages/client     daemon 的 WebSocket 驱动 + PaseoClient 门面（也是对外 SDK）
packages/server     daemon：agent 生命周期、WebSocket 服务、MCP、定时任务、中继出站
packages/app        Expo 客户端（手机 + 网页，桌面也用它）
packages/desktop    Electron 外壳
packages/cli        Docker 风格的 CLI（paseo run / ls / logs / wait），和 app 讲同一套协议
packages/relay      端到端加密中继（Curve25519 + NaCl box）
```

手机、网页、桌面、CLI 四种客户端都只是同一套 WebSocket 协议的使用方。daemon 可以脱离桌面 app 单独运行，一个 daemon 可以同时连多个客户端（`docs/product.md`）。这和 SimpleAgent ARCHITECTURE 里「REPL / `sa run` / daemon / 客户端都只是事件流的消费方」是同一个原则。

### 2.2 一次运行的数据流（`docs/architecture.md`）

1. 客户端发 `CreateAgentRequestMessage`（prompt、cwd、provider、model、mode）
2. `AgentManager.create()` 建一个 `ManagedAgent`，初始化 provider 会话
3. provider 产出 `AgentStreamEvent`，追加进 agent 的时间线，广播给所有订阅的客户端
4. 工具调用归一成 `ToolCallDetail`
5. 权限请求：agent → daemon → 客户端 → 用户决定 → daemon → agent

agent 的状态：`initializing → idle ⇄ running`，出错进 `error`，终态是 `closed`（`shared/agent-lifecycle.ts`）。

### 2.3 客户端为什么这么大

`packages/app` 光非测试代码就有约 30 万行，大头来自这些需求：

- **四个平台一套代码**：iOS、Android、浏览器、Electron，靠 `@/constants/platform` 里的四个门控区分。另外要处理手机键盘、手势面板、底部抽屉、Android 弹层这类移动端问题（`docs/mobile-panels.md`、`docs/floating-panels.md`）。
- **多 host**：`HostRuntimeController` 管多台机器的连接、重连和各自的状态。
- **离线副本**：`runtime/replica-cache` 把目录和时间线存在本地（浏览器和 Electron 用 IndexedDB，手机用 expo-sqlite）。打开时先画缓存，再和网络对账。
- **协议兼容**：app 和 daemon 的版本会错开（商店审核、用户不升级），所以协议只增不减，新的枚举值要按客户端能力开关（`docs/protocol-compatibility.md`）。
- **终端、编辑器、git diff、内置浏览器、语音**：xterm、CodeMirror、git forge 集成、语音听写和实时语音 agent。

这些需求对 SimpleAgent 基本都不成立：只做桌面、单机、前后端同仓同版本发布。

---

## 3. 桌面壳：Electron 只是外壳

### 3.1 怎么加载界面

`packages/desktop/src/main.ts`：

- 注册一个自定义协议（`protocol.registerSchemesAsPrivileged`，标记为标准、安全、支持 fetch），再用 `protocol.handle` 把 `app://app/...` 映射到打包进去的 `app-dist/`，也就是 Expo 导出的静态网页。
- 没有扩展名的路径回退到 `index.html`（单页应用路由）；`path.relative` 算出来越界就返回 404，防目录穿越。
- 窗口 `loadURL("app://app/")`，界面代码和浏览器版完全一样。

daemon 自己也能托管同一份网页（`public-docs/web-ui.md`，`features.webUi.enabled`），同源连回 API 和 WebSocket，所以浏览器直接打开 `http://localhost:6767/` 就能用。

### 3.2 preload 只补原生能力

`packages/desktop/src/preload.ts` 通过 `contextBridge` 暴露一个 `window.paseoDesktop`，里面是：窗口控制（最小化、全屏、角标数）、原生对话框、系统通知、用系统浏览器打开链接、用编辑器打开文件到指定行、从拖进来的文件取本地路径、右键菜单，以及内置浏览器相关的接口。业务逻辑一概不在壳里。界面代码看 `window.paseoDesktop` 在不在，决定走原生能力还是网页兜底。

两个细节：

- preload 跑在沙箱里，只能 `require("electron")`。引了别的模块，整个 preload 会中断，`window.paseoDesktop` 变成 undefined。0.1.108 出过这个事故，现在有测试守着。
- macOS 要先弹过一次通知，app 才会出现在系统设置的通知列表里。所以启动时发一条静默通知再立刻关掉（`features/notifications.ts` 的 `ensureNotificationCenterRegistration`）。

### 3.3 后台进程托管

`packages/desktop/src/daemon/daemon-manager.ts` 的规则：

- 桌面版可以把 daemon 拉起来当子进程（用 Electron 自带的 Node：`ELECTRON_RUN_AS_NODE=1`），也可以附着到已经在跑的 daemon。
- **只停自己这次拉起的**：用 `{pid, startedAt}` 认进程，只有本次会话 spawn 出来、两项都对得上的，才允许自动停止或替换。启动前就在跑的一律只附着。用户手动点停止时要弹确认，写明是哪个 home、哪个 pid。
- daemon 把自己实际绑定的地址写进 `paseo.pid`，客户端只信这份活记录，不从配置里猜端口。

### 3.4 对 SimpleAgent 的意义

- `web/` 以后基本不用改。包壳时写一个桥接对象（通知、对话框、角标、打开文件），界面按它在不在走不同分支。
- 壳要托管的是 Python 进程（`sa serve`），Paseo 那种「借 Electron 自带的 Node 直接跑 daemon」没法照搬：要么把 Python 一起打包（PyInstaller 或 uv 管理的环境），要么要求本机已经装了 `sa`。
- 「只停自己拉起的」这条要守住：用户可能同时开着终端里的 `sa serve`、定时 daemon 和桌面 app。SimpleAgent 已经有开发模式和日常模式两套数据目录，壳也要按数据目录和端口来认 daemon。
- Paseo 选 Electron 的两个主要理由对 SimpleAgent 不成立：它的 daemon 是 Node（Electron 白送运行时），它要内置浏览器（`<webview>` 和浏览器自动化）。壳用什么技术仍按 [AGENTS.md](../../AGENTS.md) 留给用户定，这里只记下这一点。

---

## 4. 协议与事件同步

### 4.1 Paseo 的 WebSocket 协议

- **握手**：客户端先发 `hello`（clientId、clientType、protocolVersion、capabilities），daemon 回一条 `status.server_info`（版本、`features.*`）。之后的功能按 `features` 开关，不写兜底分支。
- **存活**：客户端每 10 秒发一次应用层的 `ping`，daemon 靠它给这条连接续租，租约过期就断开。RPC 超时只算这次操作失败，不当作连接已经断了。
- **背压**：每条物理连接的出站缓冲上限 8 MiB，超了直接断这条连接，不影响同一会话的其他连接。
- **命名**：新 RPC 用带点的命名空间加方向后缀，如 `domain.operation.request` / `.response`（`docs/rpc-namespacing.md`）。

### 4.2 时间线同步（`docs/timeline-sync.md`）

核心规则一句话：**实时流只管及时，权威历史接口管正确。**

- 实时流是 `agent_stream` 消息，可能是增量；权威历史是 `fetch_agent_timeline_request`，永远返回完整的投影条目。
- 每次运行开一个新的 epoch，条目按 seq 只追加。客户端发现 seq 断档，就按 `after` 方向拉；响应里 `hasNewer: true` 就接着拉下一页，直到没有更新的才算补齐。分页是为了不超过中继的帧大小，但必须拉完整。
- 打开或恢复一个 agent 只拉最新的一页，更早的历史等用户往上滚再拉。
- 客户端心跳（在线状态）只用来路由通知，**不能**用来决定要不要投递事件（§7）。
- 工具输出进流之前先截到 64 KiB（`agent-timeline-content.ts` 的 `TOOL_CALL_CONTENT_MAX_LENGTH`），实时流和历史用的是同一个截断后的条目。
- 用户发出的消息带 `clientMessageId`，daemon 记录的正式用户消息沿用这个 id。客户端靠它把乐观显示的那条和正式的那条对上，不按内容匹配。

这套机制是为多设备、手机后台、中继分页、离线缓存设计的，整套搬过来不划算。

### 4.3 SimpleAgent 现状和一个缺口

现状（`serve/bus.py`、`web/app.js`）：

- 每个会话一个 seq，`EventBus` 在内存里给每个会话留最近 2000 帧（`HISTORY_LIMIT`），用于重放。
- 打开会话时，`GET /api/sessions/{id}` 返回消息历史和当前 seq（`app.py:426`），客户端再用 `?last_event_id=<seq>` 订阅 SSE，并丢弃 `seq <= lastSeq` 的帧（`app.js:597`）。

这已经是「拉历史 + 实时流去重」的结构，大方向和 Paseo 一致。缺的是 Paseo 的 epoch / generation 那一层：

**seq 只存在内存里，`sa serve` 重启后从 1 重新数。** 按代码推断（未在浏览器里实测），会出现这样的情况：

1. 页面开着的时候重启了 `sa serve`。
2. `EventSource` 自动重连，带上旧的 `Last-Event-ID`（比如 500）。服务端缓冲是空的，什么都不重放。
3. 之后在同一个会话里发消息，新帧的 seq 是 1、2、3……，全被 `seq <= lastSeq` 当成重放丢掉。
4. 直到 seq 超过 500、或者用户切换会话 / 刷新页面，才恢复正常。

照 Paseo 的思路修：后台每次启动生成一个 epoch（随机 id 或启动时间），放进每一帧和 `GET /api/sessions/{id}` 的响应里；客户端发现 epoch 变了，就把 `lastSeq` 归零、重新拉一次历史。`bus.py`、`frames.py`、`app.js` 各改几行。

### 4.4 SSE 还是 WebSocket

Paseo 用 WebSocket，是因为它有终端二进制流，一条连接要同时订阅很多 agent，还要走中继。SimpleAgent 在 [client-ui.md 第 6 节](../design/client-ui.md#6-与核心的接口本地-api) 选 SSE 的理由（客户端 → 服务端都是普通请求，服务端 → 客户端是单向事件流，`Last-Event-ID` 现成）现在仍然成立。

值得记下的是 Paseo「一条连接复用所有订阅」的好处。SimpleAgent 在 client-ui.md 10.4 里踩过「浏览器对同一个 host 只给 6 条连接」的坑，现在的解法是后台标签页断开 SSE。以后要同时盯多个会话（比如指挥台同时显示几个子任务的实时输出），可以加一条全局 SSE、帧里带 `session_id`，不必换成 WebSocket。

---

## 5. 流式渲染

### 5.1 Paseo 的管线（`docs/agent-stream-performance.md`）

```
provider 的增量输出
  → AgentStreamCoalescer（daemon：每个 agent 最多 60ms 发一次，第一帧立刻发，后面的攒到窗口结束）
  → agent_stream 消息
  → 前端 reducer 队列（每个动画帧提交一次；有定时器兜底，因为后台标签页不触发动画帧）
  → Markdown 按块拆成多行显示
  → 按积压量匀速显示文字
```

几条规则：

- **第一帧立刻发**（leading + trailing）。只攒尾巴的话，每一轮的第一个字都要多等一个窗口。
- **存的是全文，只有显示的那一截匀速放出**。复制、选中、滚动高度都读全文。
- **第一次看到的文字整段显示**，只有新增的部分匀速放出。这样历史加载、行重新挂载都不用特殊处理。
- **Markdown 按块拆**：已经完成的块按对象身份保留，只重解析正在增长的最后一块。

实测数据（Expo 网页 + 真实 Claude Haiku）：显示间隔的 p95 从 383ms 降到 17ms，推进了文字的帧从 6% 升到 87%。

### 5.2 SimpleAgent 现状

`app.js` 每收到一个 `text_delta` 都执行 `el.innerHTML = renderMarkdown(state.acc)`（`app.js:604`），也就是每来一小段，就把整段回复重新解析、重建一次 DOM。回复越长越慢，总开销随长度平方增长；用户选中的文字也会被重建打断。

可以按需借鉴，从便宜到贵：

1. **按块拆分**：遇到空行就把前面已经完成的块固定下来，只重渲染最后一块。只改 `app.js` 和 `markdown.js`。
2. **前端按动画帧合并**：同一帧里收到的多个 delta 只渲染一次。
3. **后台合并**：`Runner` 发帧前攒 50～60ms。要确认 trace 和 JSONL 镜像不受影响。

目前没有卡顿的反馈，等觉得输出一跳一跳、或者长回复变卡的时候再做。

---

## 6. 外部 agent 接入与审批（对 M8 最有用）

### 6.1 SimpleAgent 现状

`src/simpleagent/agents/` 用的是两家的无头模式：

- Claude Code：`claude --output-format stream-json --verbose --include-partial-messages -p -- "<prompt>"`。全放行档加 `--dangerously-skip-permissions`；只读档把工具砍到三个只读工具，再加 `--permission-mode dontAsk --permission-prompts none`。
- OpenCode：`opencode run --format json`，全放行档加 `--auto`，否则预置只读权限。

无头模式下「要问的」操作要么挂住、要么自动拒绝，所以只有只读 / 全放行两档，接不到工作台的审批卡（[client-ui.md 10.10](../design/client-ui.md#1010-外部-cli-执行者claude-code--opencode-真正跑起来了)）。

### 6.2 Paseo 的接法

每家 provider 实现同一个接口（`packages/server/src/server/agent/agent-sdk-types.ts`，下面是节选）：

```ts
interface AgentClient {            // :743，每个 provider 一个
  createSession(config, launchContext?, options?): Promise<AgentSession>;
  resumeSession(handle, overrides?, launchContext?, options?): Promise<AgentSession>;
  fetchCatalog(options, context?): Promise<ProviderCatalog>;   // 模型和模式一起查
  // ...
}

interface AgentSession {           // :663，一个活着的会话
  startTurn(prompt, options?): Promise<{ turnId: string }>;
  subscribe(callback: (event: AgentStreamEvent) => void): () => void;
  streamHistory(): AsyncGenerator<AgentStreamEvent>;           // 恢复时从 provider 的记录重建
  getPendingPermissions(): AgentPermissionRequest[];
  respondToPermission(requestId, response): Promise<...>;
  setMode(modeId): Promise<...>;                               // plan / default / full-access 等
  describePersistence(): AgentPersistenceHandle | null;        // resume 用的句柄
  interrupt(): Promise<void>;
  close(): Promise<void>;
}
```

关键是各家怎么拿到实时的权限请求：

| provider | 接法 | 权限请求从哪来 |
|---|---|---|
| Claude Code | `@anthropic-ai/claude-agent-sdk` | `canUseTool` 回调（`providers/claude/agent.ts:3301`），回调挂起，等用户决定 |
| OpenCode | 起 `opencode serve`，用 `@opencode-ai/sdk` 订阅事件 | `permission.asked` 事件（`providers/opencode-agent.ts:2306`），用 `client.permission.reply(...)` 回复 |
| Codex | `codex app-server`（JSON-RPC） | app-server 发来的审批请求 |
| Gemini CLI 等 | 通用 ACP（配置 `extends: "acp"` + 启动命令） | ACP 的 `session/request_permission` |

工具调用统一归成 `ToolCallDetail`（`packages/protocol/src/agent-types.ts:212`）：`shell`、`read`、`edit`、`write`、`search`、`fetch`、`worktree_setup`、`sub_agent`、`plain_text`。前端按类型渲染同一种卡片，不关心是哪家 agent 产生的。

### 6.3 对 SimpleAgent 的意义

- **Python 这边有对应的路**：`claude-agent-sdk`（PyPI，2026-09-27 查到的最新版 0.2.160）有 `can_use_tool` 回调；`opencode serve` 是 HTTP + SSE，用 `httpx` 就能调。在回调里 `await` 现有的 `APIApprover.request()`，外部 agent 的权限请求就能和内置 loop 一样推审批卡。
- **代价**：`claude-agent-sdk` 是新的运行时依赖，和 ARCHITECTURE 原则 7「依赖尽量少」有冲突。可以做成可选依赖，没装就退回现在的无头模式。另外，Paseo 这两家的适配各有五六千行（`claude/agent.ts` 6419 行，`opencode-agent.ts` 5442 行），大部分在处理边角情况（中断重启、子 agent、历史回放、超时）。SimpleAgent 不必追求功能对齐，先做到「能推审批卡 + 能 resume + 能取消」。
- **执行者接口可以对照 `AgentSession` 调整**：SimpleAgent 的执行者现在每一轮起一个 CLI 进程（`CliTurn`）。换成 SDK / 服务模式后，会话一直活着，可以中途审批、中途打断，接口会更接近 Paseo 的 `startTurn` / `subscribe` / `respondToPermission` / `interrupt`。
- **工具卡归一**：内置 loop 的工具和外部 agent 的工具都映射到一组固定类型（读、写、改、命令、搜索……）。这样变更 tab 和验证状态的 stale 判定也能覆盖外部 agent 的改动。

---

## 7. 通知路由（M4 第 ③ 步可以照着写）

### 7.1 Paseo 的做法

客户端定时发 `client_heartbeat`（`packages/protocol/src/messages.ts:2831`），内容是设备类型、页面是否可见、正在看哪个 agent / 终端、最近一次活动时间。

需要提醒时（agent 跑完、要审批、出错），daemon 调一个纯函数决定怎么提醒（`packages/server/src/server/agent-attention-policy.ts`，80 行）：

```
对每个客户端：最近一次活动在 3 分钟内才算在线（PRESENCE_THRESHOLD_MS = 180_000）
  有在线的客户端页面可见、并且正盯着这个 agent → 什么都不做
有在线的客户端 → 在最近活跃的那个客户端里提醒
都不在线       → 系统推送（出错不推）
```

两条配套规则：

- **在线状态不是投递依据**：心跳过期只影响提不提醒，不能让事件从实时流里消失（§4.2）。
- 桌面版的系统通知走 Electron 的 `Notification`，点通知会聚焦窗口并跳到对应的 agent。

### 7.2 映射到 SimpleAgent

ARCHITECTURE 里写的「通知路由：客户端在线推客户端，否则走系统通知 / IM」就是这件事。落到 M4 第 ③ 步：

- 工作台前端定时上报一次在线状态（页面是否可见、当前会话、最近活动时间），`visibilitychange` 时立即补报一次。
- 后台照搬这个纯函数：
  - 有人正看着这个会话 → 不提醒
  - 有在线的客户端 → 推一帧提醒，同时进消息
  - 都不在线 → 按任务的 `notify` 配置走 macOS 通知 / 飞书 / Telegram
- 这个函数没有 IO，连 FakeLLM 都用不上，直接单测几种在线组合就行。

---

## 8. 本地服务安全

Paseo 的做法（`SECURITY.md`）：

- **Host 白名单**：每个 HTTP 请求和 WebSocket 升级都校验 `Host`，默认只放行 `localhost`、`*.localhost` 和字面 IP，其他一律返回 `403 Host not allowed`。理由是 CORS 挡不住 DNS rebinding：恶意域名解析到 127.0.0.1 之后，浏览器眼里就是同源请求。
- **HTML 预览沙箱**：预览 agent 写的 `.html` 时，用不透明源加严格的 CSP，禁网络、禁存储、禁弹窗。
- 远程访问另有密码、配对邀请和中继的端到端加密。

SimpleAgent 的 `serve/app.py` 只绑 127.0.0.1。`_dispatch`（`app.py:821`）不校验 Host，各接口不看 Content-Type 直接 `json.loads`。后果有两个：

- 恶意网页借 DNS rebinding 能读写全部 API，比如列出空间、往全放行空间的会话里发指令，最终执行 bash。
- 不用 rebinding，也能用 `text/plain` 的跨站 `no-cors` POST（不触发 CORS 预检）调 `POST /api/spaces`、`POST /api/inbox` 这类不需要知道 id 的写接口。

Chrome 新版的本地网络访问限制能挡掉一部分，Safari、Firefox 不一定。修法是 Host 白名单 + 写请求要求 `application/json` + 带 Origin 时校验同源，已在 2026-09-27 单独开任务修复。

工作台以后如果要预览 agent 生成的 HTML，也要照 Paseo 那样放进沙箱 iframe。

---

## 9. 另一条路：把 SimpleAgent 接进 Paseo

Paseo 能挂任何讲 ACP 协议的 agent（`public-docs/custom-providers.md`）：

```json
{
  "agents": {
    "providers": {
      "simpleagent": { "extends": "acp", "label": "SimpleAgent", "command": ["sa", "acp"] }
    }
  }
}
```

SimpleAgent 只要实现一个 ACP stdio 服务端（比如叫 `sa acp`），就能在 Paseo 的桌面、网页、手机上跑自己的 loop，审批走 ACP 的 `session/request_permission`。

- **好处**：马上有成熟的界面和手机端；实现 ACP 本身也是在学协议（[2026-09-24 的调研](2026-09-24-agent-to-agent-orchestration.md) 梳理过 ACP 的定位）。
- **代价**：空间、指挥台调度、消息、备忘、验证状态这些工作台概念，Paseo 里都没有，要写 TS 插件补；界面和交互的控制权在别人的产品里，和「逻辑在自己的 Python 里」「学习是首要目标」都冲突。

建议：可以当成 M8 的一个小实验（`sa acp` 顺带也能让 Zed 这类支持 ACP 的编辑器调用），不作为工作台主线。

---

## 10. 不借鉴的清单

| 做法 | Paseo 为什么需要 | SimpleAgent 为什么不需要 |
|---|---|---|
| Expo / React Native | 手机端是核心场景 | 只做桌面 |
| 多 host 管理 | 一台手机连多台开发机 | 单机 |
| 中继 + 端到端加密 | 不开端口也能远程访问 | 本机使用；真要远程可以走 SSH 隧道 |
| 协议只增不减 + 能力协商 + `COMPAT(...)` 标记 | app 和 daemon 版本会错开 | 前后端同仓、同版本发布 |
| zod-aot 生成入站校验代码 | 消息类型多，要性能 | 消息量小，pydantic 或手写校验就够 |
| 本地副本缓存（IndexedDB / SQLite） | 手机离线也要先显示内容 | 后台就在本机，重新拉一次就行 |
| 时间线的分页补齐、投影合并、`sourceSeqRanges` | 中继有帧大小限制，历史存在 provider 那边 | 历史就在自己的 JSONL 里；只缺 epoch（§4.3） |
| 终端 PTY 二进制帧 | 有内置终端 | 不做内置终端 |
| 内置浏览器、语音 | 产品功能 | 暂无需求 |

---

## 11. 建议的落地顺序

| 时机 | 事项 | 章节 |
|---|---|---|
| 现在（小修） | Host 白名单 + Content-Type 校验（已开任务） | §8 |
| 现在（小修） | 事件带 epoch，后台重启后客户端归零重拉；先写个测试确认这个缺口 | §4.3 |
| M4 第 ③ 步 | 在线状态上报 + 通知路由纯函数 | §7 |
| M8 外部 agent | Claude 改用 `claude-agent-sdk` 的 `can_use_tool`，OpenCode 改用 `opencode serve`，推审批卡；工具卡归一 | §6 |
| M8（可选实验） | `sa acp`，在 Paseo / Zed 里用 SimpleAgent | §9 |
| 包桌面壳时 | 薄壳 + 桥接对象 + 只停自己拉起的 `sa serve` | §3 |
| 觉得流式卡顿时 | Markdown 按块渲染 → 按帧合并 → 后台合并 | §5 |
