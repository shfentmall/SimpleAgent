# 对话里的 LaTeX 公式按数学排版显示，不再露出源码

- 日期：2026-09-28
- 对比基线：`e8a190c`（版本号 0.2.10）
- 对应里程碑：客户端小改进

## 功能变化

- 修复：空间会话里模型回复的公式（`$$\frac{\Delta P}{P} \approx -D_{mod} \times \Delta y$$` 这类）原样显示成源码。
  现在 `$…$`、`\(…\)` 是行内公式，`$$…$$`、`\[…\]` 和直接写的 `\begin{align}` 这类是行间公式，
  转成 MathML 交给浏览器原生排版。
- 新增：零依赖手写的 TeX → MathML 转换器 `web/math.js`，覆盖分式、根号、上下标、希腊字母、常见运算符和箭头、
  `\sum` `\int` `\lim`、`\left…\right`、重音、`\text`、`\mathbb` 这类字体、矩阵 / cases / aligned 环境。
  认不出的命令显示成红字，少了右括号也能渲染，流式输出时公式写到一半先显示已有部分。
- 单个 `$` 按 pandoc 的规矩认，「从 $5 涨到 $10」「价格是$100」这类钱数不会被当成公式。
- 在 Chrome + STIX Two Math 下调过显示：括号随内容拉伸、`∑` 在行间公式里变大、`\bar` 看得见、
  `\approx -D` 的负号不留二元运算符间距、撇号位置正常。

## 函数级改动

### `src/simpleagent/web/math.js`（新增）

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `texToMathML(tex, display)` | 新增 | 对外唯一接口：TeX 源码 → `<math>`；解析器意外出错（比如嵌套深到爆栈）就原样显示源码 |
| `Parser` | 新增 | 直接在字符串上递归下降：`next` / `peek` 读记号，`parseList` / `parseScripts` / `parseAtom` 解析原子和上下标，`command` 处理各个命令，`leftRight` / `environment` / `parseRows` / `table` 处理定界符和环境 |
| `styled(ch, font)` | 新增 | `\mathbb` `\mathbf` 这类字体换成 Unicode 数学字母（Chrome 不认 `mathvariant`） |
| 符号表 | 新增 | 希腊字母、运算符、关系符、箭头、大型运算符、函数名、重音、字体；查表一律用自有属性（`has`），`\constructor` 摸不到 `Object.prototype` |

### `src/simpleagent/web/markdown.js`

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `mathHtml(tex, display)` | 新增 | 调 `texToMathML`；math.js 没加载上就原样显示源码。node 跑测试时在这里 `require("./math.js")` |
| `mathOpen(line)` | 新增 | 识别行间公式块的开头（`$$`、`\[`、`\begin{align}` 等） |
| `renderBlocks()` | 修改 | 新增行间公式块：吃到结束符为止，没闭合就到末尾 |
| `startsBlock()` | 修改 | 公式块能打断段落 |
| `renderInline()` | 修改 | 行内代码之后、反斜杠转义之前取走 `$$…$$` / `\[…\]` / `\(…\)` / `$…$`，公式里的 `*` `_` `\{` 不被 Markdown 加工 |

### `src/simpleagent/web/app.js`

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `CURSOR_STOP` | 修改 | 加 `math`：流式光标不插进 `<math>` 里 |

### `src/simpleagent/web/index.html`、`styles.css`

- `index.html`：在 `markdown.js` 之前加载 `math.js`
- `styles.css`：`.md math` 点名数学字体 `STIX Two Math` / `Cambria Math`（Chrome 的 generic `math` 在 macOS 上落不到带 MATH 表的字体，括号不拉伸），`.math-block` 太宽时横向滚动，未知命令红字

## 配置与依赖

- 无新依赖、无配置变化

## 文档

- `docs/design/client-ui.md` 10.13 补「公式」一节，更新「没做的」

## 测试

- `tests/serve/test_markdown.py`：新增公式相关测试（块级 / 行内 / 分隔符、钱数不当公式、公式里不做 Markdown、列表和表格里的公式、流式没闭合、公式里的 HTML 注入和属性白名单），以及 `math.js` 的上下标、大型运算符、定界符、环境、字体、容错测试
- `tests/serve/test_web.py`：`math.js` 能取到，且在 `markdown.js` 之前加载
- 测试结果：887 passed
- 手动：浏览器面板里看了一页典型模型输出（久期公式、求和、极限、cases、pmatrix、aligned、表格里的公式）；`sa serve` 开发模式下 `math.js` 加载正常、控制台无报错、流式光标停在公式外面
