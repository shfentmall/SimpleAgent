# 业界 AI 个人工作台 / Code Agent 功能调研（2026-09）

> 调研目的：盘清业界功能盘子，判断**哪些值得 SimpleAgent 自己做定制化**，哪些应该直接接入生态。
>
> 结论口径：本项目首要目标是**学习 agent 机制**，次要目标是**本地解决真实问题**。所以「值不值得自研」不是单纯的成本问题，而是「自研能不能学到东西 + 自研出来的东西能不能被自己改写」。
>
> 信息来源可靠性说明：本报告的**结构性结论**（分层、标准收敛、成本量级）在多个独立来源间一致，可信度高。**具体版本号、价格、star 数、benchmark 分数**来自厂商页与二手汇总，变化极快且部分来源质量参差，引用时请当量级参考而非事实。

---

## 0. 三个核心结论

**结论一：行业正在收敛到四个开放标准，接入成本已经塌了。**
`AGENTS.md`（项目指令）、`SKILL.md`（技能）、`MCP`（工具接入）、`A2A`（agent 间协作）——这四件事在两年前各做各的，现在被 Claude Code / Codex / Cursor / Gemini CLI / Cline / Roo / Goose / opencode 共同支持，且 MCP 已捐给 Linux Foundation 下的 Agentic AI Foundation 由多方共管。
**推论**：SimpleAgent 不需要为「接生态」写任何适配层。对齐格式即可白嫖整个生态。反过来，**把精力花在这些标准上是低学习价值的重复劳动**。

**结论二：自研的价值集中在「机制层」，不在「集成层」。**
业界产品功能可以粗分为两层：**机制**（上下文怎么压、记忆怎么组织、上下文怎么隔离、权限怎么判、事件怎么流）和**集成**（接哪些平台、接哪些 SaaS、接哪些浏览器）。前者是各家真正的差异化，也是学习价值所在；后者是体力活，且有标准协议兜底。
**推论**：SimpleAgent 应该把自研火力压在机制层，集成层一律走 MCP / 外部 CLI。

**结论三：SimpleAgent 的差异化位置是「本地优先 + 从零可读 + 可改写」，不是「接得更多」。**
业界个人工作台（OpenClaw、WorkBuddy、QoderWork、Raycast AI、Claude Desktop 等）的主线竞争是**多平台接入 + 云端调度 + 开箱即用**。这条赛道 SimpleAgent 打不了也不该打——它是自用工具，用户就是开发者本人。
**推论**：凡是「为了服务陌生人而存在的功能」（安装向导、技能市场、插件分发、多租户沙箱、云端 Routines）一律不做；凡是「让自己能读懂、能改、能实验」的功能优先级拉满。

---

## 1. 格局：两条线正在合流

以前「code agent」和「个人工作台」是两拨产品，2026 年这条界线基本消失了。

| 维度 | Code Agent 线 | 个人工作台线 | 合流后的形态 |
|---|---|---|---|
| 代表 | Claude Code、Codex CLI、Gemini CLI、Cursor、opencode、Cline、Aider、Amp、Goose | OpenClaw、Claude Desktop/Cowork、ChatGPT Desktop、Manus Desktop、Raycast AI、WorkBuddy、QoderWork | 桌面客户端 + 常驻后台引擎 |
| 主要场景 | 改代码、跑测试、提 PR | 整理文件、收发消息、查资料、日程 | 「让 agent 替我把一件事办完」 |
| 核心交互 | 终端 REPL / TUI | 聊天窗口 + 快捷键唤起 + IM | 多面板并行 + IM 远程触发 |
| 触发方式 | 人手敲 | 定时 / 心跳 / 事件 / 消息 | 全都要 |

**合流的三个硬证据：**

1. **Claude Code 桌面版重构成 Mission Control**：并行本地会话分屏、Git worktree 隔离、侧边聊天、SSH 到远程机器。更关键的是 **Routines**——定时 / webhook / GitHub 事件触发的跑批结果，会作为普通 session 落回同一个侧边栏，和手动开的会话共用 diff 视图和过滤器。也就是说「自动化产物」和「交互产物」被统一成同一个对象。
2. **Raycast AI 加了 Automations + Screen Awareness + 项目工作区记忆**：这是把「效率启动器」改造成常驻 agent 宿主，连「给 agent 一块专属工作区 + 项目级指令 + 记忆」都照搬了 code agent 的做法。
3. **OpenClaw 用 code agent 的配方做通用助手**：`SOUL.md` / `USER.md` / `AGENTS.md` / `MEMORY.md` + `SKILL.md` 技能 + heartbeat 心跳 + tool policy 审批，这一整套是直接从 code agent 生态搬过来的。

**对 SimpleAgent 的意义**：`docs/ARCHITECTURE.md` 里「桌面客户端 + 常驻后台引擎」的长期形态，方向是对的，而且已经踩在行业主线上（W 里程碑已经跑通空间/会话/外部 CLI 执行者）。不需要改方向，需要的是**知道哪些部件业界已经趟过了、怎么趟的**。

---

## 2. Feature 全景地图（九层）

下面按「从内核到外围」分九层。每层给出：业界做法、成熟度、实现要点。

### L1 · Agent 核心循环

