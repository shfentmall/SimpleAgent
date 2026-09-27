"""请求来源检查：Host 白名单（防 DNS rebinding）、写请求的 Content-Type 和 Origin。

不联网：port=0 起真实的 http.server，用 http.client 打请求，这样 Host / Origin 头能随便填。
"""

from __future__ import annotations

import http.client
import json
import threading

import pytest

from simpleagent.panel.store import PanelStore
from simpleagent.serve.app import check_request, host_allowed, make_server


@pytest.fixture
def port(config):
    httpd = make_server(config, host="127.0.0.1", port=0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


def _request(port: int, method: str, path: str, headers: dict[str, str], body: bytes = b""):
    """headers 里给了 Host 就用它，不给就是 http.client 默认的 127.0.0.1:port。"""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request(method, path, body=body or None, headers=headers)
        r = conn.getresponse()
        return r.status, json.loads(r.read() or b"null")
    finally:
        conn.close()


def _post_inbox(port: int, headers: dict[str, str], title: str = "日报"):
    body = json.dumps({"title": title}).encode()
    return _request(port, "POST", "/api/inbox", headers, body)


# --------------------------------------------------------------- 1. Host 白名单
@pytest.mark.parametrize(
    "host",
    ["localhost", "LOCALHOST:8384", "app.localhost:8385", "127.0.0.1", "127.0.0.1:8384",
     "192.168.1.5:8384", "[::1]", "[::1]:8384"],
)  # fmt: skip
def test_host_allowed(host):
    assert host_allowed(host)


@pytest.mark.parametrize(
    "host",
    ["", "evil.com", "evil.com:8384", "localhost.evil.com", "evil-localhost:8384",
     "127.0.0.1.nip.io", "::1", "[::1", "[evil.com]:80", "[127.0.0.1]", "localhost:x",
     "127.0.0.1:80:80"],
)  # fmt: skip
def test_host_rejected(host):
    assert not host_allowed(host)


def test_bad_host_gets_403_on_read_and_write(port, sa_home):
    status, body = _request(port, "GET", "/api/spaces", {"Host": f"evil.com:{port}"})
    assert (status, body) == (403, {"error": "host not allowed"})

    headers = {"Host": "rebind.attacker.test", "Content-Type": "application/json"}
    status, body = _post_inbox(port, headers)
    assert (status, body) == (403, {"error": "host not allowed"})
    assert PanelStore(sa_home).list_messages() == []  # 没有落盘


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "[::1]"])
def test_local_hosts_pass(port, host):
    status, body = _request(port, "GET", "/api/spaces", {"Host": f"{host}:{port}"})
    assert status == 200
    assert isinstance(body, list)


# --------------------------------------------------------------- 2. Content-Type
def test_text_plain_post_gets_415(port, sa_home):
    # 跨站 fetch(mode: "no-cors") 不设 Content-Type 时就是 text/plain，不会触发 CORS 预检
    status, body = _post_inbox(port, {"Content-Type": "text/plain;charset=UTF-8"})
    assert status == 415
    assert PanelStore(sa_home).list_messages() == []

    status, _ = _post_inbox(port, {})  # 干脆不带 Content-Type 也一样
    assert status == 415


def test_json_post_with_charset_passes(port, sa_home):
    status, body = _post_inbox(port, {"Content-Type": "Application/JSON; charset=utf-8"})
    assert status == 201
    assert body["title"] == "日报"
    assert [m["title"] for m in PanelStore(sa_home).list_messages()] == ["日报"]


def test_bodyless_write_needs_no_content_type(port, sa_home):
    item = PanelStore(sa_home).add_message(source="t", title="x")
    status, body = _request(port, "POST", f"/api/inbox/{item.id}/read", {})
    assert (status, body["read"]) == (200, item.id)


# --------------------------------------------------------------- 3. Origin
def test_cross_site_origin_gets_403(port, sa_home):
    headers = {"Content-Type": "application/json", "Origin": "https://evil.com"}
    status, body = _post_inbox(port, headers)
    assert (status, body) == (403, {"error": "origin not allowed"})

    # 不带 body 的写请求也查 Origin：POST /api/inbox/all/read 不需要知道任何 id
    status, _ = _request(port, "POST", "/api/inbox/all/read", {"Origin": "null"})
    assert status == 403
    assert PanelStore(sa_home).list_messages() == []


def test_origin_must_match_host_and_port(port):
    headers = {"Content-Type": "application/json", "Origin": "http://127.0.0.1:1"}
    assert _post_inbox(port, headers)[0] == 403
    headers["Origin"] = f"http://localhost:{port}"  # 同一台机器，但和 Host 不同源
    assert _post_inbox(port, headers)[0] == 403


def test_same_origin_write_passes(port, sa_home):
    headers = {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{port}"}
    status, _ = _post_inbox(port, headers)
    assert status == 201

    headers = {
        "Host": f"localhost:{port}",
        "Content-Type": "application/json",
        "Origin": f"http://LOCALHOST:{port}",
    }
    assert _post_inbox(port, headers, title="第二条")[0] == 201
    assert len(PanelStore(sa_home).list_messages()) == 2


def test_get_ignores_origin_and_content_type():
    # 读请求只查 Host：跨站 no-cors GET 拿到的是不透明响应，读不到内容
    headers = {"host": "127.0.0.1:8384", "origin": "https://evil.com", "content-type": "text/plain"}
    assert check_request("GET", headers, b"") is None


# --------------------------------------------------------------- 4. 正常 JSON 流程
def test_normal_json_flow(port):
    headers = {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{port}"}
    spec = {"name": "t", "kind": "generic", "profile": "a"}
    status, space = _request(port, "POST", "/api/spaces", headers, json.dumps(spec).encode())
    assert status == 201

    status, session = _request(port, "POST", f"/api/spaces/{space['id']}/sessions", headers)
    assert status == 201

    patch = json.dumps({"title": "改个名"}).encode()
    status, updated = _request(port, "PATCH", f"/api/sessions/{session['id']}", headers, patch)
    assert (status, updated["title"]) == (200, "改个名")

    status, listed = _request(port, "GET", f"/api/spaces/{space['id']}/sessions", {})
    assert status == 200
    assert [m["id"] for m in listed] == [session["id"]]
