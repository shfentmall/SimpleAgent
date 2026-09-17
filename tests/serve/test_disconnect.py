"""回归：客户端断开 SSE 连接时，服务端应当安静收场，不往 stderr 打 traceback。

背景（踩过的坑）：`BaseHTTPRequestHandler.send_header("Connection", "keep-alive")`
会把 `self.close_connection` 置回 False（CPython 的实现）。于是 SSE 这一轮处理完后，
handler 又回到 `rfile.readline()` 等下一个请求；用户关掉浏览器标签页时这里抛出
`ConnectionResetError`，`socketserver` 兜底打出一整段 traceback，看着像服务崩了。
"""

from __future__ import annotations

import socket
import threading
import time

from simpleagent.serve import app as app_module
from simpleagent.serve.app import make_server


def _serve(config):
    httpd = make_server(config, host="127.0.0.1", port=0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def test_sse_disconnect_does_not_print_traceback(config, sa_home, monkeypatch, capsys):
    # 把 keepalive 周期压到 0.2s：服务端是靠「写 keepalive 失败」才发现客户端走了的，
    # 不缩短的话要等满 15s 才能观察到。
    monkeypatch.setattr(app_module, "KEEPALIVE_INTERVAL", 0.2)
    httpd = _serve(config)
    port = httpd.server_address[1]

    s = socket.create_connection(("127.0.0.1", port))
    s.sendall(b"GET /api/sessions/probe/events HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
    time.sleep(0.3)  # 等服务端写完响应头、进入 SSE 流
    s.close()  # 模拟用户关掉标签页（发 FIN/RST）
    time.sleep(1.5)  # 覆盖一个以上 keepalive 周期

    try:
        err = capsys.readouterr().err
        assert "Traceback" not in err, f"断连时打了 traceback：\n{err}"
    finally:
        httpd.shutdown()


def test_disconnect_releases_handler_thread(config, sa_home, monkeypatch):
    """断连后 handler 线程应在一个 keepalive 周期内回收。

    服务端没有别的办法知道对方走了，只能等写 keepalive 失败，所以这个周期就是
    线程被占用的上限。不缩短它，这条断言要等 15s 才成立。
    """
    monkeypatch.setattr(app_module, "KEEPALIVE_INTERVAL", 0.2)
    httpd = _serve(config)
    port = httpd.server_address[1]
    time.sleep(0.1)
    before = threading.active_count()

    for _ in range(3):
        s = socket.create_connection(("127.0.0.1", port))
        s.sendall(b"GET /api/sessions/probe/events HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
        time.sleep(0.2)
        s.close()  # 关标签页
        time.sleep(0.2)
    time.sleep(1.0)  # 覆盖若干 keepalive 周期

    try:
        assert threading.active_count() <= before, "断连后线程没有回收"
    finally:
        httpd.shutdown()
