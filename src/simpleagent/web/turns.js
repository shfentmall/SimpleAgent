/* 一轮回复怎么分成「过程」和「最终回答」，以及过程里每一步显示什么。
   只算数据，不碰 DOM（画在 app.js），node 里能直接 require 来测（tests/serve/test_turns.py）。

   规则只看先后顺序，不看是哪个执行者：一段文字后面还跟着工具调用，它就是过程里的一句话；
   一轮里最后一次工具调用之后的文字才是回答。流式时同理——文字先当回答显示，
   等后面来了工具调用再挪进过程（Claude Code 的文字和工具调用是分开的两条事件，
   文字到的时候还不知道后面有没有工具调用，只能事后归类）。 */
(function (root) {
  "use strict";

  const asText = (c) => (typeof c === "string" ? c : c == null ? "" : JSON.stringify(c));

  /* 历史里的工具结果不带 is_error，只能按内容猜（和 panel/summary.py 的判断一致，
     再加上 Claude Code 的 <tool_use_error>） */
  function looksLikeError(text) {
    const t = String(text || "").trim();
    return /^(错误|Error|error|<tool_use_error>)/.test(t) || t.includes("Traceback");
  }

  /**
   * 历史消息 → [{ user, steps, answer }]，一条用户消息开一轮。
   *   user：那句话；第一条用户消息之前就有助手消息时为 null
   *   steps：按时间排的过程
   *     { kind: "think", text }                        思考内容
   *     { kind: "text", text }                         过程中说的话
   *     { kind: "tool", id, name, args, result, isError, done }
   *   answer：最后一次工具调用之后的文字，没有就是 ""
   */
  function groupTurns(messages) {
    const turns = [];
    let turn = null;
    const open = (user) => {
      turn = { user, steps: [], answer: "", byId: {} };
      turns.push(turn);
    };
    // 最后一次工具调用之后的文字先攒着：再遇到工具调用就归进过程，一轮结束时就是回答
    const demote = () => {
      if (turn.answer) turn.steps.push({ kind: "text", text: turn.answer });
      turn.answer = "";
    };
    for (const m of messages || []) {
      if (m.role === "user") {
        open(asText(m.content));
        continue;
      }
      if (!turn) open(null);
      if (m.role === "assistant") {
        if (m.reasoning_content) turn.steps.push({ kind: "think", text: m.reasoning_content });
        const text = asText(m.content).trim();
        if (text) turn.answer = turn.answer ? `${turn.answer}\n\n${text}` : text;
        for (const tc of m.tool_calls || []) {
          demote();
          const f = tc.function || {};
          const step = {
            kind: "tool", id: tc.id || "", name: f.name || "tool", args: f.arguments || "",
            result: "", isError: false, done: false,
          };
          turn.steps.push(step);
          if (tc.id) turn.byId[tc.id] = step;
        }
      } else if (m.role === "tool") {
        const result = asText(m.content);
        let step = turn.byId[m.tool_call_id];
        if (!step) {
          // 老会话：外部执行者以前不存工具调用，只有结果——名字、参数都不知道了
          demote();
          step = { kind: "tool", id: m.tool_call_id || "", name: m.name || "工具", args: "" };
          turn.steps.push(step);
        }
        Object.assign(step, { result, isError: looksLikeError(result), done: true });
      }
    }
    for (const t of turns) delete t.byId;
    return turns;
  }

  /* 工具名去掉 MCP 前缀：mcp__filesystem__read_file → filesystem · read_file */
  function toolLabel(name) {
    const parts = String(name || "").split("__");
    if (parts[0] === "mcp" && parts.length >= 3) return `${parts[1]} · ${parts.slice(2).join("__")}`;
    return name || "工具";
  }

  /* 空间目录下的路径显示成相对路径，界面头部已经写着目录了。
     macOS 上 /tmp 是 /private/tmp 的链接，CLI 报出来的路径两种写法都可能有 */
  function relPath(p, cwd) {
    if (typeof p !== "string") return "";
    const base = cwd && String(cwd).replace(/\/+$/, "");
    if (!base) return p;
    const bare = base.replace(/^\/private(?=\/)/, "");
    for (const b of new Set([base, bare, `/private${bare}`])) {
      if (p === b) return ".";
      if (p.startsWith(`${b}/`)) return p.slice(b.length + 1);
    }
    return p;
  }

  const oneLine = (s) => String(s).replace(/\s+/g, " ").trim();

  /**
   * 工具那一行的摘要：只挑最能说明「干了什么」的参数。
   * 内置工具（bash / read_file / grep…）和 Claude Code（Bash / Read / Grep…）两套名字都认。
   */
  function toolBrief(name, argsText, cwd) {
    let a = null;
    try { a = JSON.parse(argsText || "{}"); } catch { /* 不是 JSON 就原样显示 */ }
    if (!a || typeof a !== "object" || Array.isArray(a)) return oneLine(argsText || "").slice(0, 200);
    const path = a.file_path || a.path || a.notebook_path;
    // 搜索范围就是空间目录本身时不用写（「TODO · .」里那个点没有信息量）
    const where = path && relPath(path, cwd) !== "." ? relPath(path, cwd) : "";
    const n = String(name || "").toLowerCase();
    let brief = "";
    if (a.command) brief = a.command;
    else if (n === "grep" || n.endsWith("__grep") || n === "search_files") {
      brief = a.pattern || a.query || "";
      if (where) brief += ` · ${where}`;
    } else if (a.pattern) brief = where ? `${where} · ${a.pattern}` : a.pattern;
    else if (path) brief = relPath(path, cwd);
    else if (Array.isArray(a.todos)) brief = `${a.todos.length} 项`;
    else {
      const first = ["url", "query", "description", "prompt", "task", "name", "skill"]
        .map((k) => a[k]).find((v) => typeof v === "string" && v);
      brief = first || Object.values(a).find((v) => typeof v === "string" && v) || "";
    }
    return oneLine(brief).slice(0, 200);
  }

  /* 工具 → 「做了什么」的归类，折叠条上的汇总用。key 是小写的工具名（MCP 工具取最后一段） */
  const KINDS = [
    [["read_file", "read", "notebookread", "memory_read"], "file-read"],
    [["write_file", "edit_file", "write", "edit", "multiedit", "notebookedit", "memory_write"], "file-write"],
    [["bash"], "bash"],
    [["grep", "search_files"], "grep"],
    [["glob", "list_dir", "ls", "list_directory"], "find"],
    [["web_fetch", "webfetch", "websearch", "web_search"], "web"],
    [["todowrite", "todo"], "todo"],
    [["task", "agent", "dispatch", "followup"], "task"],
  ];
  const KIND_OF = {};
  for (const [names, kind] of KINDS) for (const x of names) KIND_OF[x] = kind;
  const PHRASE = {
    "file-read": (n) => `读了 ${n} 个文件`,
    "file-write": (n) => `改了 ${n} 个文件`,
    bash: (n) => `跑了 ${n} 条命令`,
    grep: (n) => `搜索 ${n} 次`,
    find: (n) => `找文件 ${n} 次`,
    web: (n) => `查了 ${n} 个网页`,
    todo: (n) => `更新待办 ${n} 次`,
    task: (n) => `派了 ${n} 个子任务`,
  };

  /**
   * 折叠条上的一句话：「读了 3 个文件、找文件 1 次」。
   * 读写文件按不同路径计数（同一个文件读两遍算一个），其余按次数；最多列三类，多了加「等」。
   */
  function summarize(steps, cwd) {
    const counts = new Map();   // 归类 -> Set（按路径去重）或次数
    for (const s of steps || []) {
      if (s.kind !== "tool") continue;
      const short = String(s.name || "").toLowerCase().replace(/^mcp__.+__/, "");
      const kind = KIND_OF[short] || `other:${toolLabel(s.name)}`;
      if (kind === "file-read" || kind === "file-write") {
        if (!counts.has(kind)) counts.set(kind, new Set());
        counts.get(kind).add(toolBrief(s.name, s.args, cwd) || s.id);
      } else {
        counts.set(kind, (counts.get(kind) || 0) + 1);
      }
    }
    const parts = [...counts].map(([kind, v]) => {
      const n = typeof v === "number" ? v : v.size;
      if (PHRASE[kind]) return PHRASE[kind](n);
      const label = kind.slice(6);
      return label === "工具" ? `调用工具 ${n} 次` : `调用 ${label} ${n} 次`;  // 老会话不知道工具名
    });
    return parts.length > 3 ? `${parts.slice(0, 3).join("、")} 等` : parts.join("、");
  }

  const api = { groupTurns, toolBrief, toolLabel, summarize, looksLikeError, relPath };
  Object.assign(root, { SATurns: api });
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