| 功能 | 业界做法 | 成熟度 | 要点 |
|---|---|---|---|
| 流式输出 + 思考内容分离 | 所有产品标配 | 商品化 | reasoning 字段各家命名不一（`reasoning_content` / `reasoning`），且**历史里要不要回传**各家策略不同 |
| 工具调用循环（ReAct） | 所有产品标配 | 商品化 | 流式 `tool_calls` 按 index 分片，必须自己拼 |
| 中断 + 历史修复 | Claude Code（Esc 打断）、Codex | 商品化 | 中断时未返回的 tool_call 必须补「已中断」结果，否则下次请求被 API 拒 |
| **Plan mode（先规划后执行）** | Claude Code、Cursor、Codex、Cline 的 plan/act 双模式 | 已标准化 | 本质是「只读工具集 + 强制先输出计划 + 用户确认后再放开写」，**不需要新机制**，是 prompt + 工具子集 |
| **Checkpoint / rewind** | Claude Code（Esc Esc 回滚）、Cursor 检查点 | 已标准化 | 两级：会话级 truncate（回退消息）+ 文件级快照（git stash / worktree） |
| Auto-continue | Claude Code（用量限制重置后自动续） | 小众 | 对自用意义不大 |
| max_steps / 成本上限 | 全都有 | 商品化 | 已做 |

### L2 · 上下文工程

| 功能 | 业界做法 | 成熟度 | 要点 |
|---|---|---|---|
| **分级压缩** | Claude Code `/compact`、Codex 自动压缩 | 核心差异化 | 通行三级：写入时截断 → 清理旧 tool 结果 → LLM 摘要压缩。**关键约束：不能拆开 tool_call 和它的结果** |
| **前缀缓存友好** | 各家都在做 | 核心差异化 | system prompt 和工具列表保持稳定（别动态插时间戳/随机序），命中 prompt cache 能把输入成本压掉一个量级 |
| 上下文可视化 | Claude Code `/context` | 已标准化 | 展示各段占用，是调 prompt 的必备工具 |
| **MCP 工具懒加载 / 工具搜索** | Claude Code 的 MCP Tool Search | 新趋势 | 会话开始时不再注入全部 MCP 工具定义，改为按需加载——**宣称能省 95% 上下文**。工具一多这是必需品 |
| Token 预算 | 全都有 | 商品化 | 用上次 usage + 字符估算，不用装 tokenizer |
| 循环检测 | 部分产品 | 待成熟 | 同一工具同参数重复调用 N 次就打断 |

### L3 · 记忆

这是**个人工作台真正的护城河**，也是 code agent 和通用助手差距最大的地方。

| 功能 | 业界做法 | 成熟度 | 要点 |
|---|---|---|---|
| **项目指令注入** | `AGENTS.md` / `CLAUDE.md` / `GEMINI.md`，已收敛到 `AGENTS.md` | 已标准化 | 会话开始自动注入 cwd 下的 AGENTS.md |
| **文件式长期记忆** | OpenClaw / Claude Code 的 `MEMORY.md`（索引）+ `memory/YYYY-MM-DD.md`（日志） | 已标准化 | 全部 markdown，可 grep、可 git、可人审。**不要一上来就上向量库** |
| **分层记忆（hot/cold）** | 社区最佳实践 | 最佳实践 | hot 层（当前目标/阻塞/决策）控制在 4KB 以内、结构化（KV 而非散文）；cold 层按需读。有人实测把 55KB 单文件拆成 3KB hot + 8KB cold 后，推理质量立刻变好——**不是因为信息变了，是因为留出了思考空间** |
| 记忆写入纪律 | 「Write > Brain」共识 | 最佳实践 | 用户说「记住」→ 立刻写文件；做决策 → 立刻记录含**理由**（只记 what 三周后就没用了，要记 why） |
| 记忆整理 | 定期把日志蒸馏进 `MEMORY.md` | 最佳实践 | `MEMORY.md` 是快照不是流水账，要覆盖旧条目而不是追加 |
| 语义检索 | 部分产品加向量库 | 待成熟 | 通行的判断：**如果重要的事需要靠搜索才能想起来，说明 curation 失败了**。向量只做兜底 |
| 时间衰减 | 社区实践 | 实验性 | `score × max(0.3, exp(-0.03 × 天数))`，顺手给出 GC 依据 |
| 记忆反馈环风险 | 社区踩坑 | 注意 | agent 写记忆 → 读记忆 → 放大自身偏差。乐观的总结会被越读越乐观 |
| 知识库 / 文档 RAG | Khoj、各工作台 | 已标准化 | SimpleAgent `plan.md` 里提到过「知识库」，属 L3 外围 |

### L4 · 工具与生态接入

