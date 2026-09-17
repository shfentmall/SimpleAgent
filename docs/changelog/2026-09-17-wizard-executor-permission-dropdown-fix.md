# 修复新建空间向导的「执行者」「权限」下拉

- 日期：2026-09-17
- 对比基线：`9296311`（Add changelog for CLI executors and panel merge）
- 对应里程碑：工作台（M8 提前做掉的外部执行者部分的修复）

## 功能变化

- 修复：新建空间向导里「执行者」下拉从来没被填过。前端拿到了 `/api/meta` 的 `executors`，
  但没有任何地方把它塞进 `<select id="f-executor">`，所以下拉一直是空的；「权限」下拉依赖
  选中的执行者，也跟着一直是空的。
- 修复：选好的权限档会被立刻弹回默认值。原来每次联动（改形态、改权限本身）都会重建权限选项并
  重置成 `default_permission`，所以刚选的「全放行」会被换回「只读」。

## 函数级改动

### `src/simpleagent/web/app.js`

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `fillExecutorOptions()` | 新增 | 用 `state.executors` 填「执行者」下拉，启动时调用一次；之后再打开向导不重填，保留上次的选择 |
| `fillExecutorDependents()` | 新增 | 按当前执行者重建「模型」「权限」两个下拉的选项并设默认值，只在执行者变了时调用 |
| `syncModalFields()` | 修改 | 只切字段显隐和文案，不再动下拉选项；权限警告只在外部执行者且选了 `full` 时显示 |
| `boot()` | 修改 | 启动时调用 `fillExecutorOptions()`；执行者 `onchange` 先 `fillExecutorDependents()` 再 `syncModalFields()` |

## 配置与依赖

- 无变化，不需要手动处理。

## 测试

- 没有新增测试（前端 JS 没有测试框架）。
- `uv run pytest -q`：297 passed；`uv run ruff check`、`uv run ruff format --check` 通过。
- 手动验证：用临时 `SIMPLEAGENT_HOME` 起 `sa serve`，在浏览器里检查执行者下拉有三项；选
  claude-code 后权限默认「只读」，改「全放行」后值保持、警告出现，再切任务形态值也不变；切回内置
  SimpleAgent 后权限栏隐藏、模型回到默认 profile。
