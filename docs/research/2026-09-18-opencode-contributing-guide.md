# 如何参与 opencode 贡献（实战指南）

> 调研时间：2026-09-18。仓库：`anomalyco/opencode`（原 `sst/opencode`，已改名重定向；默认分支 **`dev`**）。
> 本文所有数据来自 GitHub API / `gh` CLI 实测 + 仓库内 `.github/` 实际配置，不是通用建议。
>
> **先给结论**：opencode 的贡献通道是敞开的，门没有关——但**门槛不在技术，在「小而准」和「别踩自动关闭闸门」**。最近 400 个被合并的 PR 里，外部贡献者只占 3.2%。这不是不可能，而是必须按它的规则来。

---

## 1. 现实数据：先知道自己面对什么

| 指标 | 数值 |
|---|---|
| 团队成员（`.github/TEAM_MEMBERS`） | **23 人** |
| 当前 open PR | **约 1,475 个** |
| 最近 400 个已合并 PR 中的外部真人 | **13 个，占 3.2%** |
| 最近 100 个合并 PR 的时间跨度 | **3 天**（约 33 个/天，合并吞吐极高） |
| 合并总数 / 关闭总数（近 100 个已关闭） | 38 / 62 |
| `good first issue` 标签的开放 issue | **0 个** |
| `help-wanted` 标签的开放 issue | **0 个** |
| `documentation` / `docs` 标签的开放 issue | **0 个** |
| 有内容的标签 | `bug` 41 个、`enhancement` 12 个、`discussion` 6 个 |

**两个关键推论**：

1. **没有「新手友好 issue」这条路。** 这三个标签虽然存在，但目前一个开放 issue 都没有。CONTRIBUTING 里引导新人去搜这几个标签，实际是空的——必须自己找入口。
2. **维护者合并速度极快，但积压更严重。** 3 天合并 100 个，同时还有 1,475 个 open PR。说明**不是没人处理，是提交量远大于处理量**，而且有自动清理在淘汰长尾。你的 PR 必须让人一眼看出价值。

---

## 2. 成功模板：解剖 13 个被合并的外部 PR

这是全文最有价值的部分——**这 13 个就是被验证过能通过的路子**。

| PR | 作者 | 规模 | 标题 |
|---|---|---|---|
| #48452 | @dajiaohuang | 1 文件 +1/-1 | `docs: fix v2 plugin system example` |
| #49122 | @ysm-dev | 1 文件 +1/-1 | `fix(app): derive directory names from listed paths` |
| #48952 | @OpeOginni | 1 文件 +45/-10 | `fix(tui): preserve form drafts across tabs` |
| #48928 | @0xhckr | 1 文件 +5/-1 | `fix(nix): enable Wayland clipboard images` |
| #48710 | @R-Taneja | 3 文件 +61/-13 | `chore(deps): bump @ai-sdk/gateway to 3.0.191` |
| #48662 | @ReStranger | 2 文件 +10/-4 | `fix(nix): install opencode and keep opencode2 alias` |
| #48689 | @dukebw | 4 文件 +45/-14 | `fix(tui): include reasoning tokens in throughput` |
| #47792 | @vglafirov | 3 文件 +5/-5 | `chore: bump gitlab-ai-provider to 6.15.0` |
| #48798 | @OpeOginni | 4 文件 +13/-3 | `feat(tui): add inline home footer slot` |
| #48249 | @Tarquinen | 2 文件 +82/-78 | `fix(ai): retain streamed output for empty completion checkpoints` |
| #48491 | @heimoshuiyu | 3 文件 +86/-1 | `feat(console): add batch workspace block endpoints` |
| #49183 | @dbpolito | 3 文件 +97/-68 | `fix(core): retry acknowledged websocket read failures` |
| #48225 | @JacobNWolf | 7 文件 +495/-46 | `fix(acp): restore session options and reasoning boundaries` |

**从这 13 个能读出的规律：**