| 功能 | 业界做法 | 成熟度 | 要点 |
|---|---|---|---|
| 内置文件/搜索/执行工具 | 全都有 | 商品化 | 已做（7 个） |
| **MCP 客户端** | 所有主流产品都支持 | 已标准化 | stdio 简单（本地子进程）；远程是 **Streamable HTTP**（SSE 已废弃）+ **OAuth 2.1 动态客户端注册**。还有三个进阶能力：**elicitation**（server 反向问用户）、**sampling**（server 反向请求模型推理）、**tasks**（call-now-fetch-later 长任务） |
| **Skills（渐进式披露）** | `SKILL.md` 已成跨工具开放标准（agentskills.io） | 已标准化 | 只把 frontmatter 描述常驻 prompt，正文按需 `load_skill`。Claude Code 还扩展了 invocation 控制、子 agent 执行、动态上下文注入 |
| 外部 agent 作为工具 | Claude Code / Codex 互相包、ACP 协议 | 已标准化 | 无头 CLI 流式输出（`claude -p --output-format stream-json`）+ 保存 session id 以追问 |
| 自定义斜杠命令 | Claude Code（已并入 Skills） | 已标准化 | 命令文件和 Skill 现在等价，简单 prompt 模板用命令，要带资源的用 Skill |
| 浏览器自动化 | Playwright MCP / Chrome DevTools MCP / Computer Use | 已标准化 | 全部走 MCP，**没人自己写浏览器驱动** |
| 代码智能（LSP） | Claude Code、opencode、Zed | 新趋势 | 接 LSP 做符号级跳转和实时类型错误，大仓库比 grep 准 |
| A2A / agent 间协议 | A2A v1.0（2026-04 转正），ACP 已并入 | 新趋势 | 「MCP 是手，A2A 是工人之间的协调」 |
| **CLI vs MCP 的成本** | Scalekit 基准（GitHub 操作） | 重要 | 简单查询 CLI 1365 token vs MCP 44026 token（**32×**）；复杂查询 7×；成功率 100% vs 72%；月成本 $3.2 vs $55.2。**结论：本地开发动作走 CLI，外部服务集成走 MCP** |

### L5 · 权限与安全

| 功能 | 业界做法 | 成熟度 | 要点 |
|---|---|---|---|
| allow / ask / deny | 全都有 | 商品化 | 已做 |
| 目录边界 + 危险命令 | 全都有 | 商品化 | 已做。业界共识是**清单要窄**，拦太宽会逼模型绕审批 |
| **参数级权限规则** | Claude Code `Tool(param:value)` 语法 | 已标准化 | 例：`Agent(model:opus)` 直接禁掉；`Bash(git push:*)` 单独放行。比工具级粒度精确得多 |
| **Hooks（生命周期钩子）** | Claude Code 有 **25+ 个事件** | 已标准化 | SessionStart / UserPromptSubmit / PreToolUse / PostToolUse / PostToolUseFailure / PostToolBatch / SubagentStart / SubagentStop / Stop / PreCompact / SessionEnd / CwdChanged / FileChanged / Notification…… 执行体有五种：shell 命令、HTTP、MCP tool、prompt（LLM 判一次）、agent（多轮验证）。PreToolUse 能改参数（`updatedInput`）能拦；PostToolUse 能改工具输出（`updatedToolOutput`） |
| Auto mode + 分类器 | Claude Code（模型判断每个动作安不安全） | 实验性 | 用分类器替代逐次弹窗，适合无人值守长跑。**但对个人自用是过度设计** |
| 沙箱隔离 | Docker → gVisor → Firecracker 三档 | 已标准化 | 自托管个人 agent 的通行配方：**默认拒网的临时容器**（`--read-only` + `--cap-drop=ALL` + `--pids-limit` + `--memory` + 只有 `/workspace` 可写），就能覆盖约 95% 场景。gVisor 用在「agent 要执行网页/GitHub issue 里来的代码」时 |
| 出口白名单 | 企业实践 | 最佳实践 | 防外泄最高杠杆的一招：默认拒出网，只放行模型 API 和包仓库 |
| Prompt injection 防护 | OWASP ASI05 / 各类研究 | 未解决 | 共识：**沙箱不防注入，只限制被注入后的爆炸半径**。任何 agent 读到的东西（README、网页、PDF、Slack 消息）都是输入 |
| 审计日志 | 企业必备 | 商品化 | 记「跑了什么、碰到了什么、改了什么」 |
| MCP 安全 | 2026-04 曝出 stdio 传输层系统性 RCE，波及 7000+ server | 高风险 | **把每个 MCP server 当不可信代码**，与 agent 同沙箱跑；每个任务的 MCP 权限单独收敛 |

### L6 · 编排与自动化

| 功能 | 业界做法 | 成熟度 | 要点 |
|---|---|---|---|
| **定时任务 / daemon** | OpenClaw cron、Claude Code Routines、Raycast Automations | 已标准化 | 个人自动化的第一场景。有本地（croniter + launchd）和云端（Routines 跑在厂商基础设施）两种路线 |
| **Heartbeat 心跳** | OpenClaw（默认 30 分钟） | 已标准化 | 定期唤醒 agent 读 `HEARTBEAT.md` 检查清单，有事就行动，没事返回 `HEARTBEAT_OK` 被静默丢弃。**比纯 cron 更灵活**——agent 自己决定今天要不要干活 |
| Webhook / 事件触发 | Routines、OpenClaw | 已标准化 | GitHub 事件、外部 webhook 都能拉起 agent loop |
| **子 agent（上下文隔离）** | Claude Code subagents、各产品 | 已标准化 | 独立上下文、受限工具集、只把结论返回父会话。典型用途：长代码审查、深调研、并行探索方案 |
| Agent Teams | Claude Code（多会话互相发消息、共享任务列表） | 新趋势 | 每个 teammate 有独立上下文窗口，还能互相 message |
| 并行 worktree 隔离 | Claude Code 桌面、Warp、Windsurf | 已标准化 | 每个并行任务一个 git worktree，互不干扰 |
| Todo 工具 / 任务列表 | Claude Code、Cline | 已标准化 | 给模型一个显式的计划载体，也是给用户的进度视图 |
| 后台任务 + 实时监控 | Claude Code 的 Monitor tool | 新趋势 | 把后台脚本 stdout 逐行流给模型，省掉轮询 |
| 会话调度 | Claude Code `/loop` | 实验性 | 会话内定时重复 prompt |

