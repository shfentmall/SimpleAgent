# 空间会话里模型回复改为 Markdown 渲染

- 日期：2026-09-19
- 对比基线：`772d411`（Add changelog for the README rewrite and v0.1.0 release notes）
- 对应里程碑：W（个人 AI 工作台）

## 功能变化

- 新增：`src/simpleagent/web/markdown.js`，零依赖手写的 Markdown 渲染器，对外只暴露 `renderMarkdown(raw)`。支持标题、有序 / 无序 / 嵌套列表、任务列表（`- [ ]` / `- [x]`）、GFM 表格（含左中右对齐）、引用、分隔线、围栏代码块（含未闭合时延伸到末尾）、粗体 / 斜体 / 粗斜体 / 删除线、链接、裸网址自动链接。
- 升级：空间会话里助手（模型）的回复气泡改用 `renderMarkdown` 渲染（历史消息、流式增量、收尾统一渲染三处都改了），不再是原来的 `renderText`（只认代码块和行内代码 + `pre-wrap`）。用户气泡仍保持纯文本，不受影响。
- 升级：控制面板消息详情弹层（失败原因、stderr 等文本）继续用原来的 `renderText`，没有改动行为——那里的文本里 `#` 开头的行不该被当成标题。
- 安全：渲染器保证原文每个字符恰好经过一次 `escapeHtml` 才进 HTML，标签只由渲染器自己生成；只有 `http(s)` 链接可点（新标签页打开，`target="_blank" rel="noopener noreferrer"`），`javascript:`、相对路径等只显示文字；**图片不加载**，只渲染成「图片：alt」的链接文字，用于防止 prompt injection 诱导模型输出图片 URL 把数据外发；唯一放行的原始 HTML 标签是固定形式的 `<br>`。
- 文档：`docs/design/client-ui.md` 顶部状态新增一行，10.4 的渲染顺序描述改为指向新渲染器，新增 10.13 小节详细说明设计取舍（为什么手写不引库、两段式分块、行内占位符处理、链接/图片策略、流式与光标处理、测试方式、未做的事项）。

## 函数级改动

### `src/simpleagent/web/markdown.js`（新文件）

零依赖，立即执行函数包起来（浏览器里挂到 `window`），只对外暴露 `renderMarkdown`；在 Node 环境下额外导出 `module.exports = { renderMarkdown }` 供测试用。

| 函数 | 说明 |
|---|---|
| `escapeHtml(s)` | 转义 `& < > " '`，是全文安全性的唯一入口 |
| `indentOf(line)` | 计算行首空白列数，tab 按 4 列对齐 |
| `dedent(line, n)` | 去掉行首最多 n 列空白，供代码块 / 列表项去缩进用 |
| `fenceOpen(line)` | 判断一行是否是围栏代码块起始，返回缩进和 marker；info 串里带反引号的行不算（那是行内代码） |
| `fenceCloses(line, marker)` | 判断一行是否闭合给定 marker 的围栏 |
| `splitRow(line)` | 表格一行拆成单元格，处理 `\|` 转义和行内代码里的 `\|` 不算分隔，退化到硬切兜底 |
| `isTableStart(lines, i)` | 判断第 i 行是否是表格起始（下一行是合法的对齐分隔行） |
| `startsBlock(lines, i)` | 判断某行是否是新块的开头（代码块 / 标题 / 分隔线 / 引用 / 列表 / 表格），用于段落和懒惰续行的边界判断 |
| `renderBlocks(lines, tight)` | 块级渲染主循环：按行分派到代码块 / 标题 / 分隔线 / 引用 / 列表 / 表格 / 段落；`tight` 用于紧凑列表项内不包 `<p>` |
| `renderList(lines, i, out)` | 列表解析：按缩进切分项、识别有序/无序、松散/紧凑、任务列表 checkbox、懒惰续行，递归调用 `renderBlocks` 渲染项内容 |
| `renderTable(lines, i, out)` | 表格解析：表头 + 对齐行 + 数据行，单元格按表头列数补齐/截断 |
| `splitUrlTail(url)` | 裸网址末尾标点（句号、逗号等）和多出来的右括号从网址里剥离，归还给正文 |
| `safeLink(url, labelHtml, isImage)` | 生成安全链接：非 http(s) 只显示文字（`span.link-off`）；图片一律不生成 `<img>`，显示成「图片：xxx」的链接 |
| `emphasis(s)` | 在已转义文本上处理粗体 / 斜体 / 粗斜体 / 删除线；`*` 排除类似 `2 * 3 * 4` 的误判，`_` 排除 `snake_case` |
| `renderInline(raw)` | 行内渲染入口：代码、转义字符、`<br>`、链接、尖括号链接、裸网址先换成占位符，剩余文本整体转义后跑 `emphasis`，最后把占位符换回去 |
| `renderMarkdown(raw)` | 对外入口：统一换行符、删掉可能出现的 NUL（占位符会用到 `\x00`），交给 `renderBlocks` |

