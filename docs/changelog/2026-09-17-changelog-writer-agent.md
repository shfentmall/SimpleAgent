# 新增 changelog-writer 子 agent，用于推送前写变更记录

- 日期：2026-09-17
- 对比基线：`06ee50b`（Add project scaffold and streaming chat REPL (M0 + M1)）
- 对应里程碑：无（协作流程工具，不对应具体里程碑）

## 功能变化

- 新增：`.claude/agents/changelog-writer.md`，Claude Code 子 agent 定义。职责是推送前对比 `origin/main`，在 `docs/changelog/` 按模板写变更记录（功能变化、`src/` 函数级改动、配置与依赖、测试结果）；只允许用 Bash/Read/Write/Edit，不执行 `git add/commit/push`，不改 `src/`、`tests/` 下的代码，不读取 `~/.simpleagent/.env` 或把 key 抄进记录
- 文档：`AGENTS.md` 2.2 节补充一行，说明在 Claude Code 里用该子 agent 写变更记录，主会话检查后再提交推送

## 函数级改动

无（本次未改动 `src/`）。

## 配置与依赖

- 无新增依赖、无配置项变化
- **需要手动处理**：无

## 测试

- 未新增或修改测试文件
- 测试结果：33 passed；`ruff check`、`ruff format --check` 通过（用于确认本次改动未影响现有代码，本次实际只涉及 `AGENTS.md` 和 `.claude/agents/changelog-writer.md`）

## 相关笔记

无
