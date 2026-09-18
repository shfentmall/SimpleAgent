# README 按「怎么用 sa」重写，补 v0.1.0 发布日志

- 日期：2026-09-18
- 对比基线：`4ea1b1d`（Merge pull request #6 from shfentmall/claude/task-final-status-info-9691f7）
- 对应里程碑：无（文档 / 发布整理，不新增功能；覆盖的是已完成的 M0–M3 + 工作台 W1–W5，外加 M8「外部 agent」提前做掉的那一半）

## 功能变化

- 升级：`README.md` 整体重写。开头保留项目最初的定位（自用本地 agent，学习 + 解决真实问题），
  注明当前版本 0.1；新增「能做什么」概览（终端对话、工具调用、权限、无人值守、浏览器工作台、
  调度外部 agent、给 sa 发消息七条）。
- 升级：新增「第一次使用」四行命令（`sa init` → 写 `.env` → `chmod 600` → `sa`），比原来分散在
  「快速开始」里的写法更直接。
- 升级：原来一段「快速开始」拆成三节——**终端对话**（`/model` `/tools` `/usage` 等 REPL 命令、
  多行输入 `"""`、`max_steps` 和工具输出截断说明）、**浏览器工作台**（`sa serve`，空间、执行者、
  右栏四个 tab、验证状态的「已失效」降级、控制面板）、**无人值守**（`sa run`、`--allow`、
  `sa inbox push` 反向投消息）。
- 升级：「权限」一节补充工作台里的审批卡交互（允许 / 拒绝 / 本次会话始终允许），以及
  `bash` 工具启动子进程时会把 API key 从环境变量里摘掉这一点（原来只在代码里做了，没写进文档）。
- 新增：「配置和数据」一节，给出 `config.toml` 主要配置项表（`default_profile`、`show_reasoning`、
  `max_steps`、`system_prompt`、`[trace]`、`[tool_output]`、`[panel]`、`[profiles.*]`）和
  `~/.simpleagent/` 目录结构说明（`sessions/` `spaces/` `panel/` `tool_outputs/` `traces/`）。
- 升级：「文档」一节补上「发布日志」「变更记录」入口，链接到新增的 `docs/releases/` 和
  已有的 `docs/changelog/`；「开发」一节补上 `uv sync` 和指向 `AGENTS.md` 协作约定的链接。
- 新增：`docs/releases/v0.1.0.md`。0.1 版发布日志，按能力分组列出这一版交付了什么
  （终端对话、工具调用和 agent loop、权限、会话和无人值守、浏览器工作台、调度外部 agent、
  给 sa 发消息），附安装步骤、「已知限制」（还没有定时任务、权限规则没进配置文件、
  外部 CLI 没有实时审批、Claude Code 适配只实测过失败路径、控制面板「近期完成」还没接前端等）、
  下一步指向 M4，以及质量小结（314 个离线测试，`ruff check` / `ruff format` 干净）。
- 新增：`docs/send-message.md`（脚本 / 定时任务往控制面板投消息的完整说明，`sa inbox push` 和
  `POST /api/inbox` 两条路的参数、正文给法、字段约定）和 `docs/research/` 下两篇调研笔记
  （`2026-09-18-industry-agent-features.md` 业界 agent 功能调研、
  `2026-09-18-opencode-contributing-guide.md` OpenCode 贡献指南）。这三份是别的会话在此之前
  写好的，本次提交把它们一并纳入版本库，内容本身不属于这次改动。
- 配置：`.gitignore` 新增 `.DS_Store`，忽略 macOS 的目录元数据文件。

## 函数级改动

`src/` 下没有改动，只涉及文档和配置文件。

## 配置与依赖

无变化，用户无需手动处理。

## 测试

本次只改文档和配置，没有新跑 `pytest`（对应的代码状态仍是上一次推送 `4ea1b1d`，
那次的结果是 314 passed）。提交前跑过 `uv run ruff format --check`（106 files already
formatted，无需改动）和 `uv run ruff check`（All checks passed）。

## 相关笔记

无

## 备注

这次推送之后计划打 `v0.1.0` tag 并建 GitHub Release，对应 `docs/releases/v0.1.0.md` 的内容。