### L7 · 交互前端

| 功能 | 业界做法 | 成熟度 | 要点 |
|---|---|---|---|
| REPL / TUI | 全都有 | 商品化 | 已做。opencode / Crush 走 TUI 路线 |
| Headless / CI | Claude Code `-p`、Codex、Gemini CLI | 已标准化 | 已做。CI 里跑 `claude -p` 做 lint / 测试 / 总结 diff |
| **桌面客户端** | Claude Code Desktop（Mission Control）、Codex Desktop、Warp、Copilot App | 已标准化 | 关键词：**并行面板**、拖拽布局、侧边聊天（问完不污染主上下文）、diff 视图、多会话统一侧边栏 |
| IM 接入 | OpenClaw（20+ 平台）、WorkBuddy、QClaw | 已标准化 | 微信 / 飞书 / 钉钉 / Telegram / Slack / WhatsApp / iMessage / Discord / Signal。国内产品普遍做「微信远程遥控」 |
| 通知路由 | 各工作台 | 已标准化 | 客户端在线推客户端，否则走系统通知或 IM |
| 语音 | Raycast、ChatGPT Desktop | 已标准化 | 输入侧转写 + 输出侧 TTS |
| 屏幕感知 / Computer Use | Claude Cowork、Manus、Raycast Screen Awareness | 实验性 | 截图 + 元素识别 + 填表。稳定性和隐私代价都高 |
| 移动端 | 各工作台 | 已标准化 | 手机触发、手机看进度 |
| 快捷键唤起 | Raycast、Claude Desktop（Option 双击） | 已标准化 | 常驻效率工具标配 |

### L8 · 可观测与评测

| 功能 | 业界做法 | 成熟度 | 要点 |
|---|---|---|---|
| 全量 trace | 全都有 | 商品化 | 已做（`traces/` 落全量请求体）。业界共识是**只记最终输出是最大的盲区**——第 10 步答错，病根常在第 1 步的检索 |
| **OTel GenAI 语义约定** | OpenTelemetry 官方标准，Copilot / Codex / Claude Code 都已支持 | 已标准化 | span 树：`invoke_agent` → `chat` / `execute_tool`。属性：`gen_ai.request.model`、`gen_ai.usage.input_tokens` 等。**内容放 span events 而非 attributes**（attributes 有大小限制且会泄漏 PII，events 能在 collector 侧过滤） |
| 本地 trace 查看 | Aspire Dashboard、Langfuse（自托管） | 已标准化 | Aspire Dashboard 是免费开源单容器，接 OTLP 就能用 |
| Golden set replay | 企业实践 | 最佳实践 | 50–500 条带预期结果的 trace，每天或每次部署后重放，分数掉了就是 drift |
| LLM-as-judge 在线抽样 | 企业实践 | 最佳实践 | 对生产流量抽样打分，看 p50/p95 移动 |
| Tool schema 快照测试 | 企业实践 | 最佳实践 | CI 里记录工具 schema，变了就 diff 报警，提前发现外部 API 破坏性变更 |
| 成本 / 延迟看板 | 全都有 | 商品化 | |
| Drift 检测 / 告警 | 企业实践 | 最佳实践 | 分页级 / 工单级 / 仅看板级三档阈值 |

### L9 · 配置与分发

| 功能 | 业界做法 | 成熟度 | 要点 |
|---|---|---|---|
| 配置文件 + 多 profile | 全都有 | 商品化 | 已做 |
| 设置分层 | 用户级 / 项目级 / 插件级 / 托管级 | 已标准化 | Claude Code 有托管设置 drop-in 目录，企业按团队分发策略片段而不冲突 |
| **插件包** | Claude Code：plugin = skills + hooks + subagents + MCP + bin/PATH 注入，技能带命名空间 | 已标准化 | 跨仓库复用或对外分发时才值得做 |
| 技能市场 | Vercel skills.sh（宣称索引约 60 万技能）、ClawHub | 已标准化 | 自用完全不需要 |
| 跨工具可移植 | `SKILL.md` / `AGENTS.md` 开放标准 | 已标准化 | 一份技能能同时跑在 Claude Code / Codex / Cursor / Gemini CLI / Cline / Goose |
| 裸模式 | Claude Code `--bare`（跳过全部本地配置发现） | 小众 | 确定性的 CI 模式 |

---

## 3. 战略结论：四个收敛中的标准

这张表是整份报告**最值钱的部分**。它意味着「接生态」这件事已经被标准化了，SimpleAgent 只要对齐格式，就能免费获得所有主流产品的工具生态。