### `src/simpleagent/web/app.js`

| 位置 | 变化 | 说明 |
|---|---|---|
| `renderText(raw)` 上方注释 | 修改 | 补充说明该函数现在只给控制面板消息弹层用，对话回复走 `markdown.js` |
| `ensureAssistantBubble()` | 修改 | 助手气泡的 `.text` 元素加上 `md` class，配合新样式 |
| `placeCursor(el)`（新增） | 新增 | 把流式光标 `<span class="cursor">` 插到最后一个块（段落 / 列表项 / 代码块）末尾，遇到 `A`/`BR`/`HR`/`INPUT`（`CURSOR_STOP` 集合）就停在外面，不钻进去 |
| `finishAssistantBubble()` | 修改 | 收尾时先用 `renderMarkdown(state.acc)` 整段重渲染一次去掉光标，再清空状态；因为外部 CLI 执行者不一定先发 `message_done` 再发工具调用，原来放在 `message_done` 分支里的收尾渲染挪到了这里统一处理 |
| `renderHistory(messages)` | 修改 | 历史里的助手消息从 `renderText(m.content)` 改为 `renderMarkdown(m.content)`，气泡加 `md` class |
| `onFrame()` 的 `text_delta` 分支 | 修改 | 流式增量从 `renderText(state.acc) + 光标` 改为 `renderMarkdown(state.acc)` 后调 `placeCursor(el)` 插光标 |
| `onFrame()` 的 `message_done` 分支 | 修改 | 去掉原来内联的收尾渲染（`state.accEl.innerHTML = renderText(state.acc)`），改成直接调 `finishAssistantBubble()`（渲染逻辑已挪进去） |

### `src/simpleagent/web/index.html`

在 `<script src="/assets/app.js">` 之前新增一行 `<script src="/assets/markdown.js">`，保证 `renderMarkdown` 先挂到 `window` 上，`app.js` 才能直接调用。

### `src/simpleagent/web/styles.css`

新增 `.md` 一组选择器（约 30 行），覆盖标题层级字号、列表间距、任务列表 checkbox、引用、分隔线、行内代码 / 代码块、链接、不可点链接的虚线下划线提示、表格（含横向滚动容器 `.table-wrap`）。`.msg .text.md` 把原来的 `pre-wrap` 换行方式取消，改为块级元素的 `margin` 控制间距。

## 配置与依赖

- 无 Python 依赖变化，`pyproject.toml` / `uv.lock` 未改动。
- 新增测试文件依赖本机是否装了 `node`：装了才会真正跑 `tests/serve/test_markdown.py` 的渲染断言，没装则整体跳过（不引入 npm）。本机验证时 `node v26.9.0` 可用，19 个测试全部实际执行并通过。
- 用户无需手动处理任何配置或数据目录。

## 测试

- 新增 `tests/serve/test_markdown.py`：19 个测试用例，通过 `node -e` 直接 `require` `markdown.js`（其 `module.exports` 已适配 Node），在 Python 里断言渲染出的 HTML。覆盖标题/段落、紧凑与松散列表、有序列表起始序号与项内代码块、列表打断段落、任务列表、表格对齐与单元格、表格分隔行不合法时不识别为表格、引用与分隔线、围栏代码转义与未闭合延伸到末尾、粗体/斜体/删除线、误渲染排除（`__init__.py`、`2 * 3 * 4`）、行内代码与反斜杠转义保持字面量、链接、裸网址剥离尾部标点、各处原始 HTML 均被转义、只有 http(s) 链接可点、图片不加载、只放行 `<br>` 标签。
- 修改 `tests/serve/test_web.py` 的 `test_index_and_assets`：新增断言 `markdown.js` 在页面里排在 `app.js` 之前加载，以及 `/assets/markdown.js` 能正常取到且内容含 `renderMarkdown`。
- 测试结果：`uv run pytest -q` 全量 333 passed。
- `uv run ruff check`：All checks passed。
- `uv run ruff format --check`：108 files already formatted（无需改动）。

## 相关笔记

无