1. **规模几乎都在 1–4 个文件、百行以内。** 唯一的例外（7 文件 +495）是 ACP 协议层的系统性修复，属于资深领域。**别做大 PR。**
2. **标题 100% 遵循 `type(scope): summary`。** 没有例外。
3. **有明确的高命中领域**：
   - **`nix`（2 个）**——团队不跑 Nix，这块基本是外部人的地盘，是被人忽视的洼地
   - **`tui`（3 个）**——终端 UI 的小毛病，好定位好验证
   - **`docs`（1 个）**——门槛最低，1 文件 +1/-1 就能合
   - **`chore(deps)`（2 个）**——依赖升级，纯机械但有价值
4. **`@OpeOginni` 一个人贡献了 2 个**，说明不是「一次性运气」，是可以连续输出的。

---

## 3. 五步上手流程

### 第 0 步：环境准备

```bash
# 你的机器上已有 bun 1.3.11，满足要求（需 Bun 1.3+）
bun --version

# fork + 克隆（gh 已登录 shfentmall，有 repo scope，可直接 fork）
gh repo fork anomalyco/opencode --clone --remote
cd opencode

# 关键：默认分支是 dev，不是 main
git checkout dev
git pull upstream dev

bun install
```

### 第 1 步：跑起来，先当用户

```bash
bun dev                    # 在 packages/opencode 目录下跑
bun dev .                  # 在仓库根目录跑
bun dev --help             # 看全部命令
bun dev serve              # 起无头 API server（默认 4096 端口）
bun run --cwd packages/app dev      # 单独跑 Web UI（需先起 server）
bun run --cwd packages/desktop dev  # 跑桌面端
```

**先当用户用几天。** 这一步不能跳——CONTRIBUTING 明说「在加新功能前，先确认代码库里没有别处已经实现了」。你要改的东西，得先知道你天天在用它。

### 第 2 步：找一个真实的 bug

因为 `good first issue` 是空的，入口只有两个：

**路线 A：从自己的痛点出发（推荐）**
你在第 1 步里遇到任何咯手的地方，就是最好的候选。你自己的痛点 = 你能写清楚复现步骤 = 维护者最容易接受。

**路线 B：从 `bug` 标签的 41 个开放 issue 里挑**
当前几个可读性较好的（都是技术性 bug，不是配置问题）：

| Issue | 标题 |
|---|---|
| #47514 | desktop: refocus prompt input when switching tabs with Cmd+number |
| #41454 | tui: attached image path is omitted from model context |
| #39736 | tui: undo fails for admitted message after interrupt |
| #37372 | v2: empty reasoning-only response is recorded as successful completion |
| #36940 | tui: background hint appears when child work is already backgrounded |

> ⚠️ **避开当前的热点雷区**：现在有一大堆重复 issue 在刷屏「free tier can only be used from within OpenCode」（#49585–#49639 之间有十几个）。维护者正在处理这个洪峰，别往里挤。

#47514 是个好例子——已经有人（@argszero）在下面留言认领，并且**把代码路径都追出来了**（指出 `packages/app/src/session/composer/region.tsx` 的 `focusInput`、`packages/app/src/shell/` 的 tab 切换逻辑），说明这种做法是有效的、被接受的。

### 第 3 步：先开 issue，再写代码（**强制**）

这是 opencode 的 **Issue First Policy**，写在 CONTRIBUTING 里：

> **All PRs must reference an existing issue.** PRs without a linked issue may be closed without review.

流程：

1. **用模板开 issue**。空白 issue 被禁用了（`blank_issues_enabled: false`），只有三个模板：`bug-report.yml` / `feature-request.yml` / question（走 Discord）。
2. **issue 要短**。写清：发生了什么、怎么复现、为什么重要、你打算自己修。
3. **绝对不要贴 AI 生成的长文**。CONTRIBUTING 有专门章节 "No AI-Generated Walls of Text"，bug 模板的描述字段里直接写着 "avoid pasting giant AI generated summaries or your issue may be closed/ignored"。
4. **在 issue 里留言认领**，等维护者分配或给绿灯。
5. 如果是**新功能**，必须先做 design conversation，等核心团队批准（"please wait for that approval instead of opening a feature PR directly"）。

