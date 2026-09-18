# 控制面板消息升级为中控：详情弹层、自动归档、`sa inbox push` 外部投递

- 日期：2026-09-18
- 对比基线：`2d0f066`（Merge pull request #3 from shfentmall/claude/create-button-unresponsive-c65439）
- 对应里程碑：W5 补强（控制面板 · 消息）

## 功能变化

- 新增：外部文本消息点开弹出详情弹层，显示完整正文（之前正文原样铺在列表里，长文会撑满右栏，点了也没有详情）；弹层带复制 / +备忘 / 归档 / 打开链接（仅 `ref.url` 是 http(s) 时出现，`javascript:` 等链接不给按钮）/ 关闭，Esc 和点遮罩都能关。
- 新增：消息点开后按配置的分钟数（默认 30）自动归档；归档是「算出来的」（`archive_at = archived_at or read_at + archive_after`），不搬动 `inbox.jsonl` 的行，服务没开的时间段不会漏算；没点开过的消息永不自动归档。支持手动「归档」（不等倒计时），归档时顺带补记 `read_at`。
- 新增：`sa inbox push` CLI，不需要 `sa serve` 在跑就能往面板投消息；正文可用 `-b 文本`、`-b -` 强制读 stdin，或不给 `-b` 时在有管道输入时自动读 stdin（例如 `pytest 2>&1 | sa inbox push -t 夜间测试`）。
- 新增：左侧导航「控制面板」旁的未读角标，每 30 秒轮询一次（面板未打开时只拉 `/api/inbox/count`，打开时连列表一起刷；后台标签页跳过，切回前台补一次）。
- 升级：控制面板消息列表把「全部 / 只看未读」换成「当前 / 归档」两栏切换；每次打开面板都回到「当前」；归档栏按归档时间倒序，刚归档的排最上面。
- 升级：消息卡片新增来源标签（系统/定时/邮件/脚本/手动）、2 行正文预览（超出省略，换行拍平）、已读消息的「N 分钟后归档」提示（悬浮时换成「+备忘 / 归档」操作按钮）。
- 升级：会话类消息点开后若指向的会话已被删除（空间不存在），不再无反应，而是回退到弹层看原文并提示「这条消息指向的会话已经不存在了」。
- 修复：外部文本消息的「+备忘」原来固定建成 `kind=session` 的备忘（`ref` 是空的），导致备忘列表里出现一个点了没反应的「跳到会话」死链接；现在按消息的 `action`（`session`/`text`）来定备忘的 `kind`。

## 函数级改动

### `src/simpleagent/config.py`

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `PanelConfig` | 新增 | 新配置块，`archive_after_minutes: int = 30`（`ge=1`）。 |
| `Config.panel` | 新增字段 | `Config` 挂上 `panel: PanelConfig`。 |

