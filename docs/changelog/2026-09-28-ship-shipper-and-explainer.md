# /ship 改由子 agent shipper 在干净上下文里跑，原理图拆成独立的 /explainer

- 日期：2026-09-28
- 对比基线：`4d20151`（版本号 0.2.7）
- 对应里程碑：无（开发流程）

## 功能变化

- 升级：`/ship` 主会话只写一份交接简报（改了什么、开发时验证过什么、还没验证什么、用户的补充说明），
  上线步骤交给新增的子 agent `shipper` 在空上下文里跑；遇到要停下来问的情况，由主会话转告用户，
  再把决定发回同一个 shipper。
  - 原因：统计了历次 5 次 /ship，主会话上下文 10 万～70 万 token，每个调用都要重读，
    连一次 `git status` 都要几万 token；闲置超过 1 小时再敲 /ship，还要把整段上下文重新写进缓存（占 19%）。
- 升级：手动验证改成按需做。开发时已验证、自动检查通过、main 的改动不重叠就跳过；
  要做就先列清单，用 `browser_batch` 一次跑完一整段，读结果优先读页面文字，截图只在看布局时用。
  环境问题试两次不行就停，写进汇报。
- 升级：变更记录由 shipper 自己写，不再另派 `changelog-writer`。
- 修复：在 worktree 里 `git switch main` 会失败，第 9 步改用 `git -C <主目录> pull`。
- 新增：`/explainer <变更记录路径或功能名>`，给新功能出一套给产品经理看的原理图（Artifact 网页 + PNG）。
  在后台子 agent 里跑（`context: fork`），只在用户敲命令时运行。以前是主会话在 /ship 后顺手画，
  占了 /ship 总 token 的 17%。
  - 附带样板网页 `template.html`（2026-09-24「指挥台调度」那页）和截图脚本 `shot.py`。
  - `shot.py` 修掉了无头 Chrome 写完截图却不退出、一直卡到超时的问题：文件大小稳定后直接结束整个进程组。

## 函数级改动

`src/` 下没有改动。

| 文件 | 变化 | 说明 |
|---|---|---|
| `.claude/skills/ship/SKILL.md` | 重写 | 只剩写交接简报、派出 shipper、转告结果；加 `argument-hint` |
| `.claude/agents/shipper.md` | 新增 | 原 /ship 的 9 步，第 4 步改成按需 + 批量验证，第 5 步自己写变更记录，汇报格式加第一行状态 |
| `.claude/skills/explainer/SKILL.md` | 新增 | 原理图流程：读变更记录和代码 → 照模板换内容 → 截图自查 → 发布 Artifact、发 PNG |
| `.claude/skills/explainer/template.html` | 新增 | 样板网页，保留整段样式和组件 class |
| `.claude/skills/explainer/shot.py` | 新增 | `main()` / `shot()` / `chrome_shot()`：包成浅色主题的本地页面，`?only=<分节id>` 截单节，按背景色裁边留 48px，没截全时加高窗口重截 |
| `AGENTS.md` | 修改 | 2.3 节说明 /ship 由 shipper 执行 |

## 配置与依赖

- 没有新增项目依赖。`shot.py` 用 `uv run --with pillow` 临时带上 Pillow，需要本机装了 Google Chrome
  （路径可用环境变量 `CHROME` 覆盖）。
- 新的 `shipper` 子 agent 和 `/explainer` 要新开 Claude Code 会话才会被识别。

## 测试

- 没有改测试。`uv run ruff format --check`、`uv run ruff check` 通过；`uv run pytest -q`：872 passed。
- 手动验证：用 `shot.py` 对 `template.html` 截了全页 + 3 个分节，4 张图共 12 秒，效果和 2026-09-24 的原图一致，没有残留的 Chrome 进程。
- 没有验证：还没真的跑过一次新的 `/ship` 和 `/explainer`；后台子 agent 能不能直接发布 Artifact、发 PNG
  也没试（SKILL.md 里写了兜底：发不了就在汇报里列路径）。
