# 左栏空间可折叠，会话行更紧凑

- 日期：2026-09-28
- 对比基线：`264d69f`（版本号 0.2.4）
- 对应里程碑：客户端小改进

## 功能变化

- 新增：客户端左栏「打开的空间」里每个空间可以折叠——点空间标题行切换，左侧 ▾ / ▸ 显示状态；折叠后
  隐藏工作目录和会话列表。折叠状态存在浏览器 localStorage（`sa.workbench.collapsed`），刷新后保持。
  搜索框有内容时所有空间临时展开，免得匹配的会话被藏住。
- 升级：会话行更紧凑——行内上下边距 4px → 2px、行高 1.35，行间隔 1px → 0，列表与空间标题的距离
  6px → 4px。

## 函数级改动

### `src/simpleagent/web/app.js`

| 函数 / 常量 | 变化 | 说明 |
|---|---|---|
| `LS_COLLAPSED` | 新增 | 折叠空间 id 的 localStorage 键 |
| `state.collapsed` | 新增 | 折叠的空间 id 集合，启动时从 localStorage 读 |
| `renderSpaces()` | 修改 | 标题行加折叠箭头和点击切换；折叠的卡片加 `is-collapsed`；有搜索词时不折叠 |

### `src/simpleagent/web/styles.css`

| 选择器 | 变化 | 说明 |
|---|---|---|
| `.space-head .caret`、`.space.is-collapsed …` | 新增 | 折叠箭头样式；折叠时隐藏目录和会话列表 |
| `.sessions`、`.session` | 修改 | 收紧间距和行高 |

## 配置与依赖

无。

## 测试

- `uv run pytest`：872 passed（已合入 origin/main）
- `node --check app.js` 通过；未在浏览器里做截图验证
