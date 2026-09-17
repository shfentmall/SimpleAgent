/* SimpleAgent 工作台前端。
   原则：零依赖、零构建，原生 DOM + fetch + EventSource。
   客户端不承载业务逻辑：状态在 Python 侧，这里只负责「渲染事件 + 发命令」。 */

const $ = (id) => document.getElementById(id);
const streamEl = () => $("pane-chat");

/* SSE 帧类型。服务端用 `event: <type>` 发送，EventSource 必须按名字监听，
   onmessage 只收默认的 message 事件，收不到带 event 名的帧。 */
const FRAME_TYPES = [
  "text_delta", "reasoning_delta", "message_done", "tool_call_start", "tool_result",
  "approval_request", "verification", "status", "usage", "error", "max_steps", "turn_end",
  "unknown",
];

const LS_KEY = "sa.workbench.current";   // 记住上次打开的会话，刷新后自动回到原位

const state = {
  spaces: [],
  profiles: [],
  executors: [],
  defaultProfile: "",
  spaceId: null,
  sessionId: null,
  messages: [],        // 当前会话的历史，导出 Markdown 用
  es: null,
  lastSeq: 0,
  running: false,
  startedAt: null,     // 本轮开始时间，用于显示已用时
  noReplyTimer: null,  // 兜底：发出去之后一直没有任何帧就提醒
  acc: "",           // 当前助手消息的累积文本
  accEl: null,       // 累积文本渲染到的元素
  streamEl: null,    // 流式时的光标占位容器
  toolCards: new Map(),   // call_id -> { body, toggle }
  showAll: new Set(),     // 展开了「查看全部」的空间 id
  filter: "",
  tab: "chat",            // 当前右栏 tab
};

/* ────────────────────────────── 请求封装 ────────────────────────────── */
async function req(method, path, data) {
  const r = await fetch(path, {
    method,
    headers: data === undefined ? {} : { "Content-Type": "application/json" },
    body: data === undefined ? undefined : JSON.stringify(data),
  });
  if (!r.ok) {
    let msg = `${r.status}`;
    try { msg = (await r.json()).error || msg; } catch { /* 非 JSON 响应就用状态码 */ }
    throw new Error(msg);
  }
  return r.status === 204 ? null : r.json();
}
const api = {
  get: (p) => req("GET", p),
  post: (p, d) => req("POST", p, d),
  patch: (p, d) => req("PATCH", p, d),
  del: (p) => req("DELETE", p),
};