| 标准 | 解决的问题 | 谁在做 | SimpleAgent 该做什么 |
|---|---|---|---|
| `AGENTS.md` | 项目指令注入 | 已事实统一（Claude Code / Codex / Cursor 都读） | M7 直接读 cwd 下 AGENTS.md，**不做自己的格式** |
| `SKILL.md` | 技能 / 渐进式披露 | agentskills.io 开放标准，跨工具可移植 | M7 对齐 frontmatter 规范即可，**不发明格式** |
| `MCP` | 工具与服务接入 | Linux Foundation / AAIF 共管，14000+ server | M5 只做 stdio 手写，远程/OAuth 上官方 SDK，**不自己实现传输层** |
| `A2A` | agent 间协作 | 2026-04 转正，AAIF 管辖 | M8 之后按需，**个人自用大概率不需要** |

**成本结论（可直接指导架构）**：MCP 比 CLI 贵 7–32 倍 token。所以 SimpleAgent 的工具策略应该是——
> **内置工具（已做）→ 本地 CLI 优先 → MCP 只用于「没有 CLI 的外部服务」。**

这条对个人用户尤其重要：省钱就是省钱。

---

## 4. SimpleAgent 适配性评估矩阵

评估四维：
- **学习价值**：自研能学到多少 agent 机制（本项目首要目标）
- **自研成本**：按从零手写、依赖尽量少的口径估
- **定位契合**：与「本地优先的个人工作台 + 可改写」的契合度
- **结论**：🟢 强烈建议自研 ｜ 🟡 先做轻量版 ｜ 🔵 直接接入 ｜ 🔴 明确不做

| # | 功能 | 业界代表 | 学习价值 | 自研成本 | 定位契合 | 结论 | 里程碑 |
|---|---|---|---|---|---|---|---|
| 1 | 分级上下文压缩 | Claude Code `/compact` | ★★★★★ | ★★★ | ★★★★ | 🟢 | **M6** |
| 2 | 前缀缓存友好 prompt | 各家 | ★★★★ | ★★ | ★★★★★ | 🟢 | M6 |
| 3 | 文件式分层记忆 | OpenClaw `MEMORY.md` | ★★★★★ | ★★★ | ★★★★★ | 🟢 | **M7** |
| 4 | Skills 渐进式披露 | `SKILL.md` 标准 | ★★★★★ | ★★ | ★★★★★ | 🟢 | **M7** |
| 5 | 子 agent（上下文隔离） | Claude Code subagents | ★★★★★ | ★★★★ | ★★★★ | 🟢 | **M8** |
| 6 | 定时 daemon + 心跳 | OpenClaw heartbeat | ★★★★ | ★★★ | ★★★★★ | 🟢 | **M4** |
| 7 | Hooks 生命周期 | Claude Code 25+ 事件 | ★★★★ | ★★★ | ★★★★ | 🟢 | **M8** |
| 8 | 参数级权限规则 | `Tool(param:value)` | ★★★ | ★★ | ★★★★ | 🟢 | M3 补 |
| 9 | Plan mode | Claude Code / Cursor | ★★★ | ★ | ★★★★ | 🟢 | M4+ |
| 10 | 事件可序列化总线上移 | 各家客户端 | ★★★★ | ★★ | ★★★★★ | 🟢 | W 已守约定 |
| 11 | MCP 客户端（stdio 手写） | MCP 标准 | ★★★★★ | ★★★ | ★★★★ | 🟢 | **M5** |
| 12 | 工具懒加载 / 工具搜索 | Claude Code MCP Tool Search | ★★★★ | ★★ | ★★★★ | 🟡 | M5 后 |
| 13 | 上下文可视化 | `/context` | ★★★ | ★ | ★★★★★ | 🟡 | M6 后 |
| 14 | 循环检测 | 各家 | ★★ | ★ | ★★★★ | 🟡 | M6 |
| 15 | Checkpoint / rewind（会话级） | Esc Esc 回滚 | ★★★ | ★ | ★★★ | 🟡 | 已有 truncate |
| 16 | Checkpoint（文件级 git 快照） | worktree 隔离 | ★★★ | ★★★ | ★★★ | 🟡 | M8+ |
| 17 | 记忆语义检索 | ChromaDB / BM25 | ★★★ | ★★★ | ★★★ | 🟡 | 先 grep/BM25 |
| 18 | 记忆时间衰减 + GC | 社区实践 | ★★★ | ★ | ★★★ | 🟡 | M7 后 |
| 19 | 沙箱（Docker 临时容器） | 默认拒网容器 | ★★★★ | ★★★ | ★★★ | 🟡 | 可选开关 |
| 20 | 桌面客户端 | Mission Control | ★★★★ | ★★★★★ | ★★★★★ | 🟡 | **W** 进行中 |
| 21 | 评测 harness | `evals/` + 校验脚本 | ★★★★ | ★★ | ★★★★★ | 🟡 | **M9** |
| 22 | OTel GenAI 导出 | OTel 标准 | ★★★ | ★★ | ★★★ | 🔵 | M9 后按需 |
| 23 | MCP 远程 / OAuth / elicitation | 官方 SDK | ★★ | ★★★★ | ★★ | 🔵 | 用 SDK |
| 24 | 浏览器自动化 | Playwright MCP | ★ | ★★★★★ | ★★★ | 🔵 | 用 MCP |
| 25 | IM 接入（微信/飞书/TG） | webhook / MCP | ★★ | ★★★★ | ★★★★ | 🔵 | M4 的 notify |
| 26 | 本地 trace 查看器 | Aspire Dashboard | ★ | ★★★ | ★★★ | 🔵 | 导出 OTLP |
| 27 | 代码智能（LSP） | Claude Code / opencode | ★★★ | ★★★★ | ★★★ | 🔵 | 按需 |
| 28 | 外部 agent 包装 | 无头 CLI + ACP | ★★★ | ★★★ | ★★★★ | 🟡 | W 已做一半 |
| 29 | Auto mode 分类器 | Claude Code | ★★★ | ★★★ | ★ | 🔴 | 个人自用过重 |
| 30 | Agent Teams / A2A 协作网络 | Claude Code / A2A | ★★★★ | ★★★★★ | ★ | 🔴 | 收益低复杂度高 |
| 31 | 多租户沙箱集群 | Firecracker 池 | ★★ | ★★★★★ | ★ | 🔴 | 企业问题 |
| 32 | 插件包 / 技能市场 | plugin + marketplace | ★★ | ★★★★ | ★ | 🔴 | 自用不需要 |
| 33 | 云端调度（Routines） | Claude Code Routines | ★★ | ★★★★ | ★ | 🔴 | 违背本地优先 |
| 34 | 屏幕感知 / Computer Use | Cowork / Manus | ★★ | ★★★★★ | ★★ | 🔴 | 稳定性和隐私代价高 |
| 35 | 移动端 / 语音 | 各工作台 | ★ | ★★★★ | ★★ | 🔴 | 个人自用非必需 |

