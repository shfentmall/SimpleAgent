"""HTTP + SSE 服务：把 Runner / 总线 / 存储 暴露成设计文档 6.2 的本地 API。

技术选型走文档里的 A 方案：标准库 http.server + 手写路由 + 手写 SSE，零新依赖。
客户端→服务端都是普通请求（发消息 / 取消 / 审批），服务端→客户端是单向 SSE 事件流，
断线重连用 SSE 原生的 Last-Event-ID 即可。
"""

from __future__ import annotations

import json
import queue
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from simpleagent.config import Config
from simpleagent.serve.bus import frame_to_sse
from simpleagent.serve.runner import Runner
from simpleagent.spaces.models import SpaceSpec
from simpleagent.spaces.store import SpaceStore

# 控制面板的"近期活动流"：任务跑完不在面板上立刻消失，而是在 recent 里留存一段时间。
# 不留存的话，用户看到的是"列表少了一条"而不是"这条跑完了"，等于看不到状态变化。
RECENT_DONE_LIMIT = 10
RECENT_DONE_WINDOW_MINUTES = 24 * 60
FINAL_STATUSES = frozenset({"done", "error", "cancelled"})


def _within_window(ts: str, cutoff: float) -> bool:
    """时间戳是否晚于 cutoff。解析不出来就当它不在窗口内，别让脏数据把接口打挂。"""
    try:
        return datetime.fromisoformat(ts).timestamp() > cutoff
    except (TypeError, ValueError):
        return False


def _verification_status(meta: Any) -> str:
    """取验证状态。meta 里既可能是 Verification 对象，也可能是手写进去的 dict。"""
    v = getattr(meta, "verification", None)
    if isinstance(v, dict):
        return v.get("status", "unknown")
    return getattr(v, "status", "unknown")


class Response:
    def __init__(
        self,
        status: int,
        body: Any = None,
        headers: dict[str, str] | None = None,
        stream: Iterator[str] | None = None,
    ) -> None:
        self.status = status
        self.body = body
        self.headers = headers or {}
        self.stream = stream  # SSE 时为帧生成器，否则为 None