### `src/simpleagent/panel/store.py`

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| 模块常量 `STATE_FILENAME` / `LEGACY_READ_FILENAME` / `DEFAULT_ARCHIVE_AFTER_MINUTES` / `PREVIEW_CHARS` / `MAX_BODY_CHARS` / `LEVELS` / `View` | 新增 | 原 `READ_FILENAME` 改名并保留旧名做迁移；`LEVELS` 是级别白名单，`View` 是 `active`/`archived` 的类型别名。 |
| `_now_dt()` / `_iso()` | 新增 | 从原 `_now()` 拆出来：前者返回 `datetime`（给内部时间运算用），后者格式化成 ISO 字符串；`_now()` 保留，改为组合调用。 |
| `_parse()` | 新增 | 把存储里的时间字符串解析回 `datetime`，没有时区的按本地时间处理，解析失败返回 `None`。 |
| `_clip_body()` | 新增 | 正文超过 `MAX_BODY_CHARS`（64000）截断并注明原长度，而不是拒收或整条丢弃。 |
| `_preview()` | 新增 | 换行拍平、截 `PREVIEW_CHARS`（200）字生成列表用的预览，避免列表接口把全文一起带下来。 |
| `_action()` | 新增 | 根据 `ref` 是否同时有 `space_id` + `session_id` 判定点开后该跳会话还是看纯文本，结果不落数据模型。 |
| `InboxItem.source` | 文档改动 | 注释补充 `cli` 作为新来源示例。 |
| `PanelStore.__init__()` | 修改签名 | 新增 `archive_after_minutes`（默认 30）与可注入的 `clock` 参数（测试拨表用）；新增 `self._lock`（`threading.Lock`），因为 HTTP 层是多线程的，`state.json` 的读-改-写要串行。 |
| `PanelStore._read_file()` → `_state_file()` | 改名 | 对应文件从 `read.json` 换成 `state.json`。 |
| `PanelStore.add_message()` | 修改 | 正文先过 `_clip_body`，级别不在白名单时归一成 `info`，时间戳改用可注入的时钟；写入方式从带缓冲的 `open("a")` 换成一次性 `os.write`（`O_APPEND`），避免和 `sa inbox push` 等并发写入方把长行交错写乱。 |
| `PanelStore.mark_read()` | 修改语义 | 只在第一次点开时写 `read_at`（归档倒计时从这一刻起算），再次调用不重置；改走 `self._state()` / `self._write_state()`，并加锁。 |
| `PanelStore.archive()` | 新增 | 手动归档：没点开过的顺带补 `read_at`；已经在归档里的不再改动归档时间；id 不存在返回 `None`。 |
| `PanelStore.list_messages()` | 修改签名与逻辑 | 新增 `view: "active"|"archived"` 参数取代之前只看「全部/未读」；按 `_status()` 算出的归档状态过滤，`archived` 视图按归档时间倒序（同一时刻的保持新消息在前）；条目只带预览，不带全文。 |
| `PanelStore.get_message()` | 新增 | 按 id 取完整详情（含全文），供详情弹层使用。 |
| `PanelStore.counts()` | 新增 | 一次遍历给出 `{unread, active, archived}`，供左栏角标轮询。 |
| `PanelStore.unread_count()` | 修改 | 改为基于 `counts()` 实现，行为不变。 |
| `PanelStore._items()` | 新增（原逻辑内联在 `list_messages`/`unread_count` 里） | 统一读取 `inbox.jsonl`；`errors="replace"` 兜底编码问题，遇到 `json.JSONDecodeError`（并发写入方写到一半）或非 dict / 无 `id` 的行直接跳过，不炸整个列表。 |
| `PanelStore._find()` | 新增 | 按 id 从 `_items()` 里找一条。 |
| `PanelStore._status()` | 新增 | 唯一的归档口径：返回 `(是否已读, 归档时间, 是否已归档)`。 |
| `PanelStore._view()` | 新增 | 把 `InboxItem` + 状态组装成对外的 dict（`preview`/`action`/`read`/`read_at`/`archive_at`/`archived`），`full=False` 时去掉 `body`。 |
| `PanelStore._state()` | 新增（替代 `_read_ids()`） | 读取 `state.json`；文件不存在时触发 `_migrate_legacy_read()`。 |
| `PanelStore._migrate_legacy_read()` | 新增 | 把旧的 `read.json`（仅 id 列表）迁移成 `state.json`，`read_at` 取 `read.json` 的文件 mtime，一次性写出并返回。 |
| `PanelStore._write_state()` | 新增（替代 `_write_read()`） | 原子写 `state.json`（`indent=1`）。 |

### `src/simpleagent/cli.py`

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `inbox_push()` | 新增 | `sa inbox push` 的实现：校验标题非空，正文按「显式 `-b` / `-b -` 强制读 stdin / 无 `-b` 且 stdin 非 tty 时自动读 stdin」三种方式取得，调用 `PanelStore().add_message()` 后打印新消息 id。 |
| `main()` | 修改 | 新增 `inbox` 子命令（含 `push` 子子命令的参数：`-t/--title`、`-b/--body`、`--level`、`--source`），并在分发逻辑里接入 `inbox_push()`。 |