**统计**：🟢 11 项、🟡 10 项、🔵 7 项、🔴 7 项。
也就是说业界功能盘子里，**只有约三分之一值得自己写**，另有三分之一接入生态就行，三分之一不该碰。

---

## 5. 🟢 A 类详解：为什么这些值得自己写

### 5.1 分级上下文压缩（M6）—— 学习价值最高的一个

这是**唯一一个既高学习价值、又是各家真正差异化、还非有不可**的功能。

三级策略的顺序不是随便排的，是从便宜到贵：

```
1. 写入时截断      —— 工具输出太长时就直接截（成本零）
2. 清理旧 tool 结果 —— 把 N 轮以前的工具输出换成占位符（成本零，但能省最多）
3. LLM 摘要压缩    —— 真扛不住时才调模型总结（成本一次 LLM 调用）
```

**必须守住的约束**：不能拆开 `tool_call` 和它的结果。拆了 API 直接报错。所以压缩的原子单位是「一个 tool_call + 它的结果」，不是单条消息。

**为什么值得自研**：这条直接决定 SimpleAgent 能连续工作多久、每次花多少钱。而且压缩算法的取舍（保什么丢什么）是纯手艺，抄不明白，只能自己试。ROADMAP 里 M6 已经规划了，位置也对。

**额外收获**：顺手把「前缀缓存友好」做掉——system prompt 和工具列表保持稳定，别塞时间戳、别随机排序工具。这一条几乎不花成本，但对 DeepSeek 这类有 prompt cache 的 provider 能省一个量级的输入费用。

### 5.2 文件式分层记忆（M7）—— 个人工作台的灵魂

**这个功能的形态已经被行业验证了，而且验证结果很好：就用 markdown 文件。**

理由非常务实：
- git 天然给你版本历史，能 diff「昨天的 agent 知道什么」和今天，能把坏决策 git blame 回导致它的那条记忆
- 人可以直接读、直接改。agent 记错了，打开文件修正即可——换向量 embedding 你试试
- `grep` / `find` / 编辑器全都能用，零依赖、零数据库、零 schema 迁移
- 复制目录就是备份

**hot / cold 分层是这里面最有价值的一条经验**：hot 层（当前目标、活跃阻塞、近期决策、配置值）控制在 4KB 以内，**用 KV 结构而不是散文**；cold 层（历史决策、归档目标、详细流程）按需读。有实践者把 55KB 单文件拆成 3KB hot + 8KB cold，推理质量立刻改善——因为留出了思考空间。

**给 SimpleAgent 的具体建议**：
- `~/.simpleagent/memory/MEMORY.md`（索引，快照式，覆盖更新不是追加）+ `memory/YYYY-MM-DD.md`（日志，append-only）
- 记忆写入纪律要在 system prompt 里写死：用户说「记住」立刻写文件、做决策立刻记录**含理由**
- 语义检索**先不做**。用 grep 够用到几百条记忆。真需要了再上 BM25（约 50 行，比向量更好解释、更好调试）
- 注意**反馈环风险**：agent 写记忆 → 读记忆 → 放大自身偏差。乐观的总结会被越读越乐观，所以 `MEMORY.md` 要定期人工 review

### 5.3 Skills 渐进式披露（M7）—— 花小钱办大事

成本极低但收益极大，因为它解决的是「agent 上下文被塞满」这个根本矛盾。

机制：**只把 frontmatter 的描述常驻进 prompt，正文由 `load_skill` 按需加载**。这样你可以写 50 个技能，而 prompt 里只增加 50 行描述。

**关键提醒**：不要发明格式。`SKILL.md` 已经是开放标准（agentskills.io），Claude Code / Codex / Cursor / Gemini CLI / Cline / Roo / Goose / opencode 全都支持。对齐它有两个好处：一是省设计功夫，二是**你给 Claude Code 写的技能能直接放进 SimpleAgent 用，反之亦然**。这个杠杆非常大。

### 5.4 子 agent（M8）—— 上下文隔离是硬机制

「用隔离上下文换干净的主上下文」这件事，只有自己实现才能真正理解成本与收益。