class Server:
    def __init__(
        self,
        config: Config,
        *,
        store: SpaceStore | None = None,
        runner: Runner | None = None,
        llm_factory=None,
    ) -> None:
        self.config = config
        self.store = store or SpaceStore()
        self.runner = runner or Runner(config, store=self.store, llm_factory=llm_factory)

    def start(self) -> None:
        self.runner.start()

    # ------------------------------------------------------------------- 路由
    def handle(self, method: str, path: str, headers: dict[str, str], body: bytes) -> Response:
        # 去掉 query string
        path_only = urlparse(path).path
        if method == "GET" and path_only == "/api/spaces":
            return self._list_spaces(headers)
        if method == "POST" and path_only == "/api/spaces":
            return self._create_space(body)
        m = re.match(r"^/api/spaces/([^/]+)$", path_only)
        if m:
            space_id = m.group(1)
            if method == "GET":
                return self._space_detail(space_id)
            if method == "PATCH":
                return self._update_space(space_id, body)
            if method == "DELETE":
                return self._delete_space(space_id)
        m = re.match(r"^/api/spaces/([^/]+)/sessions$", path_only)
        if m:
            space_id = m.group(1)
            if method == "GET":
                return self._list_sessions(space_id)
            if method == "POST":
                return self._create_session(space_id, body)
        m = re.match(r"^/api/sessions/([^/]+)$", path_only)
        if m and method == "GET":
            return self._session_messages(m.group(1))
        m = re.match(r"^/api/sessions/([^/]+)/input$", path_only)
        if m and method == "POST":
            return self._session_input(m.group(1), body)
        m = re.match(r"^/api/sessions/([^/]+)/cancel$", path_only)
        if m and method == "POST":
            return self._session_cancel(m.group(1))
        m = re.match(r"^/api/sessions/([^/]+)/verify$", path_only)
        if m and method == "POST":
            return self._session_verify(m.group(1))
        m = re.match(r"^/api/sessions/([^/]+)/verification$", path_only)
        if m and method == "PATCH":
            return self._mark_verified(m.group(1), body)
        m = re.match(r"^/api/sessions/([^/]+)/events$", path_only)
        if m and method == "GET":
            return self._sse(m.group(1), headers)
        if method == "GET" and path_only == "/api/approvals":
            return self._list_approvals()
        m = re.match(r"^/api/approvals/([^/]+)$", path_only)
        if m and method == "POST":
            return self._resolve_approval(m.group(1), body)
        if method == "GET" and path_only == "/api/panel/summary":
            return self._panel_summary()
        return Response(404, {"error": "not found", "path": path_only})

    # ----------------------------------------------------------------- 空间
    def _list_spaces(self, headers: dict[str, str]) -> Response:
        opened_only = "all" not in headers.get("x-flags", "") and "all=1" not in headers.get(
            "x-query", ""
        )
        spaces = self.store.list_spaces(opened_only=opened_only)
        return Response(200, [self._space_view(s) for s in spaces])

    def _space_view(self, space) -> dict[str, Any]:
        data = space.to_dict()
        data["sessions"] = [m.to_dict() for m in self.store.list_sessions(space.id, limit=5)]
        return data

    def _create_space(self, body: bytes) -> Response:
        try:
            data = json.loads(body or b"{}")
        except json.JSONDecodeError:
            return Response(400, {"error": "invalid json"})
        allowed = {
            "name",
            "kind",
            "profile",
            "pin_dir",
            "agent_name",
            "cwd",
            "command",
            "args",
            "verify_command",
            "verify_trigger",
            "verify_timeout",
        }
        try:
            spec = SpaceSpec(**{k: v for k, v in data.items() if k in allowed})
        except (TypeError, ValueError) as e:
            return Response(400, {"error": f"参数错误：{e}"})
        space = self.store.create_space(spec)
        return Response(201, self._space_view(space))

    def _space_detail(self, space_id: str) -> Response:
        space = self.store.get_space(space_id)
        if space is None:
            return Response(404, {"error": "space not found"})
        return Response(200, self._space_view(space))

    def _update_space(self, space_id: str, body: bytes) -> Response:
        try:
            data = json.loads(body or b"{}")
        except json.JSONDecodeError:
            return Response(400, {"error": "invalid json"})
        try:
            space = self.store.update_space(space_id, **data)
        except (KeyError, ValueError) as e:
            return Response(400, {"error": str(e)})
        return Response(200, self._space_view(space))

    def _delete_space(self, space_id: str) -> Response:
        self.store.delete_space(space_id)
        return Response(200, {"deleted": space_id})

    # ----------------------------------------------------------------- 会话
    def _list_sessions(self, space_id: str) -> Response:
        if self.store.get_space(space_id) is None:
            return Response(404, {"error": "space not found"})
        metas = self.store.list_sessions(space_id, limit=5)
        return Response(200, [m.to_dict() for m in metas])

    def _create_session(self, space_id: str, body: bytes) -> Response:
        if self.store.get_space(space_id) is None:
            return Response(404, {"error": "space not found"})
        data = self._safe_json(body)
        agent = (data or {}).get("agent")
        meta = self.store.create_session(space_id, agent=agent)
        return Response(201, meta.to_dict())

    def _session_messages(self, session_id: str) -> Response:
        space_id = self.store.find_session_space(session_id)
        if space_id is None:
            return Response(404, {"error": "session not found"})
        session = self.store.load_session(space_id, session_id)
        return Response(200, {"session_id": session_id, "messages": session.messages})

    def _session_input(self, session_id: str, body: bytes) -> Response:
        space_id = self.store.find_session_space(session_id)
        if space_id is None:
            return Response(404, {"error": "session not found"})
        data = self._safe_json(body) or {}
        text = data.get("text")
        if not text or not str(text).strip():
            return Response(400, {"error": "text 不能为空"})
        self.runner.run_input(space_id, session_id, str(text))
        return Response(202, {"accepted": True})

    def _session_cancel(self, session_id: str) -> Response:
        self.runner.cancel(session_id)
        return Response(200, {"cancelled": session_id})

    def _session_verify(self, session_id: str) -> Response:
        space_id = self.store.find_session_space(session_id)
        if space_id is None:
            return Response(404, {"error": "session not found"})
        self.runner.verify(space_id, session_id)
        return Response(202, {"accepted": True})

    def _mark_verified(self, session_id: str, body: bytes) -> Response:
        space_id = self.store.find_session_space(session_id)
        if space_id is None:
            return Response(404, {"error": "session not found"})
        data = self._safe_json(body) or {}
        verification = {
            "status": data.get("status", "passed"),
            "command": data.get("command"),
            "exit_code": data.get("exit_code"),
            "source": "manual",
        }
        self.store.update_meta(space_id, session_id, verification=verification)
        return Response(200, {"verification": verification})

    # ----------------------------------------------------------------- SSE
    def _sse(self, session_id: str, headers: dict[str, str]) -> Response:
        last_event_id = headers.get("last-event-id")
        q, replay = self.runner.bus.subscribe(session_id, last_event_id)

        def stream() -> Iterator[str]:
            try:
                for frame in replay:
                    yield frame_to_sse(frame)
                while True:
                    try:
                        frame = q.get(timeout=15)
                    except queue.Empty:
                        yield ": keepalive\n\n"
                        continue
                    yield frame_to_sse(frame)
            finally:
                self.runner.bus.unsubscribe(session_id, q)

        return Response(200, stream=stream())

    # ----------------------------------------------------------------- 审批
    def _list_approvals(self) -> Response:
        return Response(200, {"pending": self.runner.pending.pending_ids()})

    def _resolve_approval(self, approval_id: str, body: bytes) -> Response:
        data = self._safe_json(body) or {}
        ok = self.runner.approve(approval_id, data.get("action", "allow"))
        if not ok:
            return Response(404, {"error": "approval not found or runner not started"})
        return Response(200, {"resolved": approval_id})

    # ----------------------------------------------------------------- 控制面板
    def _panel_summary(self) -> Response:
        """控制面板：正在跑的 + 最近完成的（近期活动流）。

        以前只筛 status == "running"，且只返回 space_id / session_id / title：
        任务一结束就从列表消失，用户看到的是"少了一条"而不是"跑完了"，加上总线里
        本来就没有完成帧，于是完全看不到状态变化。现在完成的带终态、验证结果和
        updated_at 在 recent 里留存 24 小时，前端据此显示"刚完成 ✓ / ✗ 未通过"。
        """
        cutoff = datetime.now(UTC).timestamp() - RECENT_DONE_WINDOW_MINUTES * 60
        running: list[dict[str, Any]] = []
        recent: list[dict[str, Any]] = []
        for space in self.store.list_spaces(opened_only=False):
            for meta in self.store.list_sessions(space.id, limit=50, include_pinned=False):
                item = {
                    "space_id": space.id,
                    "space_name": space.name,
                    "session_id": meta.id,
                    "title": meta.title,
                    "status": meta.status,
                    "updated_at": meta.updated_at,
                    "verification": _verification_status(meta),
                }
                if meta.status == "running":
                    running.append(item)
                elif meta.status in FINAL_STATUSES and _within_window(meta.updated_at, cutoff):
                    recent.append(item)
        recent.sort(key=lambda item: item["updated_at"], reverse=True)
        return Response(
            200,
            {
                "running": running,
                "recent": recent[:RECENT_DONE_LIMIT],
                "opened_spaces": len(self.store.list_spaces(opened_only=True)),
            },
        )

    # ----------------------------------------------------------------- 工具
    @staticmethod
    def _safe_json(body: bytes) -> dict[str, Any] | None:
        if not body:
            return None
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return None