/* ────────────────────────────── 小工具 ────────────────────────────── */
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* 极简 Markdown：先转义再处理，避免注入。只支持代码块、行内代码、段落。 */
function renderText(raw) {
  let s = escapeHtml(raw);
  s = s.replace(/```[\w]*\n([\s\S]*?)```/g, (_, code) => `<pre class="code">${code.replace(/\n$/, "")}</pre>`);
  s = s.replace(/`([^`\n]+)`/g, "<code>$1</code>");
  return s;
}

function relTime(iso) {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "";
  const min = Math.floor((Date.now() - t) / 60000);
  if (min < 1) return "刚刚";
  if (min < 60) return `${min} 分钟前`;
  if (min < 1440) return `${Math.floor(min / 60)} 小时前`;
  if (min < 1440 * 7) return `${Math.floor(min / 1440)} 天前`;
  return new Date(t).toLocaleDateString("zh-CN");
}

function shortPath(p, n = 28) {
  if (!p) return "";
  return p.length > n ? "…" + p.slice(-n) : p;
}

function toast(msg) {
  const el = $("toast");
  el.textContent = msg;
  el.classList.remove("hidden");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.add("hidden"), 2200);
}

const BADGE = { "claude-code": ["CC", "b-cc"], opencode: ["OC", "b-oc"], simpleagent: ["SA", "b-sa"] };
const VMARK = { passed: ["✓", "v-passed"], failed: ["✗", "v-failed"], stale: ["⚠", "v-stale"], running: ["◐", "v-running"], unknown: ["○", "v-unknown"] };
const STATUS_TEXT = { running: "运行中", verifying: "验证中", done: "完成", cancelled: "已取消", error: "出错", idle: "空闲" };

/* 卡片徽标：执行者决定「谁跑」，形态决定「在哪儿跑」。
   内置执行者 + 通用形态就显示「通用」，其余按执行者缩写。 */
function badgeFor(sp) {
  const exec = sp.executor || "simpleagent";
  if (exec === "simpleagent") return sp.kind === "generic" ? ["通用", ""] : ["SA", "b-sa"];
  return BADGE[exec] || ["SA", "b-sa"];
}

function statusDot(s) {
  return s === "running" ? "●" : s === "error" ? "✗" : s === "done" ? "✓" : s === "cancelled" ? "⊘" : "○";
}

function fmtDur(sec) {
  const m = Math.floor(sec / 60);
  return m ? `${m} 分 ${sec % 60} 秒` : `${sec} 秒`;
}

/* ────────────────────────────── 左栏 ────────────────────────────── */
function renderSpaces() {
  const box = $("space-list");
  const filter = state.filter.trim().toLowerCase();
  box.innerHTML = "";
  $("space-count").textContent = `(${state.spaces.length})`;

  if (!state.spaces.length) {
    box.innerHTML = `<div class="empty-sub" style="padding:8px 6px">还没有空间，点上方「+ 新建空间」开始。</div>`;
    return;
  }

  for (const sp of state.spaces) {
    const [label, cls] = badgeFor(sp);
    const dir = sp.cwd || null;
    const danger =
      (sp.executor || "simpleagent") !== "simpleagent" && sp.permission === "full"
        ? `<span class="warn-chip" title="这个空间的外部 agent 不经确认就会改文件、跑命令">全放行</span>`
        : "";
    const vs = sp.sessions || [];
    const vsum = vs.length
      ? `<span class="vsum" title="最近 ${vs.length} 个会话里通过验证的数量">✓ ${
          vs.filter((m) => (m.verification || {}).status === "passed").length
        }/${vs.length}</span>`
      : "";

    const card = document.createElement("div");
    card.className = "space is-open";
    card.innerHTML = `
      <div class="space-head">
        <span class="space-name">${escapeHtml(sp.name)}</span>
        <span class="badge ${cls}">${label}</span>
        ${danger}
        ${vsum}
        <span class="space-actions">
          <button class="icon-btn" title="在这个空间新建会话" data-act="new-session">＋</button>
          <button class="icon-btn" title="关闭（只是不显示，不删数据）" data-act="close">×</button>
        </span>
      </div>
      ${dir ? `<div class="space-dir" title="${escapeHtml(dir)}">${escapeHtml(shortPath(dir))}</div>` : ""}
      <div class="sessions"></div>`;

    const list = card.querySelector(".sessions");
    const sessions = (sp.sessions || []).filter(
      (m) => !filter || (m.title || "").toLowerCase().includes(filter));

    if (!sessions.length) {
      list.innerHTML = `<div class="space-dir" style="padding:2px 6px">没有匹配的会话</div>`;
    }
    for (const m of sessions) {
      const [mk, mcls] = VMARK[(m.verification || {}).status] || VMARK.unknown;
      const row = document.createElement("div");
      row.className = "session" + (m.id === state.sessionId ? " is-active" : "");
      row.innerHTML = `
        <span class="st ${m.status}">${statusDot(m.status)}</span>
        <span class="title" title="${escapeHtml(m.title || "")}">${m.pinned ? "★ " : ""}${escapeHtml(m.title || "新会话")}</span>
        <span class="vmark ${mcls}">${mk}</span>
        <span class="time">${relTime(m.updated_at || m.created_at)}</span>
        <span class="row-actions">
          <button class="icon-btn" data-act="pin" title="${m.pinned ? "取消置顶" : "置顶"}">${m.pinned ? "★" : "☆"}</button>
          <button class="icon-btn" data-act="rename" title="重命名">✎</button>
        </span>`;
      row.onclick = () => selectSession(sp.id, m.id);
      row.querySelector('[data-act="pin"]').onclick = (ev) => {
        ev.stopPropagation();
        api.patch(`/api/sessions/${m.id}`, { pinned: !m.pinned })
          .then(loadSpaces)
          .catch((e) => toast(e.message));
      };
      row.querySelector('[data-act="rename"]').onclick = (ev) => {
        ev.stopPropagation();
        startRename(row, m);
      };
      list.appendChild(row);
    }

    if (!state.showAll.has(sp.id)) {
      const more = document.createElement("div");
      more.className = "more";
      more.textContent = "查看全部";
      more.onclick = async () => {
        state.showAll.add(sp.id);
        try {
          const all = await api.get(`/api/spaces/${sp.id}/sessions?limit=50`);
          sp.sessions = all;
          sp._total = all.length;
          renderSpaces();
        } catch (e) { toast(`加载失败：${e.message}`); }
      };
      list.appendChild(more);
    }

    card.querySelector('[data-act="new-session"]').onclick = (ev) => {
      ev.stopPropagation();
      newSession(sp.id);
    };
    card.querySelector('[data-act="close"]').onclick = async (ev) => {
      ev.stopPropagation();
      await api.patch(`/api/spaces/${sp.id}`, { opened: false }).catch((e) => toast(e.message));
      await loadSpaces();
    };
    box.appendChild(card);
  }
}

/* 行内重命名：标题换成输入框，回车保存 / Esc 取消。
   不用 prompt()：那是浏览器原生弹窗，出现在这个界面里很跳。 */
function startRename(row, meta) {
  const titleEl = row.querySelector(".title");
  if (row.querySelector("input")) return;
  const input = document.createElement("input");
  input.className = "rename";
  input.value = meta.title || "";
  titleEl.replaceWith(input);
  input.focus();
  input.select();

  let done = false;
  const finish = async (save) => {
    if (done) return;
    done = true;
    const next = input.value.trim();
    if (save && next && next !== meta.title) {
      try {
        await api.patch(`/api/sessions/${meta.id}`, { title: next });
        meta.title = next;
      } catch (e) { toast(e.message); }
    }
    renderSpaces();
  };
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); finish(true); }
    if (e.key === "Escape") { e.preventDefault(); finish(false); }
  });
  input.addEventListener("blur", () => finish(true));
}

/* ────────────────────────────── 右栏头部 ────────────────────────────── */
function renderHeader() {
  const sp = state.spaces.find((s) => s.id === state.spaceId);
  const m = sp && (sp.sessions || []).find((x) => x.id === state.sessionId);
  $("ws-crumb").textContent = m ? `${sp.name} / ${m.title || "新会话"}` : (sp ? sp.name : "未选择会话");

  const badge = $("ws-badge");
  if (sp) {
    const [label, cls] = badgeFor(sp);
    badge.className = `badge ${cls}`;
    badge.textContent = label;
    badge.title =
      (sp.executor || "simpleagent") !== "simpleagent" && sp.permission === "full"
        ? "这个空间的外部 agent 不经确认就会改文件、跑命令"
        : "";
    badge.classList.remove("hidden");
  } else {
    badge.classList.add("hidden");
  }

  const dir = (sp && sp.cwd) || "";
  $("ws-dir").textContent = shortPath(dir, 46);
  $("ws-dir").title = dir || "";

  const v = (m && m.verification) || {};
  const vc = $("ws-verify");
  if (m && v.status && v.status !== "unknown") {
    const text = { passed: "已验证 ✓", failed: "未通过 ✗", stale: "已失效 ⚠", running: "验证中 ◐" }[v.status];
    vc.textContent = text || v.status;
    vc.className = `chip c-${v.status}`;
    vc.classList.remove("hidden");
  } else {
    vc.classList.add("hidden");
  }

  const u = (m && m.usage) || {};
  const total = (u.prompt_tokens || 0) + (u.completion_tokens || 0);
  const uc = $("ws-usage");
  if (total) {
    uc.textContent = `${total.toLocaleString()} tokens`;
    uc.className = "chip";
    uc.classList.remove("hidden");
  } else {
    uc.classList.add("hidden");
  }

  $("btn-new-session").disabled = !sp;
  $("btn-rerun").disabled = !m || state.running;
  $("btn-export").disabled = !m;
  $("btn-verify").disabled = !m;
  $("btn-stop").disabled = !state.running;
  $("input").disabled = !m || state.running;
  $("btn-send").disabled = !m || state.running;
  const ps = $("profile");
  const external = !!sp && (sp.executor || "simpleagent") !== "simpleagent";
  ps.disabled = !sp || external;
  if (external) {
    // 外部 agent 的模型由它自己的配置决定，这里没有可切的东西，别放假选项骗人
    ps.innerHTML = `<option value="">本机默认（不注入配置）</option>`;
    ps.title = "这个空间绑的是外部 agent，模型由它自己的配置决定";
  } else {
    ps.innerHTML = state.profiles.map((p) => `<option value="${p}">${p}</option>`).join("");
    if (sp) ps.value = sp.profile;
    ps.title = "内置执行者的模型 profile";
  }
}

/* ────────────────────────────── 消息流 ────────────────────────────── */
function clearStream() {
  const box = streamEl();
  box.innerHTML = "";
  state.toolCards.clear();
  state.acc = "";
  state.accEl = null;
  state.streamEl = null;
}

function addUserBubble(text) {
  const box = streamEl();
  $("empty-state")?.remove();
  const el = document.createElement("div");
  el.className = "msg user";
  el.innerHTML = `<div class="avatar">你</div><div class="body"><div class="who">用户</div><div class="text"></div></div>`;
  el.querySelector(".text").textContent = text;
  box.appendChild(el);
  scrollDown();
}

function ensureAssistantBubble() {
  if (state.accEl) return state.accEl;
  const box = streamEl();
  $("empty-state")?.remove();
  const el = document.createElement("div");
  el.className = "msg assistant";
  el.innerHTML = `<div class="avatar">AI</div><div class="body"><div class="who">助手</div><div class="text"></div></div>`;
  box.appendChild(el);
  state.accEl = el.querySelector(".text");
  state.streamEl = el;
  return state.accEl;
}

function finishAssistantBubble() {
  state.acc = "";
  state.accEl = null;
  state.streamEl = null;
}

function addToolCard(name, argsText, callId) {
  const box = streamEl();
  $("empty-state")?.remove();
  const card = document.createElement("div");
  card.className = "card";
  card.innerHTML = `
    <div class="card-head"><span class="name">${escapeHtml(name)}</span>
      <span class="args">${escapeHtml(shortPath(argsText, 60))}</span>
      <span class="tail">展开</span></div>
    <div class="card-body hidden"></div>`;
  const body = card.querySelector(".card-body");
  const tail = card.querySelector(".tail");
  card.querySelector(".card-head").onclick = () => {
    body.classList.toggle("hidden");
    tail.textContent = body.classList.contains("hidden") ? "展开" : "收起";
  };
  box.appendChild(card);
  if (callId) state.toolCards.set(callId, { card, body });
  scrollDown();
  return { card, body };
}

function addErrorCard(text) {
  const box = streamEl();
  $("empty-state")?.remove();
  const el = document.createElement("div");
  el.className = "card is-error";
  el.textContent = text;
  box.appendChild(el);
  scrollDown();
}

function addApprovalCard(approvalId, toolName, argsText, reason) {
  const box = streamEl();
  const el = document.createElement("div");
  el.className = "card is-approval";
  el.dataset.approval = approvalId;
  el.innerHTML = `
    <div><b>需要批准</b> · <code>${escapeHtml(toolName)}</code></div>
    ${reason ? `<div class="card-body">${escapeHtml(reason)}</div>` : ""}
    <div class="card-body">${escapeHtml(argsText)}</div>
    <div style="margin-top:8px;display:flex;gap:6px">
      <button class="btn btn-primary" data-act="allow">允许</button>
      <button class="btn" data-act="always">本次会话始终允许</button>
      <button class="btn" data-act="deny">拒绝</button>
    </div>`;
  el.querySelectorAll("button").forEach((b) => {
    b.onclick = async () => {
      try {
        await api.post(`/api/approvals/${approvalId}`, { action: b.dataset.act });
        el.querySelectorAll("button").forEach((x) => (x.disabled = true));
        el.querySelector("div").textContent = `已处理：${b.textContent}`;
      } catch (e) { toast(`提交失败：${e.message}`); }
    };
  });
  box.appendChild(el);
  scrollDown();
}

/* 只在用户本来就贴着底部时才自动滚动：否则翻看上面内容时会被流式输出一直拽回去 */
function scrollDown(force) {
  const box = streamEl();
  const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 120;
  if (force || nearBottom) box.scrollTop = box.scrollHeight;
}

/* 历史消息 → 气泡。assistant 带 tool_calls 时画成工具卡，tool 消息回填到对应卡片。 */
function renderHistory(messages) {
  clearStream();
  const box = streamEl();
  if (!messages.length) {
    box.innerHTML = `<div class="empty"><div class="empty-title">新会话</div>
      <div class="empty-sub">在下面输入第一句话。</div></div>`;
    return;
  }
  for (const m of messages) {
    if (m.role === "user") {
      addUserBubble(typeof m.content === "string" ? m.content : JSON.stringify(m.content));
    } else if (m.role === "assistant") {
      if (m.content) {
        const el = document.createElement("div");
        el.className = "msg assistant";
        el.innerHTML = `<div class="avatar">AI</div><div class="body"><div class="who">助手</div>
          <div class="text">${renderText(m.content)}</div></div>`;
        box.appendChild(el);
      }
      for (const tc of m.tool_calls || []) {
        const args = tc.function ? tc.function.arguments : "";
        addToolCard(tc.function ? tc.function.name : "tool", args, tc.id);
      }
    } else if (m.role === "tool") {
      const hit = state.toolCards.get(m.tool_call_id);
      const text = typeof m.content === "string" ? m.content : JSON.stringify(m.content);
      if (hit) {
        hit.body.textContent = text;
        hit.body.classList.remove("hidden");
      } else {
        addToolCard(m.name || "tool", "", m.tool_call_id).body.textContent = text;
      }
    }
  }
  scrollDown();
}

/* ────────────────────────────── SSE ────────────────────────────── */
function closeStream() {
  if (state.es) { state.es.close(); state.es = null; }
}

function subscribe(sessionId) {
  closeStream();
  state.lastSeq = 0;
  const es = new EventSource(`/api/sessions/${sessionId}/events`);
  state.es = es;
  for (const t of FRAME_TYPES) {
    es.addEventListener(t, (ev) => {
      try { onFrame(t, JSON.parse(ev.data)); } catch { /* 坏帧忽略 */ }
    });
  }
}

async function onFrame(type, frame) {
  // 收到任何一帧就说明服务端活着，撤掉「无响应」提醒
  if (state.noReplyTimer) { clearTimeout(state.noReplyTimer); state.noReplyTimer = null; }
  if (frame.seq && frame.seq <= state.lastSeq) return;  // 断线重放去重
  if (frame.seq) state.lastSeq = frame.seq;
  const p = frame.payload || {};

  if (type === "text_delta") {
    state.acc += p.text || "";
    const el = ensureAssistantBubble();
    el.innerHTML = renderText(state.acc) + '<span class="cursor">&nbsp;</span>';
    scrollDown();
  } else if (type === "reasoning_delta") {
    let d = state.streamEl && state.streamEl.querySelector("details.reasoning");
    if (!d) {
      ensureAssistantBubble();
      d = document.createElement("details");
      d.className = "reasoning";
      d.innerHTML = `<summary>思考过程</summary><div></div>`;
      state.streamEl.querySelector(".body").appendChild(d);
    }
    d.querySelector("div").textContent += p.text || "";
  } else if (type === "tool_call_start") {
    finishAssistantBubble();
    const args = typeof p.arguments === "string" ? p.arguments : JSON.stringify(p.arguments);
    addToolCard(p.name, args, p.call_id);
  } else if (type === "tool_result") {
    const hit = state.toolCards.get(p.call_id);
    if (hit) {
      hit.body.textContent = p.content || "";
      hit.body.classList.remove("hidden");
      hit.card.classList.toggle("is-error", !!p.is_error);
    } else {
      addToolCard(p.name || "tool", "", p.call_id).body.textContent = p.content || "";
    }
    scrollDown();
  } else if (type === "message_done") {
    if (state.accEl) state.accEl.innerHTML = renderText(state.acc);
    finishAssistantBubble();
    if (p.usage) updateUsage(p.usage);
    await refreshSessions();
  } else if (type === "status") {
    state.running = p.status === "running" || p.status === "verifying";
    if (state.running) {
      if (!state.startedAt) state.startedAt = Date.now();
      $("status-hint").textContent = STATUS_TEXT[p.status] || p.status;
    } else {
      const sec = state.startedAt ? Math.floor((Date.now() - state.startedAt) / 1000) : null;
      state.startedAt = null;
      $("status-hint").textContent = sec !== null && p.status === "done"
        ? `${STATUS_TEXT[p.status]}，用时 ${fmtDur(sec)}`
        : (STATUS_TEXT[p.status] || p.status);
    }
    renderHeader();
    if (p.status !== "running") await refreshSessions();
  } else if (type === "error") {
    state.running = false;
    state.startedAt = null;
    addErrorCard(p.message || "出错了");
    renderHeader();
  } else if (type === "max_steps") {
    addErrorCard(`达到最大步数（${p.max_steps}）已停止。`);
  } else if (type === "approval_request") {
    addApprovalCard(p.approval_id, p.tool_name, p.arguments || "", p.reason);
  } else if (type === "verification") {
    await refreshSessions();
  } else if (type === "usage") {
    updateUsage(p);
  }
}

function updateUsage(u) {
  const total = (u.prompt_tokens || 0) + (u.completion_tokens || 0);
  if (!total) return;
  const el = $("ws-usage");
  el.textContent = `${total.toLocaleString()} tokens`;
  el.className = "chip";
  el.classList.remove("hidden");
}

/* ────────────────────────────── 交互 ────────────────────────────── */
async function loadSpaces() {
  state.spaces = await api.get("/api/spaces");
  renderSpaces();
  renderHeader();
}

async function refreshSessions() {
  // 只刷左侧列表，不重画消息流（否则会打断正在看的上下文）
  try {
    const spaces = await api.get("/api/spaces");
    const merged = new Map(spaces.map((s) => [s.id, s]));
    state.spaces = state.spaces.map((s) => {
      const fresh = merged.get(s.id);
      if (!fresh) return s;
      return state.showAll.has(s.id) ? { ...s, ...fresh, sessions: s.sessions } : fresh;
    });
    renderSpaces();
    renderHeader();
  } catch { /* 刷新失败不影响当前会话 */ }
}

/* 补出「没收到那一帧」的审批卡：SSE 首次连接不重放，所以晚连上来的客户端
   （切到某个会话、或者控制面板下发的任务）必须主动拉一次待审批列表。 */
async function renderPendingApprovals(sessionId) {
  if (!sessionId) return;
  let pending = [];
  try {
    pending = (await api.get("/api/approvals")).pending || [];
  } catch { return; }
  for (const p of pending) {
    if (p.session_id !== sessionId) continue;
    if (document.querySelector(`[data-approval="${p.approval_id}"]`)) continue;
    addApprovalCard(p.approval_id, p.tool_name, p.arguments || "", p.reason);
  }
}

async function selectSession(spaceId, sessionId) {
  if (panelState.open) closePanel();
  const sp = state.spaces.find((s) => s.id === spaceId);
  const meta = sp && (sp.sessions || []).find((x) => x.id === sessionId);
  state.spaceId = spaceId;
  state.sessionId = sessionId;
  // 切回来时如果它其实还在跑（比如刷新了页面），按落盘的状态恢复「运行中」
  state.running = !!meta && meta.status === "running";
  state.startedAt = state.running ? Date.now() : null;
  $("status-hint").textContent = state.running ? "运行中…" : "";
  renderSpaces();
  renderHeader();
  try {
    const data = await api.get(`/api/sessions/${sessionId}`);
    state.messages = data.messages || [];
    renderHistory(state.messages);
    scrollDown(true);
  } catch (e) {
    addErrorCard(`加载会话失败：${e.message}`);
  }
  subscribe(sessionId);
  await renderPendingApprovals(sessionId);
  // 停在非对话 tab 时切换会话，面板内容要跟着换
  if (state.tab && state.tab !== "chat") await switchTab(state.tab);
  try {
    localStorage.setItem(LS_KEY, JSON.stringify({ spaceId, sessionId }));
  } catch { /* 隐私模式下写不了，忽略 */ }
}

/* ─────────────────────── 控制面板（W5） ─────────────────────── */
const panelState = {
  open: false,
  summary: null,
  dispatch: [],      // 本次打开面板后下发的任务
  inbox: [],
  todos: [],
  inboxUnreadOnly: false,
  pollTimer: null,
  mentions: { open: false, items: [], index: 0, from: 0 },
};

async function openPanel() {
  panelState.open = true;
  $("ws-header").classList.add("hidden");
  $("tabs").classList.add("hidden");
  $("composer").classList.add("hidden");
  for (const id of Object.values(PANES)) $(id).classList.add("hidden");
  $("pane-panel").classList.remove("hidden");
  $("nav-panel").classList.add("is-active-nav");
  await loadPanel();
  if (!panelState.pollTimer) panelState.pollTimer = setInterval(pollDispatch, 3000);
}

function closePanel() {
  panelState.open = false;
  if (panelState.pollTimer) { clearInterval(panelState.pollTimer); panelState.pollTimer = null; }
  state.tab = "chat";  // 从面板跳走时总是回到对话视图：那里才有完整上下文
  $("ws-header").classList.remove("hidden");
  $("tabs").classList.remove("hidden");
  $("composer").classList.toggle("hidden", state.tab !== "chat");
  $("pane-panel").classList.add("hidden");
  for (const [key, id] of Object.entries(PANES)) $(id).classList.toggle("hidden", key !== state.tab);
  $("nav-panel").classList.remove("is-active-nav");
}

async function loadPanel() {
  const [summary, inbox, todos] = await Promise.all([
    api.get("/api/panel/summary"),
    api.get(`/api/inbox?limit=50${panelState.inboxUnreadOnly ? "&unread=1" : ""}`),
    api.get("/api/todos"),
  ]);
  panelState.summary = summary;
  panelState.inbox = inbox;
  panelState.todos = todos;
  renderStats();
  renderInbox();
  renderTodos();
  renderDispatch();
}

function renderStats() {
  const s = panelState.summary || {};
  const cells = [
    ["运行中", s.running ? s.running.length : 0],
    ["未读消息", s.unread || 0],
    ["待办", s.todos || 0],
    ["累计 tokens", (s.today_tokens || 0).toLocaleString()],
  ];
  $("panel-stats").innerHTML = cells.map(([k, v]) =>
    `<div class="panel-stat"><div class="k">${k}</div><div class="v">${escapeHtml(String(v))}</div></div>`).join("");
}

/* ── 指挥台 ── */
/* 解析 `@空间名 任务描述`：先精确匹配，再前缀，最后包含匹配。
   没有 rest 就是「只查状态」，不下发。 */
function parseTarget(text) {
  const m = text.match(/@([^\s@]+)/);
  if (!m) return null;
  const name = m[1];
  const lower = name.toLowerCase();
  const space =
    state.spaces.find((s) => s.name === name)
    || state.spaces.find((s) => s.name.toLowerCase().startsWith(lower))
    || state.spaces.find((s) => s.name.toLowerCase().includes(lower));
  return { token: m[0], name, space, rest: text.replace(m[0], "").trim() };
}

async function renderDispatch() {
  const box = $("dispatch-list");
  if (!panelState.dispatch.length) {
    box.innerHTML = `<div class="empty-sub" style="padding:8px">还没有下发任务。试试「@${escapeHtml(
      (state.spaces[0] || {}).name || "空间名")} 把 tests/ 下重复 fixture 提出来」</div>`;
    return;
  }
  box.innerHTML = panelState.dispatch.map((d, i) => {
    const s = d.summary || {};
    const ap = d.approval;
    const cls = ap ? "error" : (s.status || "idle");
    const when = d.finishedAt ? relTime(d.finishedAt) : fmtDur(Math.floor((Date.now() - d.startedAt) / 1000));
    return `<div class="dispatch ${cls}" data-i="${i}">
      <div class="row1"><span class="dot ${cls}"></span>
        <span class="who">${escapeHtml(d.spaceName)}</span>
        <span class="when">${escapeHtml(when)}</span></div>
      <div class="row2">${escapeHtml(s.title || "新会话")} · ${escapeHtml(
        ap ? `等待批准 ${ap.tool_name}` : (cls === "running" ? "运行中…" : (s.line || STATUS_TEXT[cls] || cls)))}</div>
      ${ap ? `<div class="row3">${escapeHtml(ap.arguments || "")}</div>
        <div class="dispatch-actions">
          <button class="btn btn-mini" data-act="ap-allow">允许</button>
          <button class="btn btn-mini" data-act="ap-always">始终允许</button>
          <button class="btn btn-mini" data-act="ap-deny">拒绝</button>
        </div>` : ""}
      ${!ap && s.last_text ? `<div class="row3">${escapeHtml(s.last_text)}</div>` : ""}
    </div>`;
  }).join("");
  box.querySelectorAll(".dispatch").forEach((el) => {
    const d = panelState.dispatch[Number(el.dataset.i)];
    el.querySelectorAll("[data-act^='ap-']").forEach((btn) => {
      btn.onclick = async (ev) => {
        ev.stopPropagation();
        await api.post(`/api/approvals/${d.approval.approval_id}`,
          { action: btn.dataset.act.replace("ap-", "") });
        d.approval = null;
        renderDispatch();
        toast("已处理");
      };
    });
    el.onclick = async () => {
      const d = panelState.dispatch[Number(el.dataset.i)];
      closePanel();
      await selectSession(d.spaceId, d.sessionId);
    };
  });
}

async function pollDispatch() {
  let changed = false;
  // 先看有没有卡在审批上的：面板上不显示的话，任务会一直挂着没人管
  let pending = [];
  try {
    pending = (await api.get("/api/approvals")).pending || [];
  } catch { /* 拿不到就当没有，下一轮再试 */ }
  for (const d of panelState.dispatch) {
    const ap = pending.find((p) => p.session_id === d.sessionId);
    if (ap && !d.approval) { d.approval = ap; changed = true; }
    if (!ap && d.approval) { d.approval = null; changed = true; }
    if (d.done) continue;
    try {
      const s = await api.get(`/api/sessions/${d.sessionId}/summary`);
      const wasStatus = (d.summary || {}).status;
      d.summary = s;
      if (s.status !== "running" && s.status !== "idle") {
        d.done = true;
        d.finishedAt = new Date().toISOString();
        changed = true;  // 刚结束：顺带把新产生的系统消息拉进来
      } else if (wasStatus !== s.status) {
        changed = true;
      }
    } catch { /* 单个拉取失败不影响其它卡片 */ }
  }
  renderDispatch();
  if (changed) {
    await loadInbox();
    await loadStatsOnly();
  }
}

async function loadInbox() {
  panelState.inbox = await api.get(
    `/api/inbox?limit=50${panelState.inboxUnreadOnly ? "&unread=1" : ""}`);
  renderInbox();
}

async function loadStatsOnly() {
  panelState.summary = await api.get("/api/panel/summary");
  renderStats();
}

/* 下发一条：@空间名 有任务描述就新建 session 跑；只 @ 就回一张状态卡 */
async function dispatch() {
  const input = $("dispatch-input");
  const text = input.value.trim();
  if (!text) return;
  const t = parseTarget(text);
  if (!t) {
    $("dispatch-hint").textContent = "要用 @空间名 开头，例如 @临时整理 清理下载目录";
    return;
  }
  if (!t.space) {
    $("dispatch-hint").textContent = `没有叫「${t.name}」的空间`;
    return;
  }
  input.value = "";
  $("dispatch-hint").textContent = "";

  if (!t.rest) {
    // 只 @：查这个空间最近一个 session 的状态，不新建
    const metas = await api.get(`/api/spaces/${t.space.id}/sessions?limit=1`);
    const m = metas[0];
    panelState.dispatch.unshift({
      spaceId: t.space.id,
      spaceName: t.space.name,
      sessionId: m ? m.id : null,
      startedAt: Date.now(),
      done: !m || m.status !== "running",
      finishedAt: m ? m.updated_at : null,
      summary: m
        ? { title: m.title, status: m.status, line: STATUS_TEXT[m.status] || m.status }
        : { title: "这个空间还没有会话", status: "idle", line: "—" },
    });
    renderDispatch();
    return;
  }

  const meta = await api.post(`/api/spaces/${t.space.id}/sessions`, {});
  await api.post(`/api/sessions/${meta.id}/input`, { text: t.rest });
  panelState.dispatch.unshift({
    spaceId: t.space.id,
    spaceName: t.space.name,
    sessionId: meta.id,
    startedAt: Date.now(),
    done: false,
    summary: { title: t.rest.slice(0, 40), status: "running" },
  });
  renderDispatch();
}

/* ── 消息 ── */
function renderInbox() {
  const box = $("inbox-list");
  $("inbox-unread").textContent = String((panelState.summary || {}).unread || 0);
  if (!panelState.inbox.length) {
    box.innerHTML = `<div class="empty-sub" style="padding:8px">暂时没有消息。</div>`;
    return;
  }
  box.innerHTML = panelState.inbox.map((m) => `
    <div class="inbox-item ${m.read ? "" : "is-unread"}" data-id="${escapeHtml(m.id)}">
      <span class="inbox-bar ${escapeHtml(m.level)}"></span>
      <div style="flex:1;min-width:0">
        <div class="t">${escapeHtml(m.title)}</div>
        ${m.body ? `<div class="b">${escapeHtml(m.body)}</div>` : ""}
      </div>
      <span class="w">${escapeHtml(relTime(m.ts))}</span>
      <span class="todo-tag" data-act="todo" title="转为备忘">+备忘</span>
    </div>`).join("");

  box.querySelectorAll(".inbox-item").forEach((el) => {
    const item = panelState.inbox.find((m) => m.id === el.dataset.id);
    el.querySelector('[data-act="todo"]').onclick = async (ev) => {
      ev.stopPropagation();
      await api.post("/api/todos", { text: item.title, kind: "session", ref: item.ref || {} });
      await loadPanel();
      toast("已加入备忘");
    };
    el.onclick = async () => {
      if (!item.read) {
        await api.post(`/api/inbox/${item.id}/read`, {});
        item.read = true;
      }
      const sid = (item.ref || {}).session_id;
      const spid = (item.ref || {}).space_id;
      if (sid && spid) { closePanel(); await selectSession(spid, sid); return; }
      renderInbox();
      await loadStatsOnly();
    };
  });
}

/* ── 备忘 ── */
function renderTodos() {
  const box = $("todo-list");
  if (!panelState.todos.length) {
    box.innerHTML = `<div class="empty-sub" style="padding:6px">还没有备忘。</div>`;
    return;
  }
  box.innerHTML = panelState.todos.map((t) => `
    <div class="todo-item ${t.done ? "is-done" : ""}" data-id="${escapeHtml(t.id)}">
      <span class="todo-check" data-act="toggle"></span>
      <span class="todo-text">${escapeHtml(t.text)}</span>
      ${t.kind === "session" ? `<span class="todo-tag" data-act="goto">跳到会话</span>` : ""}
      <span class="todo-del" data-act="del">×</span>
    </div>`).join("");

  box.querySelectorAll(".todo-item").forEach((el) => {
    const item = panelState.todos.find((t) => t.id === el.dataset.id);
    el.querySelector('[data-act="toggle"]').onclick = async () => {
      await api.patch(`/api/todos/${item.id}`, { done: !item.done });
      await loadPanel();
    };
    el.querySelector('[data-act="del"]').onclick = async () => {
      await fetch(`/api/todos/${item.id}`, { method: "DELETE" });
      await loadPanel();
    };
    const goto = el.querySelector('[data-act="goto"]');
    if (goto) {
      goto.onclick = async () => {
        const spid = (item.ref || {}).space_id;
        const sid = (item.ref || {}).session_id;
        if (spid && sid) { closePanel(); await selectSession(spid, sid); }
      };
    }
  });
}

async function addTodoInline() {
  const box = $("todo-list");
  if (box.querySelector("input")) return;
  const input = document.createElement("input");
  input.className = "rename";
  input.placeholder = "写点什么，回车保存";
  box.prepend(input);
  input.focus();
  let done = false;
  const finish = async (save) => {
    if (done) return;
    done = true;
    const text = input.value.trim();
    if (save && text) await api.post("/api/todos", { text });
    await loadPanel();
  };
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); finish(true); }
    if (e.key === "Escape") { e.preventDefault(); finish(false); }
  });
  input.addEventListener("blur", () => finish(true));
}

/* ── @ 补全 ── */
function updateMentions() {
  const ta = $("dispatch-input");
  const host = $("dispatch-hint").parentElement;
  host.querySelectorAll(".mentions").forEach((el) => el.remove());
  const upto = ta.value.slice(0, ta.selectionStart);
  const m = upto.match(/@([^\s@]*)$/);
  if (!m) { panelState.mentions.open = false; return; }

  const q = m[1].toLowerCase();
  const hits = state.spaces.filter((s) => s.name.toLowerCase().includes(q)).slice(0, 8);
  if (!hits.length) { panelState.mentions.open = false; return; }

  panelState.mentions = { open: true, items: hits, index: 0, from: upto.length - m[0].length };
  host.style.position = "relative";
  const menu = document.createElement("div");
  menu.className = "mentions";
  menu.innerHTML = hits.map((s, i) => `
    <div class="mention ${i === 0 ? "is-active" : ""}" data-i="${i}">
      <span class="badge ${badgeFor(s)[1]}">${badgeFor(s)[0]}</span>
      ${escapeHtml(s.name)}
    </div>`).join("");
  host.appendChild(menu);
  menu.querySelectorAll(".mention").forEach((el) => {
    el.onmousedown = (ev) => { ev.preventDefault(); applyMention(Number(el.dataset.i)); };
  });
}

function applyMention(i) {
  const hit = panelState.mentions.items[i];
  if (!hit) return;
  const ta = $("dispatch-input");
  const before = ta.value.slice(0, panelState.mentions.from);
  const after = ta.value.slice(ta.selectionStart);
  ta.value = `${before}@${hit.name} ${after}`;
  panelState.mentions.open = false;
  ta.focus();
  updateMentions();
}

const PANES = { chat: "pane-chat", changes: "pane-changes", files: "pane-files", logs: "pane-logs" };

async function switchTab(name) {
  state.tab = name;
  document.querySelectorAll(".tab").forEach((t) => {
    t.classList.toggle("is-active", t.dataset.tab === name);
  });
  for (const [key, id] of Object.entries(PANES)) $(id).classList.toggle("hidden", key !== name);
  $("composer").classList.toggle("hidden", name !== "chat");
  // 按需渲染：切过去时才拉最新数据，不必每收到一帧就重算
  if (name === "changes") await renderChanges();
  if (name === "files") await renderFiles();
  if (name === "logs") await renderLogs();
}

/* 变更：把消息流里 write_file / edit_file 的调用和它的结果配对 */
function collectChanges(messages) {
  const results = new Map();
  for (const m of messages) if (m.role === "tool") results.set(m.tool_call_id, m.content || "");
  const changes = [];
  for (const m of messages) {
    for (const tc of m.tool_calls || []) {
      const name = (tc.function || {}).name;
      if (name !== "write_file" && name !== "edit_file") continue;
      let args = {};
      try { args = JSON.parse((tc.function || {}).arguments || "{}"); } catch { args = {}; }
      changes.push({ name, path: args.path || "?", result: results.get(tc.id) || "" });
    }
  }
  return changes;
}

async function latestMessages() {
  if (!state.sessionId) return [];
  const data = await api.get(`/api/sessions/${state.sessionId}`);
  state.messages = data.messages || [];
  return state.messages;
}

async function renderChanges() {
  const pane = $(PANES.changes);
  if (!state.sessionId) {
    pane.innerHTML = `<div class="empty"><div class="empty-sub">先选一个会话。</div></div>`;
    return;
  }
  let changes;
  try {
    changes = collectChanges(await latestMessages());
  } catch (e) {
    pane.innerHTML = `<div class="card is-error">加载失败：${escapeHtml(e.message)}</div>`;
    return;
  }
  const badge = $("changes-count");
  badge.textContent = changes.length ? ` ${changes.length}` : "";
  badge.classList.toggle("hidden", !changes.length);
  if (!changes.length) {
    pane.innerHTML = `<div class="empty"><div class="empty-title">没有文件变更</div>
      <div class="empty-sub">这个会话还没有写过文件。</div></div>`;
    return;
  }
  pane.innerHTML = changes.map((c, i) => `
    <div class="card">
      <div class="card-head"><span class="name">${c.name === "write_file" ? "写入" : "修改"}</span>
        <span class="args">${escapeHtml(c.path)}</span><span class="tail">#${i + 1}</span></div>
      <div class="card-body">${escapeHtml(c.result.slice(0, 4000)) || "（无输出）"}</div>
    </div>`).join("");
}

async function renderFiles() {
  const pane = $(PANES.files);
  if (!state.spaceId) {
    pane.innerHTML = `<div class="empty"><div class="empty-sub">先选一个空间。</div></div>`;
    return;
  }
  let data;
  try {
    data = await api.get(`/api/spaces/${state.spaceId}/files`);
  } catch (e) {
    pane.innerHTML = `<div class="card is-error">加载失败：${escapeHtml(e.message)}</div>`;
    return;
  }
  const render = (nodes) => `<ul class="tree">${nodes.map((n) => `
      <li><span class="tree-node ${n.type}">${n.type === "dir" ? "▸" : "·"} ${escapeHtml(n.name)}</span>
      ${n.children && n.children.length ? render(n.children) : ""}</li>`).join("")}</ul>`;
  pane.innerHTML = `<div class="ws-dir" style="margin-bottom:6px">${escapeHtml(data.cwd)}</div>
    ${data.tree.length ? render(data.tree) : `<div class="empty-sub">目录是空的。</div>`}`;
}

async function renderLogs() {
  const pane = $(PANES.logs);
  if (!state.sessionId) {
    pane.innerHTML = `<div class="empty"><div class="empty-sub">先选一个会话。</div></div>`;
    return;
  }
  await refreshSessions();  // 拿最新的 usage / verification
  const sp = state.spaces.find((s) => s.id === state.spaceId);
  const m = sp && (sp.sessions || []).find((x) => x.id === state.sessionId);
  if (!m) {
    pane.innerHTML = `<div class="empty"><div class="empty-sub">找不到这个会话。</div></div>`;
    return;
  }
  const u = m.usage || {};
  const v = m.verification || {};
  const bad = state.messages.filter(
    (x) => x.role === "tool" && /error|traceback/i.test(String(x.content))).length;
  pane.innerHTML = `
    <div class="card"><div class="card-head"><span class="name">会话</span></div>
      <div class="card-body">id：${escapeHtml(m.id)}