典型用途：一次很长的代码审查、一次深调研、两个方案并行探索——这些都会在主上下文里留下大量中间产物，而主上下文只需要结论。

**实现要点**：
- 子 agent 要能收窄工具集（比如探索类只给只读工具，天然安全）
- 只把最终结论返回父会话，中间过程不外泄
- 事件要能标注来源（哪个子 agent 发的），否则前端没法展示

**为什么值得自研**：这是纯机制，没有标准可抄，而且和「事件可序列化」「工具受限」等已有约定是同构的，实现出来能加深对整个架构的理解。

### 5.5 定时 daemon + 心跳（M4）—— 个人自动化的入口

ROADMAP 把 M4 排在 M5–M8 之前，这个排序是对的：**M4 完成才是「每天能真正用上」的转折点**。

业界有一个值得抄的增强：**heartbeat 心跳**（OpenClaw 的做法）。除了纯 cron 触发，再定期唤醒 agent 让它读一份检查清单文件（`HEARTBEAT.md`），有事就行动、没事就静默返回。这比纯 cron 灵活——agent 自己判断今天要不要干活。

**和 M3 的衔接**：M4 需要「权限进配置文件」（M3 遗留的 TODO）。定时任务跑在白名单审批器下，需要能配「这个任务 bash 全部放行」这种规则。所以 M3 那两件事（权限等级进 config.toml、`--allow` 支持模式匹配）应该在 M4 之前补掉。

### 5.6 Hooks 生命周期（M8）—— 学一次管一辈子

这个功能的性价比被严重低估：**实现不复杂，但能学到「确定性自动化」和「模型自主」的边界在哪。**

Claude Code 验出来的设计很好抄：
- 事件列表覆盖全生命周期（会话开始、用户提交、工具调用前/后/失败后、批次结束、子 agent 起停、压缩前/后、会话结束、文件变化、目录变化……）
- 执行体要有多种类型：shell 命令（最常用）、HTTP、MCP tool、prompt（LLM 判一次）、agent（多轮验证）
- **PreToolUse 能改参数**、**PostToolUse 能改输出**，这才让 hook 从「观察者」变成「执行者」
- 结果合并规则：最严格的决定胜出（deny > defer > ask > allow），但**每个 hook 都跑完**（不能让一个 deny 抑制另一个的副作用）

对 SimpleAgent 的最小可用版：先做 `PreToolUse` + `PostToolUse` + `SessionStart` + `Stop` 四个事件，执行体只支持 shell 命令。

### 5.7 参数级权限规则（M3 补）—— 补一个明显的洞

现在权限是工具级的，跟行业比差一档。业界语法是 `Tool(param:value)`：
- `Bash(git push:*)` 单独放行
- `Agent(model:opus)` 直接禁掉贵模型
- `read_file(path:/etc/*)` 拦住敏感路径

对个人自用特别实用：**「git 命令全放行，但 rm 要走审批」**这种规则，现在写不出来。

### 5.8 Plan mode —— 成本最低、收益很直观的一个

不要把它想复杂。本质就是：
1. 进 plan 阶段，工具集收窄到只读
2. 强制模型先输出完整计划
3. 用户确认后放开写工具，按计划执行

**零新机制**，纯 prompt + 工具子集 + 一个状态位。但对「让 agent 干一件稍大的事」的体验提升非常明显，因为它把「改错了才发现」提前成「计划里就能看出来」。

---

## 6. 🔵 C 类：别自己造的七个东西

这一节的目的是**省时间**。下面每一个，自己写都是净亏损。

| 功能 | 为什么别自研 | 正确做法 |
|---|---|---|
| **MCP 远程 / OAuth / elicitation / tasks** | Streamable HTTP 传输、OAuth 2.1 动态客户端注册、反向取样、长任务状态机——这是一整套协议工程，而且规范还在动（SEP-1442 要把有状态会话改成无状态） | stdio 可以手写（学习价值高，M5 规划正确）；远程一律用官方 `mcp` SDK |
| **浏览器自动化** | 浏览器驱动是深坑，且 MCP 生态已经有 Playwright / Chrome DevTools 两类成熟 server | 需要时接 Playwright MCP |
| **IM 平台适配** | 每家平台的鉴权、限流、消息格式、媒体上传都不一样，是纯体力活 | 用 webhook（M4 已规划飞书/Telegram）+ 现成 MCP |
| **可观测看板** | trace 采集是核心，展示层是商品。而本地 trace 全量落盘已经做了（这是最关键的部分） | 需要时导出 OTLP，接免费的 Aspire Dashboard 或自托管 Langfuse |
| **沙箱运行时** | Firecracker / gVisor 这一层不该自己搭。个人自用还有一个更简单的答案：**默认拒网的临时容器**就能覆盖约 95% 场景 | 先靠已有的工作目录边界 + 危险命令识别；真要隔离就加一个可选 Docker 开关 |
| **模型路由** | 已经是 OpenAI 兼容协议的赢家通吃局面，profile + `base_url` 就够了 | 保持现状，别做 router |
| **插件分发 / 技能市场** | 自用没有分发需求 | 不做 |

---

## 7. 🔴 D 类：明确不做的七个方向

这些在业界是重要功能，但对 SimpleAgent **要么收益为零、要么性价比极差**。