# ----------------------------------------------------------------------- 适配 http.server
def _make_handler(app: Server):
    class Handler(BaseHTTPRequestHandler):
        def _dispatch(self) -> None:
            method = self.command
            parsed = urlparse(self.path)
            headers = {k.lower(): v for k, v in self.headers.items()}
            headers["x-query"] = parsed.query
            length = int(headers.get("content-length", 0) or 0)
            body = self.rfile.read(length) if length else b""
            resp = app.handle(method, parsed.path, headers, body)

            self.send_response(resp.status)
            for k, v in resp.headers.items():
                self.send_header(k, v)
            if resp.stream is not None:
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                try:
                    for chunk in resp.stream:
                        data = chunk.encode("utf-8") if isinstance(chunk, str) else chunk
                        self.wfile.write(data)
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                return
            if resp.body is not None:
                out = json.dumps(resp.body, ensure_ascii=False).encode("utf-8")
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)
            else:
                self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            self._dispatch()

        def do_POST(self) -> None:  # noqa: N802
            self._dispatch()

        def do_PATCH(self) -> None:  # noqa: N802
            self._dispatch()

        def do_DELETE(self) -> None:  # noqa: N802
            self._dispatch()

        def log_message(self, *args: Any) -> None:  # 安静：不刷访问控制日志
            pass

    return Handler


def make_server(
    config: Config, host: str = "127.0.0.1", port: int = 8384, llm_factory=None
) -> ThreadingHTTPServer:
    """构造并启动本地 API 服务，返回已 start() 的 http server（调用方负责 serve_forever）。"""
    app = Server(config, llm_factory=llm_factory)
    app.start()
    handler = _make_handler(app)
    httpd = ThreadingHTTPServer((host, port), handler)
    return httpd