### `src/simpleagent/serve/app.py`

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `Server.__init__()` | 修改 | `PanelStore()` 改为传入 `archive_after_minutes=config.panel.archive_after_minutes`。 |
| `Server.handle()`（路由分发，未命名为独立函数但改动集中在这段路由匹配代码） | 修改 | 新增路由：`GET /api/inbox/count`（须排在 `GET /api/inbox/{id}` 之前，避免 `count` 被当成 id）、`GET /api/inbox/{id}`、`POST /api/inbox/{id}/archive`。 |
| `Server._inbox_list()` | 修改 | 新增并校验 `view` 查询参数（非法值返回 400）；`limit` 解析改为先判断 `isdigit()`，非数字时直接回退默认值 50（原实现遇到非数字字符串会抛异常）。 |
| `Server._inbox_detail()` | 新增 | `GET /api/inbox/{id}`，id 不存在返回 404。 |
| `Server._inbox_add()` | 文档改动 | 补充说明可用 `sa inbox push` 替代（不开 serve 时）；逻辑未变。 |
| `Server._inbox_read()` | 修改行为 | 单条标记已读时改为返回更新后的完整条目（不含 `body`），供前端直接替换列表项；id 不存在时返回 404（**行为变化**：原来对未知 id 也返回 200/`changed:false`）。`*`/`all` 分支行为不变。 |
| `Server._inbox_archive()` | 新增 | `POST /api/inbox/{id}/archive`，id 不存在返回 404。 |

### `src/simpleagent/web/app.js`

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `panelState` | 修改字段 | 去掉 `inboxUnreadOnly`，新增 `inboxView`（`"active"`）、`counts`、`modalItem`。 |
| `openPanel()` | 修改 | 每次打开面板强制把 `inboxView` 重置为 `"active"`。 |
| `loadPanel()` | 修改 | 消息数据改由新的 `loadInbox()` 统一加载（不再单独 `api.get("/api/inbox")` 再 `renderInbox()`）。 |
| `loadInbox()` | 修改 | 按 `panelState.inboxView` 请求 `?view=active|archived`（归档视图 limit 200，当前视图 50），并行拉 `/api/inbox/count` 合并进状态。 |
| `SOURCE_LABEL` / `LEVEL_LABEL` / `INBOX_POLL_MS` / `sourceLabel()` | 新增 | 来源/级别的中文映射与 30 秒轮询间隔常量。 |
| `archiveHint()` | 新增 | 根据 `archive_at`/`archived` 生成「N 分钟后归档」「即将归档」「归档于 …」提示文案。 |
| `applyCounts()` | 新增 | 把 `/api/inbox/count` 的结果同步到左栏角标（99+ 上限）和消息卡头部的未读/归档数字。 |
| `refreshBadge()` | 新增 | 单独拉一次 `/api/inbox/count` 刷角标（面板未打开时用）。 |
| `pollInbox()` | 新增 | 30 秒定时器的回调：面板打开时刷列表 + 统计，未打开只刷角标；标签页隐藏时跳过。 |
| `renderInbox()` | 重写 | 渲染当前/归档切换标签；卡片改为来源标签 + 2 行截断预览 + 悬浮显示的归档提示/操作按钮；归档视图不显示「归档」按钮；点击整卡改走 `openMessage()`。 |
| `openMessage()` | 新增（替代原来内联在 `renderInbox` 里的 `el.onclick`） | 先标已读（若未读），按 `action` 分流：`session` 尝试跳转，会话不存在时回退到 `openMessageModal()` 并带提示；否则直接打开详情弹层。 |
| `openMessageModal()` | 新增 | 拉取 `/api/inbox/{id}` 详情并渲染进弹层（标题、meta、可选提示、正文、链接按钮的显隐、归档按钮的显隐）。 |
| `closeMessageModal()` | 新增 | 关闭弹层并清空 `panelState.modalItem`。 |
| `archiveMessage()` | 新增 | 调 `POST /api/inbox/{id}/archive`，刷新列表和统计，弹 toast。 |
| `messageToTodo()` | 新增（替代原内联逻辑） | 按 `item.action` 决定新建备忘的 `kind` 是 `session` 还是 `text`（修复了之前外部文本消息固定建 `session` 备忘导致的死链接）。 |
| `boot()` 内的事件绑定 | 修改 | 原 `#inbox-filter` 的点击换成 `#inbox-view` 下 `.seg-item` 的切换；新增消息弹层的关闭/复制/+备忘/归档按钮绑定与 Esc 关闭；启动时调用一次 `refreshBadge()` 并 `setInterval(pollInbox, INBOX_POLL_MS)`；`visibilitychange` 回调里补充切回前台时调用 `pollInbox()`。 |