1. **Auto mode 分类器**（用模型判断每个动作安不安全）
   企业场景要「无人值守但别出事」，所以愿意为分类器付延迟和成本。个人自用的正确做法是**白名单 + 定时任务只给只读或固定命令**，比分类器更可预测、更好调试。

2. **Agent Teams / A2A 协作网络**
   多 agent 互相发消息、共享任务列表、协调分工——这是为企业级复杂度设计的。个人场景里，「子 agent（M8）」的隔离收益已经吃到了，再加一层协作只会让调试变难。

3. **多租户沙箱集群**
   Firecracker 池、出口网关、DLP 标记、参数异常评分——全是「服务陌生人」的问题。单用户本地工具完全不需要。

4. **云端调度（Routines 跑在厂商基础设施）**
   M4 的本地 `croniter` + launchd 已经解决同一个问题，而且**数据不出本机**——这恰恰是 SimpleAgent 相对业界产品最大的优势，不该放弃。

5. **插件包 / 技能市场**
   自用不存在「跨仓库复用」和「分发」需求。等真的需要了，`SKILL.md` 已经是开放标准，直接放进 `~/.simpleagent/skills/` 即可，不用打包。

6. **屏幕感知 / Computer Use**
   截图 + 元素识别 + 模拟操作，稳定性差、隐私代价高、调试困难，而且是**最容易被提示注入攻击**的入口（屏幕上任何内容都是输入）。真需要 GUI 操作时，用 Playwright MCP 做浏览器内的自动化更可控。

7. **移动端 / 语音**
   个人自用非必需。真要「手机上触发」，M4 的 IM webhook 通知通道反向使用即可——发条消息给 bot，bot 拉起一个任务。

---

## 8. 差异化定位建议

把上面的分析收拢成一句话：

> **SimpleAgent 不该和 OpenClaw / WorkBuddy 比「接了多少平台」，而应该比「agent 的内部机制有多可读、多可改」。**

业界个人工作台的竞争维度是「开箱即用 + 平台覆盖 + 云端能力」，这三项 SimpleAgent 全都不占优也不该占。但它有两项是那些产品**结构上不可能提供**的：

| 优势 | 业界为什么给不了 |
|---|---|
| **每个机制都能读、能改** | 闭源产品（Cowork / WorkBuddy / QoderWork / Manus）根本不给源码；开源产品（OpenClaw）体量大、要维护多平台兼容，改一行要理解整个抽象层 |
| **零妥协的本地优先** | 厂商路线图天然倾向云端调度、云端记忆、账号体系——那才是它们的商业模式 |

**具体做法**：把自己写过的每一层机制都留一个开关（ARCHITECTURE 里「新 feature 用配置开关接入」这条原则正是为此）、每次实验结论写进 `docs/notes/`、M9 的 evals 用来做 A/B 对比。**这套「可实验性」就是产品本身。**

**OpenClaw 值得当参照系而不是对手**：它 MIT 开源、可读，而且把「个人 AI 工作台」的功能盘子铺得最全（多平台网关、文件式记忆、SKILL.md、cron + heartbeat、tool policy 审批）。当你想知道某个功能业界怎么做的，去读它的实现，比读博客有用得多。事实上这个工作区里的 `SOUL.md` / `IDENTITY.md` / `USER.md` 就是它那套身份文件格式——方向已经很清楚了。

---

## 9. 行动清单

按「现在该动什么」排序：

**立刻补（挡着 M4）**
1. M3 遗留：权限等级进 `config.toml`（`schedules.toml` 要引用它）
2. M3 遗留：`--allow` 支持模式匹配 → 顺势升级为 `Tool(param:value)` 语法

**下一步（M4，收益最高）**
3. `sa daemon` + `schedules.toml` + 通知路由
4. 加 **heartbeat** 而不只是 cron——agent 自己判断今天要不要干活

**然后按路线图**
5. M5：MCP stdio 手写（远程留给 SDK）
6. M6：三级压缩 + 前缀缓存友好（**最高学习价值**）
7. M7：文件式分层记忆 + `SKILL.md` 对齐（**别发明格式**）
8. M8：子 agent + 四个核心 hooks

**随时可以捡的轻量项**
9. Plan mode（一天的工作量，体验提升明显）
10. `/context` 上下文可视化
11. 循环检测

**明确不做**：第 7 节列的全部七项。

---

## 附：本报告的可靠性分级

| 结论 | 置信度 | 依据 |
|---|---|---|
| 四条标准收敛（AGENTS.md / SKILL.md / MCP / A2A） | 高 | 多个独立来源一致，且有官方治理架构佐证 |
| MCP 比 CLI 贵 7–32× token | 中高 | 单一 benchmark（Scalekit），但方向与直觉一致，量级可信 |
| Claude Code hooks 事件清单 | 高 | 官方文档原文 |
| 文件式记忆 + hot/cold 分层的收益 | 中 | 社区实践报告，非受控实验，但多个来源相互印证 |
| 「默认拒网容器覆盖 95% 个人场景」 | 中 | 自托管沙箱指南的观点，非量化结论 |
| 具体产品版本号 / 价格 / star 数 / benchmark | 低 | 二手 SEO 站点为主，变化极快，仅作量级参考 |
| 桌面客户端「并行面板 + 定时结果回流侧边栏」的形态 | 中高 | 官方博客转述 + 产品页 |