### 第 4 步：写代码

```bash
# 分支名：最多三个词，连字符分隔
# ❌ 不要用 feat/ fix/ 这类前缀，也不要斜杠
git checkout -b fix-scroll-state

# ...改代码...

# 提交前必须全过（CI 跑的就是这些）
bun turbo test                # CI 用 GITHUB_ACTIONS=false bun turbo test
bun run check:generated       # 检查生成的 client 是否同步
bun run test:httpapi          # HttpApi 闸门

# 如果改了 API 或 SDK（如 packages/opencode/src/server/server.ts）
bun run generate              # 重新生成 SDK，不要手改 src/generated
```

**代码风格要点**（来自 AGENTS.md，比多数项目严格）：

- 避免 `else`，用 early return
- 尽量不用 `try`/`catch`，优先 `.catch(...)`
- 不用 `any`，靠类型推断
- 优先 `const` 而不是 `let`
- **不做不必要的解构**，用点号保留上下文（`obj.a` 而不是 `const { a } = obj`）
- **导入不改名、不用 `* as`**（`import { resolve as pathResolve }` 是违规的）
- 用 Bun 原生 API（如 `Bun.file()`）
- **不要提前抽只调用一次的 helper**，就地在调用点写
- 只用一个地方的值不要先赋给变量再传

### 第 5 步：提 PR

**PR 标题**（会被自动化检查）：

```
fix(tui): simplify thinking toggle styling
docs: update contributing guide
chore(sdk): regenerate types
```

合法 type：`feat` / `fix` / `docs` / `chore` / `refactor` / `test`
scope 可选：`core` / `opencode` / `tui` / `app` / `desktop` / `sdk` / `plugin`

**PR 描述**：

- 用 `Fixes #123` 或 `Closes #123` 关联 issue（**必须**）
- 保持小、聚焦
- **UI 改动必须附前后对比截图或视频**
- **逻辑改动必须说明你怎么验证的**：测了什么、reviewer 怎么复现
- 用自己的话短说，**不要 AI 生成的长文**

---

## 4. 五条会被自动关闭的雷（必读）

仓库里有多个自动化 workflow 在盯着。踩了就是白干：

### 雷 1：PR 超过 1 个月且攒不到 2 个正面 reaction → 自动关闭

`.github/workflows/close-prs.yml` 每天 22:00 UTC 跑，脚本是 `script/github/close-prs.ts`：

```
--threshold 2 --age-months 1
```

- **判定标准**：PR 创建超过 1 个月，且**少于 2 个正面 reaction**
- **正面 reaction 只算这四种**：👍 THUMBS_UP、❤️ HEART、🎉 HOORAY、🚀 ROCKET
  （注意：**评论不算**，reaction 才有效；而且必须是加在 PR 本身上的）
- **推论**：提完 PR 不能干等。要在 Discord 里问、在相关 issue 里提一句、让真实用户看到并给你 reaction。这是 opencode 特有的机制——它用「社区认可度」而不是「维护者是否回复」来决定一个 PR 的去留。

### 雷 2：`needs:compliance` 标签 2 小时内不修就关

`.github/workflows/compliance-close.yml` 每 30 分钟跑一次。被自动 checker 打上 `needs:compliance` 的 issue/PR，**只有 2 小时**修正窗口（模板没填全、留了占位符、疑似 AI 长文等）。团队/org 成员豁免，你没有豁免。

### 雷 3：没有关联 issue 的 PR 可能不经 review 直接关

见第 3 步。Issue First Policy 是硬规则。

### 雷 4：新 PR 会被 LLM 自动查重

