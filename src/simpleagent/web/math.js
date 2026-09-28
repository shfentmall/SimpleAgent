/* 模型回复里的 LaTeX 公式：TeX → MathML，排版交给浏览器。零依赖，在 markdown.js 之前加载，
   对外只有 texToMathML(tex, display)。

   为什么是 MathML：Chrome 109+ / Safari / Firefox 都原生支持 MathML Core，这里只做「TeX 子集
   → MathML 标签」的翻译。KaTeX 要带几百 KB 的 JS + CSS + 字体，也违反前端零依赖。

   安全口径和 markdown.js 一样：公式里的每个字符都经过 escapeHtml 才进输出，标签和属性值都来自
   这里的固定表（\color 的颜色、\hspace 的宽度另外按白名单校验）。

   解析是宽松的：少了右括号就当它在末尾，认不出的命令原样显示成红字，不会整条公式失败。
   流式输出时公式写到一半也能先渲染出已有的部分。

   Chrome 的 MathML Core 不认 mathvariant（只认 normal）和 columnalign，所以：
   \mathbb 这类字体换成 Unicode 数学字母（ℝ、𝐱），表格对齐写成 mtd 的 style。 */

(function (root) {
  "use strict";

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  /* ─────────────────────────── 符号表 ─────────────────────────── */
  const GREEK = {
    alpha: "α", beta: "β", gamma: "γ", delta: "δ", epsilon: "ϵ", varepsilon: "ε", zeta: "ζ",
    eta: "η", theta: "θ", vartheta: "ϑ", iota: "ι", kappa: "κ", varkappa: "ϰ", lambda: "λ",
    mu: "μ", nu: "ν", xi: "ξ", omicron: "ο", pi: "π", varpi: "ϖ", rho: "ρ", varrho: "ϱ",
    sigma: "σ", varsigma: "ς", tau: "τ", upsilon: "υ", phi: "ϕ", varphi: "φ", chi: "χ",
    psi: "ψ", omega: "ω",
  };

  /* 正体的普通符号：大写希腊字母在 TeX 里是正体 */
  const ORD = {
    Gamma: "Γ", Delta: "Δ", Theta: "Θ", Lambda: "Λ", Xi: "Ξ", Pi: "Π", Sigma: "Σ",
    Upsilon: "Υ", Phi: "Φ", Psi: "Ψ", Omega: "Ω",
    infty: "∞", partial: "∂", nabla: "∇", hbar: "ℏ", ell: "ℓ", Re: "ℜ", Im: "ℑ", aleph: "ℵ",
    wp: "℘", emptyset: "∅", varnothing: "∅", imath: "ı", jmath: "ȷ",
    forall: "∀", exists: "∃", nexists: "∄", neg: "¬", lnot: "¬", angle: "∠",
    measuredangle: "∡", prime: "′", degree: "°", circ: "∘", top: "⊤", bot: "⊥",
    triangle: "△", square: "□", Box: "□", blacksquare: "■", checkmark: "✓", S: "§", P: "¶",
    sharp: "♯", flat: "♭", natural: "♮", clubsuit: "♣", diamondsuit: "♢", heartsuit: "♡",
    spadesuit: "♠", backslash: "\\", "%": "%", $: "$", "#": "#", "&": "&", _: "_",
  };

  /* 运算符、关系符、箭头：交给 <mo>，间距由浏览器的运算符字典决定 */
  const OPS = {
    pm: "±", mp: "∓", times: "×", div: "÷", cdot: "⋅", ast: "∗", star: "⋆", bullet: "∙",
    oplus: "⊕", ominus: "⊖", otimes: "⊗", odot: "⊙", oslash: "⊘", cup: "∪", cap: "∩",
    setminus: "∖", wedge: "∧", land: "∧", vee: "∨", lor: "∨", sqcup: "⊔", sqcap: "⊓",
    uplus: "⊎", dagger: "†", ddagger: "‡", diamond: "⋄", amalg: "⨿", wr: "≀",
    leq: "≤", le: "≤", geq: "≥", ge: "≥", neq: "≠", ne: "≠", approx: "≈", equiv: "≡",
    sim: "∼", simeq: "≃", cong: "≅", propto: "∝", ll: "≪", gg: "≫", prec: "≺", succ: "≻",
    preceq: "⪯", succeq: "⪰", subset: "⊂", supset: "⊃", subseteq: "⊆", supseteq: "⊇",
    subsetneq: "⊊", supsetneq: "⊋", in: "∈", notin: "∉", ni: "∋", mid: "∣", nmid: "∤",
    parallel: "∥", nparallel: "∦", perp: "⊥", models: "⊨", vdash: "⊢", dashv: "⊣",
    asymp: "≍", doteq: "≐", leqslant: "⩽", geqslant: "⩾", lesssim: "≲", gtrsim: "≳",
    approxeq: "≊", lt: "<", gt: ">", coloneqq: "≔", triangleq: "≜", neql: "≠",
    to: "→", rightarrow: "→", leftarrow: "←", gets: "←", leftrightarrow: "↔",
    Rightarrow: "⇒", Leftarrow: "⇐", Leftrightarrow: "⇔", implies: "⟹", impliedby: "⟸",
    iff: "⟺", longrightarrow: "⟶", longleftarrow: "⟵", longleftrightarrow: "⟷",
    Longrightarrow: "⟹", Longleftarrow: "⟸", Longleftrightarrow: "⟺", mapsto: "↦",
    longmapsto: "⟼", uparrow: "↑", downarrow: "↓", updownarrow: "↕", Uparrow: "⇑",
    Downarrow: "⇓", nearrow: "↗", searrow: "↘", nwarrow: "↖", swarrow: "↙",
    hookrightarrow: "↪", hookleftarrow: "↩", rightleftharpoons: "⇌", leftrightharpoons: "⇋",
    rightharpoonup: "⇀", therefore: "∴", because: "∵",
    ldots: "…", dots: "…", dotsc: "…", dotso: "…", cdots: "⋯", dotsb: "⋯", dotsm: "⋯",
    dotsi: "⋯", vdots: "⋮", ddots: "⋱", colon: ":",
  };

  /* \not 后面跟这些就换成带斜线的字符 */
  const NEGATED = {
    "=": "≠", "<": "≮", ">": "≯", in: "∉", ni: "∌", equiv: "≢", subset: "⊄", supset: "⊅",
    subseteq: "⊈", supseteq: "⊉", sim: "≁", simeq: "≄", approx: "≉", cong: "≇", leq: "≰",
    le: "≰", geq: "≱", ge: "≱", mid: "∤", parallel: "∦", exists: "∄",
  };

  /* 可以当定界符的命令（\left \right \big 后面、或直接写） */
  const DELIM_CMDS = {
    "{": "{", "}": "}", lbrace: "{", rbrace: "}", lbrack: "[", rbrack: "]", lparen: "(",
    rparen: ")", "|": "‖", vert: "|", Vert: "‖", lvert: "|", rvert: "|", lVert: "‖",
    rVert: "‖", langle: "⟨", rangle: "⟩", lceil: "⌈", rceil: "⌉", lfloor: "⌊", rfloor: "⌋",
    backslash: "\\", uparrow: "↑", downarrow: "↓", updownarrow: "↕",
  };
  const DELIM_CHARS = { "(": "(", ")": ")", "[": "[", "]": "]", "|": "|", "/": "/", "<": "⟨", ">": "⟩" };

  /* 大型运算符：上下限在行间公式里放正上 / 正下，行内放右边（movablelimits） */
  const BIG_OPS = {
    sum: "∑", prod: "∏", coprod: "∐", bigcup: "⋃", bigcap: "⋂", bigoplus: "⨁",
    bigotimes: "⨂", bigodot: "⨀", bigvee: "⋁", bigwedge: "⋀", bigsqcup: "⨆", biguplus: "⨄",
  };
  /* 积分号的上下限默认放右边 */
  const INTEGRALS = { int: "∫", iint: "∬", iiint: "∭", oint: "∮", oiint: "∯" };

  const FUNCS = new Set(("arcsin arccos arctan arccot arg cos cosh cot coth csc deg dim exp "
    + "hom ker lg ln log sec sin sinh tan tanh sgn").split(" "));
  const LIMIT_FUNCS = {
    lim: "lim", limsup: "lim\u2009sup", liminf: "lim\u2009inf", max: "max", min: "min", sup: "sup",
    inf: "inf", det: "det", gcd: "gcd", Pr: "Pr", argmax: "arg\u2009max", argmin: "arg\u2009min",
  };

  /* 重音：[符号, 是否随内容拉伸]。字符是在 STIX Two Math 下挑过的：\widehat 要用组合字符 U+0302
     才拉得宽；\bar 用 ‾ 会贴在字母顶上看不见。STIX 没有能拉宽的波浪线，\widetilde 只能是普通大小 */
  const ACCENTS = {
    hat: ["ˆ", false], widehat: ["\u0302", true], tilde: ["˜", false], widetilde: ["˜", true],
    bar: ["¯", false], overline: ["‾", true], vec: ["→", false], overrightarrow: ["→", true],
    overleftarrow: ["←", true], overleftrightarrow: ["↔", true], dot: ["˙", false],
    ddot: ["¨", false], check: ["ˇ", false], breve: ["˘", false], acute: ["´", false],
    grave: ["`", false], mathring: ["˚", false],
  };

  const SPACES = {
    ",": "0.1667em", thinspace: "0.1667em", ":": "0.2222em", ">": "0.2222em",
    medspace: "0.2222em", ";": "0.2778em", thickspace: "0.2778em", "!": "-0.1667em",
    negthinspace: "-0.1667em", " ": "0.3333em", enspace: "0.5em", quad: "1em", qquad: "2em",
  };

  /* 环境两边的括号 */
  const ENV_FENCES = {
    pmatrix: ["(", ")"], bmatrix: ["[", "]"], Bmatrix: ["{", "}"], vmatrix: ["|", "|"],
    Vmatrix: ["‖", "‖"], cases: ["{", ""], dcases: ["{", ""], rcases: ["", "}"],
  };

  /* \big( 这类：固定高度的定界符 */
  const BIG_SIZES = { big: "1.2em", Big: "1.623em", bigg: "2.047em", Bigg: "2.470em" };

  const FONTS = {
    mathrm: "normal", mathup: "normal", mathnormal: null, mathit: "italic", mathbf: "bold",
    mathbfit: "bold-italic", boldsymbol: "bold-italic", bm: "bold-italic", pmb: "bold-italic",
    mathbb: "double-struck", mathcal: "script", mathscr: "script", mathfrak: "fraktur",
    mathsf: "sans-serif", mathtt: "monospace",
  };
  const TEXT_STYLES = {
    text: "", textrm: "", textup: "", textnormal: "", mbox: "", hbox: "", textsf: "",
    texttt: "", textbf: "font-weight:bold", textit: "font-style:italic",
    emph: "font-style:italic",
  };

  /* Unicode 数学字母区块：[大写 A, 小写 a, 数字 0] 的码位 */
  const FONT_BASE = {
    bold: [0x1d400, 0x1d41a, 0x1d7ce], italic: [0x1d434, 0x1d44e, 0],
    "bold-italic": [0x1d468, 0x1d482, 0x1d7ce], script: [0x1d49c, 0x1d4b6, 0],
    fraktur: [0x1d504, 0x1d51e, 0], "double-struck": [0x1d538, 0x1d552, 0x1d7d8],
    "sans-serif": [0x1d5a0, 0x1d5ba, 0x1d7e2], monospace: [0x1d670, 0x1d68a, 0x1d7f6],
  };
  /* 这些字母更早就进了 Unicode，数学字母区块里对应的位置是空的 */
  const FONT_HOLES = {
    italic: { h: "ℎ" },
    script: { B: "ℬ", E: "ℰ", F: "ℱ", H: "ℋ", I: "ℐ", L: "ℒ", M: "ℳ", R: "ℛ", e: "ℯ", g: "ℊ", o: "ℴ" },
    fraktur: { C: "ℭ", H: "ℌ", I: "ℑ", R: "ℜ", Z: "ℨ" },
    "double-struck": { C: "ℂ", H: "ℍ", N: "ℕ", P: "ℙ", Q: "ℚ", R: "ℝ", Z: "ℤ" },
  };

  /* 希腊字母只有粗体、斜体、粗斜体：[大写 Α, 小写 α] */
  const GREEK_BASE = {
    bold: [0x1d6a8, 0x1d6c2], italic: [0x1d6e2, 0x1d6fc], "bold-italic": [0x1d71c, 0x1d736],
  };

  // 查表一律用自有属性：\constructor 这种名字不能摸到 Object.prototype 上
  const has = (table, key) => Object.prototype.hasOwnProperty.call(table, key);

  function styled(ch, font) {
    if (!font) return ch;
    if (has(FONT_HOLES, font) && has(FONT_HOLES[font], ch)) return FONT_HOLES[font][ch];
    const code = ch.charCodeAt(0);
    const greek = has(GREEK_BASE, font) ? GREEK_BASE[font] : null;
    if (greek && code >= 0x391 && code <= 0x3a9) return String.fromCodePoint(greek[0] + code - 0x391);
    if (greek && code >= 0x3b1 && code <= 0x3c9) return String.fromCodePoint(greek[1] + code - 0x3b1);
    const base = has(FONT_BASE, font) ? FONT_BASE[font] : null;
    if (!base) return ch;
    if (ch >= "A" && ch <= "Z") return String.fromCodePoint(base[0] + code - 65);
    if (ch >= "a" && ch <= "z") return String.fromCodePoint(base[1] + code - 97);
    if (ch >= "0" && ch <= "9" && base[2]) return String.fromCodePoint(base[2] + code - 48);
    return ch;
  }

  /* ─────────────────────────── 输出小工具 ─────────────────────────── */
  const mi = (c, normal) => `<mi${normal ? ' mathvariant="normal"' : ""}>${escapeHtml(c)}</mi>`;
  const mo = (c, attrs = "") => `<mo${attrs}>${escapeHtml(c)}</mo>`;
  const mspace = (w) => `<mspace width="${w}"></mspace>`;
  // 上下标、分式的每个参数都必须是单个元素，多个就包一层 mrow
  const mrow = (list) => (list.length === 1 ? list[0] : `<mrow>${list.join("")}</mrow>`);
  const fence = (c) => (c ? mo(c, ' fence="true" stretchy="true"') : "");

  // 输出里「以运算符结尾」的元素：<mo>，但右括号这类不算
  const OPERATOR_RE = /^<mo[ >](?!.*>[)\]|⟩⌉⌋‖}]<\/mo>$)/;
  const STARRED = new Set(["operatorname", "tag", "hspace"]);
  const COLOR_RE = /^(?:[a-zA-Z]{1,20}|#[0-9a-fA-F]{3,8})$/;
  const LENGTH_RE = /^-?(?:\d+\.?\d*|\.\d+)(?:em|ex|pt|px|mm|cm)$/;
  const CJK_RE = /[\u2e80-\u9fff\uf900-\ufaff\uff00-\uffef\u3000-\u303f]/;

  /* ─────────────────────────── 解析 ─────────────────────────── */
  /* 直接在字符串上递归下降：\text{...}、\begin{...} 这类参数要按原文读，先切 token 反而麻烦 */
  class Parser {
    constructor(src) {
      this.src = src;
      this.i = 0;
      this.font = null;
    }

    skipSpace() {
      while (this.i < this.src.length && /\s/.test(this.src[this.i])) this.i++;
    }

    /* 读下一个记号：{t:"cmd", v:"frac"} / {t:"num", v:"3.14"} / {t:"char", v:"x"} */
    next() {
      this.skipSpace();
      const s = this.src;
      if (this.i >= s.length) return null;
      if (s[this.i] === "\\") {
        const m = /\\([a-zA-Z]+|[^a-zA-Z]?)/y;
        m.lastIndex = this.i;
        const hit = m.exec(s);
        this.i += hit[0].length;
        let v = hit[1];
        if (v === "cr" || v === "newline") v = "\\";
        if (/^\s$/.test(v)) v = " ";  // 反斜杠后面跟换行 / tab 也是一个空格
        // \operatorname* 这类带星号的变体；别的命令后面的 * 是乘号
        if (s[this.i] === "*" && STARRED.has(v)) { v += "*"; this.i++; }
        return { t: "cmd", v };
      }
      const num = /\d+(?:\.\d+)?/y;
      num.lastIndex = this.i;
      const n = num.exec(s);
      if (n) { this.i += n[0].length; return { t: "num", v: n[0] }; }
      const ch = String.fromCodePoint(s.codePointAt(this.i));
      this.i += ch.length;
      return { t: "char", v: ch };
    }

    peek() {
      const at = this.i;
      const t = this.next();
      this.i = at;
      return t;
    }

    /* 列表在这些记号前停下，交给外层（分组、环境、\left…\right）处理 */
    isStop(t) {
      if (!t) return true;
      if (t.t === "char") return t.v === "}" || t.v === "&";
      return t.t === "cmd" && (t.v === "\\" || t.v === "end" || t.v === "right" || t.v === "middle");
    }

    /* 按原文读一个 {...}，不解析：\text、\begin 的环境名、\color 的颜色 */
    rawGroup() {
      this.skipSpace();
      const s = this.src;
      if (s[this.i] !== "{") {
        const t = this.next();  // \text x 这种不带括号的写法只取一个字符
        return t ? (t.t === "cmd" ? `\\${t.v}` : t.v) : "";
      }
      let depth = 0;
      const start = this.i + 1;
      for (; this.i < s.length; this.i++) {
        if (s[this.i] === "\\") { this.i++; continue; }
        if (s[this.i] === "{") depth++;
        else if (s[this.i] === "}" && --depth === 0) return s.slice(start, this.i++);
      }
      return s.slice(start);
    }

    /* 可选参数 [...]：\sqrt[3]{x}、\\[2pt] */
    optional() {
      this.skipSpace();
      if (this.src[this.i] !== "[") return null;
      const end = this.src.indexOf("]", this.i);
      if (end < 0) return null;
      const body = this.src.slice(this.i + 1, end);
      this.i = end + 1;
      return body;
    }

    /* \\[2pt] 的行距参数丢掉；只认长度，免得把下一行开头的 [a, b] 吃掉 */
    rowGap() {
      const m = /\s*\[\s*-?[\d.]+\s*[a-z]{2}\s*\]/y;
      m.lastIndex = this.i;
      if (m.exec(this.src)) this.i = m.lastIndex;
    }

    /* 子公式单独解析，字体设置沿用当前的 */
    sub(tex) {
      const p = new Parser(tex);
      p.font = this.font;
      return p.parseTable();
    }

    /* 命令的参数：{...} 或者单个记号 */
    parseArg() {
      const t = this.peek();
      if (!t || this.isStop(t)) return "<mrow></mrow>";
      if (t.t === "char" && t.v === "{") {
        this.next();
        const body = this.parseList();
        this.eat("}");
        return mrow(body);
      }
      // x^23 是 x² 后面跟 3：数字只取第一位
      if (t.t === "num") {
        this.skipSpace();
        return this.number(this.src[this.i++]);
      }
      const atom = this.parseAtom();
      return atom ? atom.ml : "<mrow></mrow>";
    }

    eat(ch) {
      const t = this.peek();
      if (t && t.t === "char" && t.v === ch) this.next();
    }

    number(v) {
      return `<mn>${escapeHtml(this.font ? [...v].map((c) => styled(c, this.font)).join("") : v)}</mn>`;
    }

    /* 一串原子，直到停止记号 */
    parseList() {
      const out = [];
      for (;;) {
        const t = this.peek();
        if (this.isStop(t)) break;
        if (t.t === "cmd" && /^(displaystyle|textstyle|scriptstyle|color)$/.test(t.v)) {
          // 这几个作用于同组里剩下的全部内容
          this.next();
          let attrs = "";
          if (t.v === "color") {
            const c = this.rawGroup().trim();
            attrs = COLOR_RE.test(c) ? ` mathcolor="${c}"` : "";
          } else {
            attrs = t.v === "displaystyle" ? ' displaystyle="true"' : ' displaystyle="false"';
          }
          out.push(`<mstyle${attrs}>${this.parseList().join("")}</mstyle>`);
          break;
        }
        let atom;
        if (t.t === "char" && (t.v === "^" || t.v === "_" || t.v === "'")) {
          atom = { ml: "<mrow></mrow>" };  // 没有底数的上下标：{}^{14}C
        } else {
          atom = this.parseAtom();
          if (!atom) continue;
        }
        // 开头、运算符 / 关系符 / 左括号后面的正负号是一元的（≈ −D），不留二元运算符的间距。
        // 浏览器只会把 mrow 的第一个 mo 当前缀，夹在中间的要标 form="prefix"
        if (atom.sign && (!out.length || OPERATOR_RE.test(out[out.length - 1]))) {
          atom.ml = atom.ml.replace("<mo>", '<mo form="prefix">');
        }
        out.push(this.parseScripts(atom));
        // \sin x 之间有个小间距；后面紧跟括号就不加
        if (atom.func) {
          const n = this.peek();
          const open = n && ((n.t === "char" && "([|".includes(n.v))
            || (n.t === "cmd" && (n.v === "left" || has(DELIM_CMDS, n.v) || /^(big|Big|bigg|Bigg)[lrm]?$/.test(n.v))));
          if (n && !this.isStop(n) && !open) out.push(mspace("0.1667em"));
        }
      }
      return out;
    }

    /* 底数后面的 ^ _ ' 和 \limits */
    parseScripts(atom) {
      let sub = null;
      let sup = null;
      let primes = 0;
      let limits = atom.limits || null;
      for (;;) {
        const t = this.peek();
        if (!t) break;
        if (t.t === "cmd" && (t.v === "limits" || t.v === "nolimits")) {
          this.next();
          limits = t.v === "limits" ? "force" : null;
          continue;
        }
        if (t.t !== "char") break;
        if (t.v === "'") { this.next(); primes++; continue; }
        if (t.v !== "^" && t.v !== "_") break;
        this.next();
        const arg = this.parseArg();
        if (t.v === "^") sup = sup ? `<mrow>${sup}${arg}</mrow>` : arg;
        else sub = sub ? `<mrow>${sub}${arg}</mrow>` : arg;
      }
      // 撇号的字形本来就在上标的高度，单独出现时直接跟在后面；放进 msup 会被抬得太高、缩得太小
      const prime = primes ? mo(primes <= 4 ? "′″‴⁗"[primes - 1] : "′".repeat(primes), ' lspace="0" rspace="0"') : "";
      if (prime && sup) sup = `<mrow>${prime}${sup}</mrow>`;
      const ml = this.attach(atom.ml, sub, sup, limits);
      return prime && !sup ? `<mrow>${ml}${prime}</mrow>` : ml;
    }

    attach(base, sub, sup, limits) {
      if (!sub && !sup) return base;
      if (limits) {
        if (limits === "force") base = base.replace('movablelimits="true"', 'movablelimits="false"');
        if (sub && sup) return `<munderover>${base}${sub}${sup}</munderover>`;
        return sub ? `<munder>${base}${sub}</munder>` : `<mover>${base}${sup}</mover>`;
      }
      if (sub && sup) return `<msubsup>${base}${sub}${sup}</msubsup>`;
      return sub ? `<msub>${base}${sub}</msub>` : `<msup>${base}${sup}</msup>`;
    }

    /* 一个原子（不含上下标）。返回 {ml, limits?, func?}，null 表示这个记号不产生输出 */
    parseAtom() {
      const t = this.next();
      if (t.t === "num") return { ml: this.number(t.v) };
      if (t.t === "cmd") return this.command(t.v);
      const c = t.v;
      if (c === "{") {
        const body = this.parseList();
        this.eat("}");
        return { ml: mrow(body) };
      }
      if (/[a-zA-Z]/.test(c)) {
        if (this.font === "normal") {
          // \mathrm{kg}：连续的字母合成一个正体的 <mi>
          let word = c;
          while (/[a-zA-Z]/.test(this.src[this.i] || "")) word += this.src[this.i++];
          return { ml: mi(word, word.length === 1) };
        }
        return { ml: this.font ? mi(styled(c, this.font)) : mi(c) };
      }
      if ("()[]|".includes(c)) {
        return { ml: mo(c, ' stretchy="false"') };  // 普通括号不跟着内容变高，和 TeX 一致
      }
      if (c === "-") return { ml: mo("−"), sign: true };
      if (c === "+") return { ml: mo("+"), sign: true };
      if (c === "*") return { ml: mo("∗") };
      if (c === "~") return { ml: mspace("0.3333em") };
      if (c === "'") return { ml: mo("′") };
      if (".?@\"#%$".includes(c)) return { ml: mi(c, true) };
      if (CJK_RE.test(c)) {
        // 公式里夹的中文按一段文字显示
        let text = c;
        while (this.i < this.src.length && CJK_RE.test(this.src[this.i])) text += this.src[this.i++];
        return { ml: `<mtext>${escapeHtml(text)}</mtext>` };
      }
      if (/\p{L}/u.test(c)) return { ml: this.font === "normal" ? mi(c, true) : mi(styled(c, this.font)) };
      if (/\p{N}/u.test(c)) return { ml: `<mn>${escapeHtml(c)}</mn>` };
      return { ml: mo(c) };
    }

    /* 希腊字母和正体符号：\mathbf{\beta} 这类要换成对应字体的字母 */
    symbol(c, upright) {
      const f = styled(c, this.font);
      if (f !== c) return mi(f);
      return mi(c, upright || this.font === "normal");
    }

    /* 定界符：\left 后面、\big 后面 */
    delimiter() {
      const t = this.next();
      if (!t) return "";
      if (t.t === "char") return t.v === "." ? "" : has(DELIM_CHARS, t.v) ? DELIM_CHARS[t.v] : t.v;
      if (has(DELIM_CMDS, t.v)) return DELIM_CMDS[t.v];
      return has(OPS, t.v) ? OPS[t.v] : "";
    }

    leftRight() {
      const open = this.delimiter();
      const body = [fence(open)];
      let close = "";
      for (;;) {
        body.push(...this.parseList());
        const t = this.peek();
        if (t && t.t === "cmd" && t.v === "middle") {
          this.next();
          body.push(fence(this.delimiter()));
          continue;
        }
        if (t && t.t === "cmd" && t.v === "right") {
          this.next();
          close = this.delimiter();
        }
        break;  // 缺了 \right：到这一组结束为止
      }
      body.push(fence(close));
      return { ml: `<mrow>${body.join("")}</mrow>` };
    }

    command(name) {
      if (has(GREEK, name)) return { ml: this.symbol(GREEK[name], false) };
      if (has(ORD, name)) return { ml: this.symbol(ORD[name], true) };
      if (has(OPS, name)) return { ml: mo(OPS[name]), sign: name === "pm" || name === "mp" };
      if (has(DELIM_CMDS, name)) return { ml: mo(DELIM_CMDS[name], ' stretchy="false"') };
      if (has(BIG_OPS, name)) return { ml: mo(BIG_OPS[name], ' movablelimits="true"'), limits: "movable" };
      if (has(INTEGRALS, name)) return { ml: mo(INTEGRALS[name]), limits: null };
      if (FUNCS.has(name)) return { ml: `<mi>${name}</mi>`, func: true };
      if (has(LIMIT_FUNCS, name)) {
        return {
          ml: mo(LIMIT_FUNCS[name], ' movablelimits="true" lspace="0" rspace="0"'),
          limits: "movable",
          func: true,
        };
      }
      if (has(SPACES, name)) return { ml: mspace(SPACES[name]) };
      if (has(ACCENTS, name)) {
        const [mark, stretch] = ACCENTS[name];
        const base = this.parseArg();
        // 不标 accent="true"：Chrome 按重音排时符号紧贴字母顶，¯ 这类直接和字母糊在一起
        return { ml: `<mover>${base}${mo(mark, ` stretchy="${stretch}"`)}</mover>` };
      }
      if (has(FONTS, name)) {
        const saved = this.font;
        this.font = FONTS[name];
        const body = this.parseArg();
        this.font = saved;
        return { ml: body };
      }
      if (has(TEXT_STYLES, name)) return { ml: this.text(this.rawGroup(), TEXT_STYLES[name]) };
      if (/^(big|Big|bigg|Bigg)[lrm]?$/.test(name)) {
        const size = BIG_SIZES[name.replace(/[lrm]$/, "")];
        const d = this.delimiter();
        return d ? { ml: mo(d, ` stretchy="true" minsize="${size}" maxsize="${size}"`) } : null;
      }
      switch (name) {
        case "frac": case "dfrac": case "tfrac": case "cfrac": {
          const frac = `<mfrac>${this.parseArg()}${this.parseArg()}</mfrac>`;
          if (name === "frac" || name === "cfrac") return { ml: frac };
          return { ml: `<mstyle displaystyle="${name === "dfrac"}">${frac}</mstyle>` };
        }
        case "binom": case "dbinom": case "tbinom": {
          const b = `<mfrac linethickness="0">${this.parseArg()}${this.parseArg()}</mfrac>`;
          return { ml: `<mrow>${fence("(")}${b}${fence(")")}</mrow>` };
        }
        case "sqrt": {
          const index = this.optional();
          const base = this.parseArg();
          return { ml: index === null ? `<msqrt>${base}</msqrt>` : `<mroot>${base}${this.sub(index)}</mroot>` };
        }
        case "left": return this.leftRight();
        case "right": case "middle": this.delimiter(); return null;  // 落单的，丢掉
        case "operatorname": case "operatorname*": {
          const word = this.rawGroup().replace(/\\[,;:! ]/g, " ").replace(/\\/g, "").trim();
          if (name.endsWith("*")) {
            return { ml: mo(word, ' movablelimits="true" lspace="0" rspace="0"'), limits: "movable", func: true };
          }
          return { ml: mi(word, word.length === 1), func: true };
        }
        case "overbrace": case "underbrace": {
          const base = this.parseArg();
          const over = name === "overbrace";
          const brace = mo(over ? "⏞" : "⏟", ' stretchy="true"');
          const ml = over ? `<mover>${base}${brace}</mover>` : `<munder>${base}${brace}</munder>`;
          return { ml, limits: "force" };  // 后面的 ^{} / _{} 放在括号正上 / 正下
        }
        case "underline": {
          const base = this.parseArg();
          return { ml: `<munder accentunder="true">${base}${mo("‾", ' stretchy="true"')}</munder>` };
        }
        case "overset": case "stackrel": case "underset": {
          const top = this.parseArg();
          const base = this.parseArg();
          return { ml: name === "underset" ? `<munder>${base}${top}</munder>` : `<mover>${base}${top}</mover>` };
        }
        case "xrightarrow": case "xleftarrow": {
          const below = this.optional();
          const above = this.parseArg();
          const arrow = mo(name === "xrightarrow" ? "→" : "←", ' stretchy="true" minsize="2em"');
          return {
            ml: below === null ? `<mover>${arrow}${above}</mover>`
              : `<munderover>${arrow}${this.sub(below)}${above}</munderover>`,
          };
        }
        case "not": {
          const t = this.peek();
          const key = t && (t.t === "cmd" || t.t === "char") ? t.v : "";
          if (has(NEGATED, key)) { this.next(); return { ml: mo(NEGATED[key]) }; }
          return { ml: mo("/") };
        }
        case "textcolor": {
          const c = this.rawGroup().trim();
          const body = this.parseArg();
          return { ml: COLOR_RE.test(c) ? `<mstyle mathcolor="${c}">${body}</mstyle>` : body };
        }
        case "boxed": case "fbox": {
          const body = name === "fbox" ? this.text(this.rawGroup(), "") : this.parseArg();
          return { ml: `<mrow style="border:1px solid;padding:0.15em 0.3em">${body}</mrow>` };
        }
        case "phantom": case "hphantom": case "vphantom":
          return { ml: `<mphantom>${this.parseArg()}</mphantom>` };
        case "smash": case "mathop": case "mathbin": case "mathrel": case "mathord":
          return { ml: this.parseArg() };
        case "hspace": case "hspace*": case "kern": case "mkern": case "hskip": {
          const w = this.rawGroup().trim();
          return { ml: mspace(LENGTH_RE.test(w) ? w : "0.5em") };
        }
        case "mod": return { ml: `<mrow>${mspace("1em")}<mi>mod</mi>${mspace("0.3333em")}</mrow>` };
        case "bmod": return { ml: mo("mod") };
        case "pmod": {
          const arg = this.parseArg();
          return {
            ml: `<mrow>${mspace("1em")}${mo("(", ' stretchy="false"')}<mi>mod</mi>${mspace("0.3333em")}${
              arg}${mo(")", ' stretchy="false"')}</mrow>`,
          };
        }
        case "substack": {
          this.skipSpace();
          if (this.src[this.i] === "{") this.i++;
          return { ml: this.table(this.parseRows("}"), "substack") };
        }
        case "begin": return this.environment();
        case "tag": case "tag*": {
          const label = this.rawGroup();
          return { ml: `<mrow>${mspace("2em")}${this.text(name === "tag" ? `(${label})` : label, "")}</mrow>` };
        }
        case "label": case "hline": case "cline": case "nonumber": case "notag":
        case "limits": case "nolimits": case "strut": case "mathstrut": case "centering":
          if (name === "label" || name === "cline") this.rawGroup();
          return null;
        case "": return null;  // 结尾落单的反斜杠
        default:
          return { ml: `<mtext class="math-unknown">\\${escapeHtml(name)}</mtext>` };
      }
    }

    /* \text{...} 的内容按文字显示：认几个转义，首尾空格换成不折叠的空格 */
    text(raw, style) {
      const s = raw.replace(/\\([%&$#_{} ])/g, "$1").replace(/~/g, "\u00a0").replace(/^ | $/g, "\u00a0");
      return `<mtext${style ? ` style="${style}"` : ""}>${escapeHtml(s)}</mtext>`;
    }

    /* 按 & 和 \\ 切成行列，直到 \end{...}（或 closer，\substack 用 }）。
       顶层也走这里：$$ a &= b \\ &= c $$ 这种没写环境的多行公式照样排成表 */
    parseRows(closer, env) {
      const rows = [[]];
      let cell = [];
      const endCell = () => { rows[rows.length - 1].push(mrow(cell)); cell = []; };
      for (;;) {
        cell.push(...this.parseList());
        const t = this.next();
        if (!t) break;
        if (t.t === "char" && t.v === "&") { endCell(); continue; }
        if (t.t === "cmd" && t.v === "\\") { endCell(); this.rowGap(); rows.push([]); continue; }
        if (t.t === "cmd" && t.v === "end") {
          this.rawGroup();
          if (env) break;
          continue;
        }
        if (t.t === "char" && t.v === "}" && closer === "}") break;
        // 剩下的是落单的 }、\right、\middle：丢掉，接着填当前这一格
        if (t.t === "cmd") this.delimiter();
      }
      endCell();
      // 最后一行 \\ 结尾留下的空行不要
      const last = rows[rows.length - 1];
      if (rows.length > 1 && last.length === 1 && last[0] === "<mrow></mrow>") rows.pop();
      return rows;
    }

    /* 行列拼成 <mtable>。Chrome 不认 columnalign，对齐和间距都写在 mtd 的 style 上 */
    table(rows, kind, { aligns = null, display = kind === "align" || kind === "gather" } = {}) {
      const cellStyle = (k, n) => {
        if (kind === "align") {
          // 右对齐 | 左对齐 成对出现，对与对之间空开
          return k % 2 === 0 ? "text-align:right;padding:0.25em 0"
            : `text-align:left;padding:0.25em ${k < n - 1 ? "2em" : "0"} 0.25em 0`;
        }
        if (kind === "cases") return `text-align:left;padding:0.15em ${k < n - 1 ? "1em" : "0"} 0.15em 0`;
        if (kind === "gather") return "padding:0.25em 0";
        if (kind === "substack") return "padding:0";
        if (aligns && aligns[k]) return `text-align:${aligns[k]}`;
        return "";
      };
      const body = rows.map((cells) => `<mtr>${cells.map((c, k) => {
        const style = cellStyle(k, cells.length);
        const inner = display ? `<mstyle displaystyle="true">${c}</mstyle>` : c;
        return `<mtd${style ? ` style="${style}"` : ""}>${inner}</mtd>`;
      }).join("")}</mtr>`).join("");
      return `<mtable>${body}</mtable>`;
    }

    environment() {
      const name = this.rawGroup().trim();
      const env = name.replace(/\*$/, "");
      let kind = "matrix";
      let aligns = null;
      if (/^(align|aligned|alignat|alignedat|split|eqnarray|flalign)$/.test(env)) kind = "align";
      else if (/^(gather|gathered|equation|multline|displaymath)$/.test(env)) kind = "gather";
      else if (/cases$/.test(env)) kind = "cases";
      if (env === "alignat" || env === "alignedat") this.rawGroup();
      if (env === "array" || env === "subarray") {
        const spec = this.rawGroup().replace(/[^lcr]/g, "");
        aligns = [...spec].map((c) => ({ l: "left", c: "center", r: "right" })[c]);
      }
      const rows = this.parseRows(null, name || "?");
      let ml;
      if (kind === "gather" && rows.length === 1 && rows[0].length === 1) ml = rows[0][0];
      else ml = this.table(rows, kind, env === "dcases" ? { aligns, display: true } : { aligns });
      if (has(ENV_FENCES, env)) {
        const [l, r] = ENV_FENCES[env];
        ml = `<mrow>${fence(l)}${ml}${fence(r)}</mrow>`;
      }
      if (env === "smallmatrix") ml = `<mstyle scriptlevel="1">${ml}</mstyle>`;
      return { ml };
    }

    /* 整条公式：只有一格就是普通的一行，多行 / 多列排成表 */
    parseTable() {
      const rows = this.parseRows(null, null);
      if (rows.length === 1 && rows[0].length === 1) return rows[0][0];
      const cols = Math.max(...rows.map((r) => r.length));
      return this.table(rows, cols > 1 ? "align" : "gather");
    }
  }

  /* tex 是去掉定界符（$ $$ \( \[）之后的公式源码 */
  function texToMathML(tex, display) {
    const src = String(tex ?? "");
    let body;
    try {
      body = new Parser(src).parseTable();
    } catch {
      // 兜底：解析器出了意外（比如嵌套深到爆栈）就原样显示源码
      return `<code class="math-error">${escapeHtml(src)}</code>`;
    }
    return `<math${display ? ' display="block"' : ""}>${body}</math>`;
  }

  root.texToMathML = texToMathML;
  if (typeof module !== "undefined" && module.exports) module.exports = { texToMathML };
})(typeof window !== "undefined" ? window : globalThis);