### `src/simpleagent/web/index.html`

- 左栏「控制面板」导航项新增未读角标 `#nav-panel-badge`。
- 消息卡头部把「全部/只看未读」链接换成「当前/归档」分段控件（`#inbox-view`），归档标签带计数 `#inbox-archived-n`。
- 新增消息详情弹层 `#msg-modal`（标题、meta、提示条、正文、复制/+备忘/归档/打开链接/关闭按钮）。

### `src/simpleagent/web/styles.css`

- 新增 `.nav-badge`（左栏角标）、`.seg`/`.seg-item`（当前/归档分段控件）样式。
- `.inbox-item` 系列样式调整：可点击态、已读态弱化标题、来源标签 `.inbox-src`、右侧 `.inbox-side`/`.inbox-hint`/`.inbox-acts`（悬浮切换归档提示与操作按钮）、正文预览改为 2 行截断（`-webkit-line-clamp`）。
- 新增消息详情弹层相关样式：`.modal-card.wide`、`.mm-meta`、`.mm-note`、`.mm-body`（含空态提示与内嵌 `code` 样式）、`.modal-foot .spacer`、`a.btn`。

### `src/simpleagent/config.example.toml`

- 新增注释掉的 `[panel] archive_after_minutes = 30` 示例配置块。

### `README.md`

- 快速开始新增一行 `sa inbox push` 用法示例（管道 + `-t`/`--level`）。

### `docs/design/client-ui.md`

- 顶部状态说明补充一句本次改动概述。
- API 表格更新：`GET /api/inbox` 补充 `view` 参数说明；新增 `GET /api/inbox/count`、`GET /api/inbox/{id}`、`POST /api/inbox/{id}/archive` 三行；`POST /api/inbox` 和 `POST /api/inbox/{id}/read` 的说明文字更新。
- 10.7 节「消息（inbox）」段落末尾补充一句指向新增的 10.11 节。
- 新增 10.11 节「控制面板消息：详情、归档与外部投递」，完整记录本次设计（归档口径公式、迁移策略、`action` 推导规则、列表/详情分离、外部投递的并发写入策略、轮询策略、已知未做的事项）。

## 配置与依赖

- 新增配置项 `[panel] archive_after_minutes`（整数，`>=1`，默认 30，分钟）。不写就是默认值，无需手动改 `config.toml`；如果想调整归档时限才需要手动加这段（示例见 `config.example.toml`）。
- 数据目录变化：`~/.simpleagent/panel/read.json` 被 `~/.simpleagent/panel/state.json` 取代。**无需手动迁移**——`PanelStore` 首次读取 `state.json` 不存在时会自动读旧的 `read.json` 迁移（用旧文件的 mtime 当 `read_at`），迁移后旧消息会直接进入归档视图。旧 `read.json` 文件不会被删除，可以手动清理。
- 无新增第三方依赖，`uv.lock` 未改动，不需要 `uv sync`。
- 无需修改 `~/.simpleagent/.env`。

## 测试

- `tests/test_config.py`：新增 `test_panel_config`，覆盖 `archive_after_minutes` 默认值、自定义值、`<1` 时的校验报错（1 个新测试）。
- `tests/test_panel.py`：改动较大。新增可拨表的 `Clock` 测试辅助类；围绕 store 的归档口径新增/改写了约 10 个测试函数，覆盖：30 分钟后自动归档、归档时限可配置、`mark all read` 统一起算倒计时、手动归档（含幂等与「不存在的 id」）、预览/详情/`action` 推导、超长正文截断与非法级别归一、半行 JSON 容错跳过、旧 `read.json` 迁移；`test_panel_api` / `test_panel_api_bad_input` 补充了详情、手动归档、`view` 非法值、未知 id 的 404 等接口断言；新增 4 个 `sa inbox push` CLI 测试（带 `-b`、管道读 stdin、tty 下不读 stdin、拒绝空标题）。
- 测试结果：`uv run pytest -q` → **311 passed**（实测，非估计）。
- `uv run ruff check` → All checks passed。
- `uv run ruff format --check` → 99 files already formatted（无需改动）。

## 相关笔记

- 无（按约定，`docs/notes/` 学习笔记在里程碑整体完成后统一写，本次是 W5 的补强，不单独写）。
