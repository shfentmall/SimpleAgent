# 变更记录

每次推送到 GitHub 前，写一份本次推送相对上次推送的变更记录：升级了哪些功能、改了哪些函数。

## 规则

- **时机**：推送前写好，和代码放在同一批提交里一起推送
- **对比基线**：上次推送到 GitHub 的提交（`origin/main`），覆盖这之间的所有本地提交
- **文件名**：`YYYY-MM-DD-<简短英文描述>.md`，比如 `2026-09-17-m0-m1-streaming-chat.md`
- **函数级改动**：`src/` 下的代码列到函数/类级别；测试只列到文件级别
- **新记录加到下面索引的最上面**

## 模板

```markdown
# <一句话概括本次推送>

- 日期：YYYY-MM-DD
- 对比基线：`<hash>`（<那次提交的标题>）
- 对应里程碑：M?

## 功能变化

- 新增：……
- 升级：……
- 修复：……

## 函数级改动

### `src/simpleagent/<文件>.py`

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `Foo.bar()` | 新增 / 修改 / 删除 | 做了什么、为什么 |

## 配置与依赖

- 配置项、依赖、数据目录的变化
- 需要手动处理的地方（比如要改 `~/.simpleagent/config.toml`）

## 测试

- 新增或修改的测试文件
- 测试结果：N passed

## 相关笔记

- `docs/notes/` 里对应的学习笔记（没有就省略）
```

## 索引

- [2026-09-17 客户端 UI 设计文档去除本机用户名](2026-09-17-client-ui-doc-redact-username.md)
- [2026-09-17 工作台 W1 + W2：Space/会话持久化 + 本地 HTTP + SSE API](2026-09-17-workbench-spaces-and-local-api.md)
- [2026-09-17 M2 完成：其余 6 个内置工具 + 输出截断落盘 + 只读并行/含写串行](2026-09-17-m2-tools-truncation-and-fixes.md)
- [2026-09-17 M2 第一段：工具抽象 + list_dir + 带工具调用的 Agent loop](2026-09-17-m2-tool-calling-list-dir.md)
- [2026-09-17 新增 changelog-writer 子 agent，用于推送前写变更记录](2026-09-17-changelog-writer-agent.md)
- [2026-09-17 M0 + M1：项目脚手架与流式对话 REPL](2026-09-17-m0-m1-streaming-chat.md)
