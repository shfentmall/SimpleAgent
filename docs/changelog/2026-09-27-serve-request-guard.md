# `sa serve` 本地 API 加请求来源检查：防 DNS rebinding 和跨站请求

- 日期：2026-09-27
- 对比基线：`9ab5d64`（版本号 0.2.2）
- 对应里程碑：W 个人 AI 工作台（W2 本地 API 的安全加固）

## 功能变化

- 新增：`sa serve` 的本地 HTTP API 在路由之前新增一层「请求来源检查」，堵住三类之前不设防的攻击面：
  - **Host 白名单**（所有请求都查）：`Host` 头去掉端口后必须是 `localhost`、`*.localhost` 或字面 IP（IPv6 要带方括号），否则返回 `403 {"error": "host not allowed"}`；缺 `Host` 头也拒。防的是 DNS rebinding——恶意网页把自己的域名解析到 `127.0.0.1` 后，浏览器会把它当同源放行跨域限制，从而读写整个本地 API（列空间 → 往全放行空间的会话发输入 → 执行 bash），但请求里的 `Host` 头仍是那个域名，靠这层能识别出来。
  - **写请求（POST/PATCH/DELETE）校验 Origin**：带了 `Origin` 头就必须和 `Host` 同源（host:port 一致，scheme 为 http/https），否则 `403 {"error": "origin not allowed"}`；`Origin: null`（沙箱 iframe、`file://` 页面）也拒。不带 `Origin` 的请求（curl、脚本、`sa inbox push` 那种直接写文件的场景）不受影响。
  - **写请求带 body 必须是 JSON**：`Content-Type` 忽略大小写和 `charset` 等参数后必须是 `application/json`，否则 `415 {"error": "content-type must be application/json"}`。堵的是 `text/plain` 这种「简单请求」——跨站 `fetch(..., {mode: "no-cors"})` 不设 `Content-Type` 时默认就是 `text/plain`，不会触发浏览器的 CORS 预检，能直接打到 `POST /api/spaces`、`POST /api/inbox` 这类不需要知道任何 id 的接口；要求 JSON 就逼浏览器先做预检，而本服务不回任何 CORS 头，预检必然失败，请求发不出去。不带 body 的写请求（取消、重跑、标已读）不查这条。
- 文档：`docs/design/client-ui.md` 第 6 节补充「请求来源检查」小节，说明设计动机和三条规则；`docs/send-message.md` 的响应状态码表补上 `415` 和 `403` 两行。

## 函数级改动

### `src/simpleagent/serve/app.py`

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `WRITE_METHODS` | 新增 | `frozenset({"POST", "PATCH", "DELETE"})`，标记会改状态、需要查 Origin/Content-Type 的方法。 |
| `host_allowed(host)` | 新增 | 判断去掉端口后的 `Host` 是否为 `localhost`/`*.localhost`/字面 IPv4/带方括号的字面 IPv6；格式不对（比如端口不是数字、IPv6 缺右方括号）一律拒绝。 |
| `_same_origin(origin, host)` | 新增 | 用 `urlparse` 解析 `Origin`，scheme 必须是 http/https 且 `netloc`（host:port）与请求 `Host` 完全一致（忽略大小写）才算同源。 |
| `check_request(method, headers, body)` | 新增 | 路由前的统一检查入口：先查 Host 白名单（不过直接 403）；非写方法到此放行；写方法再查跨站 Origin（403）和 body 的 Content-Type（415）；全部通过返回 `None`。放在 HTTP 适配层而不是 `Server.handle` 里，是为了让仓库里已有的约 34 处直接调用 `Server.handle` 的单测不用伪造这些头。 |
| `_make_handler._dispatch` | 修改 | 读完请求 body 后，先把结果交给 `check_request`，未通过就直接把它返回的 `Response`（403/415）发给客户端，不再进 `app.handle`；通过则和之前一样走 `app.handle`。 |

## 配置与依赖

- 无配置项或依赖变化。
- 需要手动处理：
  - 用局域网主机名（如 `mac.local`）访问 `sa serve` 的网页会被 403，需要改用 IP（如 `192.168.x.x`）或 `localhost`。
  - `curl` / 脚本调用写接口（如 `POST /api/inbox`）必须带 `Content-Type: application/json`（`docs/send-message.md` 里的示例本来就带，无需改）。
  - 以后如果把界面包成 Tauri 之类的桌面壳，其 WebView 的 `Origin`（例如 `tauri://localhost`）需要加进 `_same_origin` 的白名单，否则写请求会被 403 拦掉；这属于后续工作，本次未做。

## 测试

- 新增 `tests/serve/test_request_guard.py`：起真实 `http.server`（`port=0`）用 `http.client` 发请求，覆盖坏 `Host` 返回 403（含读、写两类请求）、`localhost`/`127.0.0.1`/`[::1]` 等合法 Host 放行、`Host` 白名单的正反例参数化、`text/plain` 写请求 415、不带 body 的写请求不查 Content-Type、跨站 `Origin`（含 `null`）返回 403、同源 Origin（含大小写不同）放行、GET 请求忽略 Origin/Content-Type、以及新建空间 → 建会话 → 改标题 → 查会话列表的正常 JSON 流程。pytest 收集到 32 个具体用例（含参数化展开）。
- 全量测试：`uv run pytest -q` 842 passed。
- `uv run ruff check`：全部通过。
- `uv run ruff format --check`：全部通过（187 files already formatted）。
- 另外手动验证：临时数据目录下起 `sa serve`，用 `curl` 分别发坏 `Host`、`text/plain` 写请求、跨站 `Origin` 写请求，确认分别被拒；再用浏览器打开控制面板正常操作（增删改 todo、新建空间），确认同源请求不受影响。

## 相关笔记

- 无