`.github/workflows/pr-management.yml` 在**每个新外部 PR** 上运行——用 opencode 自己（`bun script/duplicate-pr.ts`）检查是否与已有 PR 重复，重复就自动评论提示。所以提 PR 前先搜一遍有没有人已经做了。

### 雷 5：issue 没用模板 → 自动 checker 会 flag

`.github/workflows/duplicate-issues.yml` + 自动合规检查。空白 issue 被禁用，占位符文本会被识别。

---

## 5. 难度排序：从哪开始最划算

按「被合并的概率 × 上手成本」排序：

| 优先级 | 方向 | 为什么 | 参考 |
|---|---|---|---|
| ⭐⭐⭐ | **`docs:` 文档修复** | 门槛最低，1 文件 +1/-1 就能合，已被验证 | #48452 |
| ⭐⭐⭐ | **`nix` 相关修复** | 团队不跑 Nix，是外部人的洼地，最近合了 2 个 | #48928 #48662 |
| ⭐⭐ | **`chore(deps): bump xxx`** | 机械但有价值，最近合了 2 个 | #48710 #47792 |
| ⭐⭐ | **`fix(tui):` 小毛病** | 定位明确、易验证，最近合了 3 个 | #48952 #48689 |
| ⭐⭐ | **新增 provider** | **不用改 opencode 代码**——CONTRIBUTING 说去 `anomalyco/models.dev` 提 PR | — |
| ⭐ | **`fix(app)` / `fix(desktop)` UI 细节** | 需要附截图，但改动小 | #49122 |
| ⚠️ | **`core` / `acp` / `ai` 深层修复** | 难度高，但价值也高，适合有对应经验的领域 | #49183 #48225 |

**最实际的建议**：先用几天 opencode，把遇到的第一个小毛病修掉。**不要一上来就挑核心 bug。**

---

## 6. 你的账号准备好了吗

实测本机状态：

| 项 | 状态 |
|---|---|
| `gh` CLI | ✅ v2.101.0 已安装 |
| `gh` 登录账号 | ✅ `shfentmall`（有 `repo` scope） |
| `bun` | ✅ 1.3.11（要求 1.3+） |
| `git` | ✅ 2.39.5 |
| SSH | ✅ 已配（`shfentmall` 的 key） |

**结论**：可以直接 `gh repo fork anomalyco/opencode --clone`，然后推分支、开 PR，全程不需要额外配凭据。

---

## 7. 社区入口

- **Discord**：https://discord.gg/opencode（README 明说：支持、疑难、how-to、实时讨论都来这里）
  - 这是最关键的渠道——PR 攒 reaction、找认领机会、问方向，都在这里
- **X**：@opencode
- **仓库**：https://github.com/anomalyco/opencode
- **注意**：`sst/opencode` 这个老地址会重定向到 `anomalyco/opencode`，旧的教程链接别照抄

---

## 附：一页速查

```bash
# 准备
gh repo fork anomalyco/opencode --clone --remote && cd opencode
git checkout dev && bun install

# 开发
bun dev                    # 跑起来
git checkout -b fix-something-here   # 分支名最多三词、无前缀

# 提交前必过
bun turbo test
bun run check:generated
bun run test:httpapi
bun run generate           # 仅在改了 API/SDK 时

# 提 PR 前自检
# □ 有对应的 issue，且 PR 描述里写了 Fixes #xxx
# □ 标题是 type(scope): summary
# □ 规模小（1-4 文件、百行内）
# □ UI 改动附了截图；逻辑改动写了怎么验证
# □ 描述是我自己写的短话，不是 AI 长文
# □ 搜过了，没有重复 PR
# □ 提完后去 Discord 让人看到，争取 2 个 reaction（1 个月内）
```

**最大的心智转变**：在 opencode 提 PR，技术难度不是主要障碍，**「小、准、按规矩」才是**。它的自动化流水线会淘汰掉大部分不合规的提交，剩下的才会进入人的视野。三个外部人用 +1/-1 的文档改动和 Nix 修复就进了贡献者列表——这就是可复制的路径。