创建：${escapeHtml(m.created_at || "-")}
更新：${escapeHtml(m.updated_at || "-")}
状态：${escapeHtml(STATUS_TEXT[m.status] || m.status)}
消息：${state.messages.length} 条（其中疑似报错的工具结果 ${bad} 条）</div></div>
    <div class="card"><div class="card-head"><span class="name">用量</span></div>
      <div class="card-body">prompt：${(u.prompt_tokens || 0).toLocaleString()}
completion：${(u.completion_tokens || 0).toLocaleString()}
缓存命中：${(u.cached_tokens || 0).toLocaleString()}
思考：${(u.reasoning_tokens || 0).toLocaleString()}</div></div>
    <div class="card"><div class="card-head"><span class="name">验证</span>
        <span class="tail">${(VMARK[v.status] || VMARK.unknown)[0]} ${escapeHtml(v.status || "unknown")}</span></div>
      <div class="card-body">命令：${escapeHtml(v.command || "（未配置）")}
来源：${escapeHtml(v.source || "-")}　退出码：${v.exit_code === null || v.exit_code === undefined ? "-" : v.exit_code}
${v.finished_at ? "完成于：" + escapeHtml(v.finished_at) + "\n" : ""}${v.output ? "输出：\n" + escapeHtml(v.output) : ""}</div></div>`;
}

/* 重跑：用最后一条用户消息再跑一次（改了 prompt 或换了模型后想重试） */
async function rerun() {
  if (!state.sessionId) return;
  try {
    const r = await api.post(`/api/sessions/${state.sessionId}/rerun`, {});
    state.running = true;
    state.startedAt = Date.now();
    $("status-hint").textContent = "重跑中…";
    renderHeader();
    toast(`已重跑：${String(r.text).slice(0, 20)}`);
  } catch (e) {
    toast(`重跑失败：${e.message}`);
  }
}

function exportMarkdown() {
  const sp = state.spaces.find((s) => s.id === state.spaceId);
  const m = sp && (sp.sessions || []).find((x) => x.id === state.sessionId);
  if (!m) return;
  const lines = [`# ${m.title || "会话"}`, "", `> 空间：${sp.name}　导出时间：${new Date().toLocaleString("zh-CN")}`, ""];
  for (const msg of state.messages) {
    if (msg.role === "user") {
      lines.push("", "## 用户", "", typeof msg.content === "string" ? msg.content : JSON.stringify(msg.content));
    } else if (msg.role === "assistant") {
      if (msg.content) lines.push("", "## 助手", "", msg.content);
      for (const tc of msg.tool_calls || []) {
        const f = tc.function || {};
        lines.push("", `### 工具调用：${f.name || "tool"}`, "", "```", f.arguments || "", "```");
      }
    } else if (msg.role === "tool") {
      lines.push("", `#### 工具结果：${msg.name || "tool"}`, "", "```",
        typeof msg.content === "string" ? msg.content : JSON.stringify(msg.content), "```");
    }
  }
  const blob = new Blob([lines.join("\n")], { type: "text/markdown;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `${(m.title || "会话").replace(/[\\/:*?"<>|]/g, "_")}.md`;
  a.click();
  URL.revokeObjectURL(a.href);
  toast("已导出 Markdown");
}

/* 斜杠命令：只做本地就能完成的几个，不新增后端接口 */
async function runCommand(text) {
  const [cmd, ...rest] = text.slice(1).split(/\s+/);
  const arg = rest.join(" ").trim();
  if (cmd === "help" || cmd === "") {
    addNoteCard(["可用命令：",
      "/verify —— 跑空间里配置的验证命令",
      `/model <profile> —— 切换模型（当前可选：${state.profiles.join(" / ")}）`,
      "/help —— 显示这条帮助"].join("\n"));
    return;
  }
  if (cmd === "verify") {
    if (!state.sessionId) return;
    toast("已提交验证命令");
    try { await api.post(`/api/sessions/${state.sessionId}/verify`, {}); }
    catch (e) { toast(`验证失败：${e.message}`); }
    return;
  }
  if (cmd === "model") {
    if (!arg) { toast("用法：/model <profile>"); return; }
    if (!state.profiles.includes(arg)) { toast(`没有这个 profile：${arg}`); return; }
    const cur = state.spaces.find((s) => s.id === state.spaceId);
    if (cur && (cur.executor || "simpleagent") !== "simpleagent") {
      toast("这个空间绑的是外部 agent，模型由它自己的配置决定");
      return;
    }
    try {
      await api.patch(`/api/spaces/${state.spaceId}`, { profile: arg });
      await loadSpaces();
      toast(`已切换到 ${arg}`);
    } catch (e) { toast(e.message); }
    return;
  }
  toast(`未知命令 /${cmd}，输入 /help 看可用的`);
}

function addNoteCard(text) {
  const box = streamEl();
  $("empty-state")?.remove();
  const el = document.createElement("div");
  el.className = "card";
  el.style.whiteSpace = "pre-wrap";
  el.textContent = text;
  box.appendChild(el);
  scrollDown(true);
}

function autoGrow() {
  const t = $("input");
  t.style.height = "auto";
  t.style.height = `${Math.min(t.scrollHeight, 200)}px`;
}

async function newSession(spaceId) {
  const meta = await api.post(`/api/spaces/${spaceId}/sessions`, {});
  await loadSpaces();
  await selectSession(spaceId, meta.id);
}

async function send() {
  const text = $("input").value.trim();
  if (!text || !state.sessionId) return;
  $("input").value = "";
  autoGrow();
  if (text.startsWith("/")) { await runCommand(text); return; }
  addUserBubble(text);
  state.running = true;
  state.startedAt = Date.now();
  $("status-hint").textContent = "已提交，等待运行…";
  renderHeader();
  // 兜底：服务进程挂了或请求根本没跑起来时，别让用户一直干等
  clearTimeout(state.noReplyTimer);
  state.noReplyTimer = setTimeout(() => {
    if (!state.running) return;
    state.running = false;
    state.startedAt = null;
    $("status-hint").textContent = "没有收到任何响应，检查 sa serve 是否还在运行（或看服务日志）";
    renderHeader();
  }, 15000);
  try {
    await api.post(`/api/sessions/${state.sessionId}/input`, { text });
  } catch (e) {
    state.running = false;
    addErrorCard(`发送失败：${e.message}`);
    renderHeader();
  }
}

/* ────────────────────────────── 新建空间向导 ────────────────────────────── */
function openModal() {
  $("modal").classList.remove("hidden");
  $("f-name").value = "";
  $("f-cwd").value = "";
  $("f-verify").value = "";
  $("modal-err").textContent = "";
  syncModalFields();
  $("f-name").focus();
}

/* 执行者名单由 /api/meta 给，启动时填一次；之后再打开向导不重填，保留上次的选择。 */
function fillExecutorOptions() {
  $("f-executor").innerHTML = state.executors
    .map((e) => `<option value="${escapeHtml(e.name)}">${escapeHtml(e.label)}</option>`)
    .join("");
  fillExecutorDependents();
}

/* 模型、权限两个下拉的选项取决于执行者，只在执行者变了时重建。
   形态、权限自己变动时不重建，不然刚选的「全放行」会被悄悄换回默认档。 */
function fillExecutorDependents() {
  const executor = $("f-executor").value;
  const sel = $("f-profile");
  if (executor !== "simpleagent") {
    // 外部 CLI 本次只支持「本机默认」：不注入任何 env/args，用它自己的配置
    sel.innerHTML = `<option value="">本机默认（不注入配置）</option>`;
  } else {
    sel.innerHTML = state.profiles
      .map((p) => `<option value="${escapeHtml(p)}">${escapeHtml(p)}</option>`).join("");
    sel.value = state.defaultProfile || state.profiles[0] || "";
  }

  const perm = $("f-permission");
  const info = state.executors.find((e) => e.name === executor);
  const perms = (info && info.permissions) || [];
  perm.innerHTML = perms
    .map((p) => `<option value="${escapeHtml(p.name)}">${escapeHtml(p.label)}</option>`).join("");
  if (perms.length) perm.value = info.default_permission || perms[0].name;
}

/* 两个维度各管各的：形态只管工作目录，执行者只管模型、权限那几栏怎么显示。
   这里只切显隐和文案，不动下拉的选项，所以切来切去不会把上次选的东西换掉。 */
function syncModalFields() {
  const kind = $("f-kind").value;
  const external = $("f-executor").value !== "simpleagent";
  $("f-cwd-wrap").classList.toggle("hidden", kind !== "agent");
  $("f-model-hint").classList.toggle("hidden", !external);
  $("f-perm-wrap").classList.toggle("hidden", !external);
  $("f-model-label").textContent = external ? "模型" : "模型 profile";
  $("f-perm-hint").classList.toggle("hidden", !external || $("f-permission").value !== "full");
}

async function createSpace() {
  const name = $("f-name").value.trim();
  if (!name) { $("modal-err").textContent = "名称不能为空"; return; }
  const kind = $("f-kind").value;
  const executor = $("f-executor").value;
  const body = { name, kind, executor };
  if (kind === "agent") {
    body.cwd = $("f-cwd").value.trim();
    if (!body.cwd) { $("modal-err").textContent = "绑定目录的空间必须填工作目录"; return; }
  }
  if (executor === "simpleagent") {
    body.profile = $("f-profile").value;
  } else {
    // 本机默认 = 不传 cli_model；以后有 preset 时这里换成选中的 preset 名
    const cliModel = $("f-profile").value;
    if (cliModel) body.cli_model = cliModel;
    body.permission = $("f-permission").value;
  }
  const v = $("f-verify").value.trim();
  if (v) { body.verify_command = v; body.verify_trigger = "on_stop"; }
  try {
    const sp = await api.post("/api/spaces", body);
    $("modal").classList.add("hidden");
    await loadSpaces();
    await newSession(sp.id);
  } catch (e) {
    $("modal-err").textContent = `创建失败：${e.message}`;
  }
}

/* ────────────────────────────── 启动 ────────────────────────────── */
async function boot() {
  const meta = await api.get("/api/meta");
  state.profiles = meta.profiles || [];
  state.defaultProfile = meta.default_profile || state.profiles[0] || "";
  state.executors = meta.executors || [{ name: "simpleagent", label: "内置 SimpleAgent" }];
  fillExecutorOptions();
  await loadSpaces();

  $("btn-new-space").onclick = openModal;
  $("f-cancel").onclick = () => $("modal").classList.add("hidden");
  $("f-create").onclick = createSpace;
  $("f-kind").onchange = syncModalFields;
  $("f-executor").onchange = () => {
    fillExecutorDependents();
    syncModalFields();
  };
  $("f-permission").onchange = syncModalFields;
  $("btn-send").onclick = send;
  $("btn-new-session").onclick = () => state.spaceId && newSession(state.spaceId);
  $("btn-stop").onclick = async () => {
    if (!state.sessionId) return;
    await api.post(`/api/sessions/${state.sessionId}/cancel`, {}).catch((e) => toast(e.message));
    state.running = false;
    renderHeader();
  };
  $("btn-verify").onclick = async () => {
    if (!state.sessionId) return;
    toast("已提交验证命令");
    await api.post(`/api/sessions/${state.sessionId}/verify`, {}).catch((e) => toast(e.message));
  };
  $("input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
  });
  $("input").addEventListener("input", autoGrow);
  $("search").addEventListener("input", (e) => {
    state.filter = e.target.value;
    renderSpaces();
  });
  $("profile").onchange = async () => {
    if (!state.spaceId) return;
    await api.patch(`/api/spaces/${state.spaceId}`, { profile: $("profile").value })
      .then(() => loadSpaces())
      .catch((e) => toast(e.message));
  };
  $("btn-export").onclick = exportMarkdown;
  $("btn-rerun").onclick = rerun;
  document.querySelectorAll(".tab").forEach((t) => {
    t.onclick = () => switchTab(t.dataset.tab);
  });
  $("nav-panel").onclick = () => openPanel();
  $("btn-dispatch").onclick = dispatch;
  $("dispatch-input").addEventListener("input", updateMentions);
  $("dispatch-input").addEventListener("keydown", (e) => {
    const mn = panelState.mentions;
    if (mn.open) {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        mn.index = (mn.index + (e.key === "ArrowDown" ? 1 : mn.items.length - 1)) % mn.items.length;
        updateMentions();
        mn.index = Math.min(mn.index, mn.items.length - 1);
        return;
      }
      if (e.key === "Enter" || e.key === "Tab") {
        e.preventDefault();
        applyMention(mn.index);
        return;
      }
      if (e.key === "Escape") { e.preventDefault(); mn.open = false; updateMentions(); return; }
    }
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); dispatch(); }
  });
  $("inbox-filter").onclick = async () => {
    panelState.inboxUnreadOnly = !panelState.inboxUnreadOnly;
    $("inbox-filter").textContent = panelState.inboxUnreadOnly ? "只看未读" : "全部";
    await loadInbox();
  };
  $("inbox-readall").onclick = async () => {
    await api.post("/api/inbox/all/read", {});
    await loadPanel();
  };
  $("todo-add").onclick = addTodoInline;

  // 运行中每秒刷新一次「已用时」
  setInterval(() => {
    if (state.running && state.startedAt) {
      $("status-hint").textContent =
        `${STATUS_TEXT.running}… ${fmtDur(Math.floor((Date.now() - state.startedAt) / 1000))}`;
    }
  }, 1000);

  // 回到上次打开的会话（刷新页面不该丢上下文）
  try {
    const saved = JSON.parse(localStorage.getItem(LS_KEY) || "null");
    const sp = saved && state.spaces.find((s) => s.id === saved.spaceId);
    if (sp && (sp.sessions || []).some((m) => m.id === saved.sessionId)) {
      await selectSession(saved.spaceId, saved.sessionId);
    }
  } catch { /* 存的是脏数据就当没有 */ }
}

boot().catch((e) => {
  document.body.innerHTML = `<div style="padding:40px;font:14px/1.7 system-ui">
    <b>加载失败：${escapeHtml(e.message)}</b><br>确认 <code>sa serve</code> 正在运行，然后刷新页面。</div>`;
});
