"""W5 测试：控制面板的 inbox / todos / 结构化摘要 / panel summary。

不联网，全部用临时 home。
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

from simpleagent.panel.store import PanelStore
from simpleagent.panel.summary import one_line, summarize
from simpleagent.serve.app import make_server
from simpleagent.spaces.models import SpaceSpec
from simpleagent.spaces.store import SpaceStore


def _serve(config):
    httpd = make_server(config, host="127.0.0.1", port=0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{httpd.server_address[1]}"


def _get_json(url: str):
    with urllib.request.urlopen(url, timeout=5) as r:
        return json.loads(r.read())


def _req(method: str, url: str, data: dict | None = None):
    req = urllib.request.Request(
        url,
        data=None if data is None else json.dumps(data).encode(),
        headers={"Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


# --------------------------------------------------------------- 1. inbox
def test_inbox_append_and_read(sa_home):
    panel = PanelStore(sa_home)
    a = panel.add_message(source="system", title="第一个", body="内容", level="success")
    panel.add_message(source="system", title="第二个")

    items = panel.list_messages()
    assert [i["title"] for i in items] == ["第二个", "第一个"]  # 新的在前
    assert items[0]["read"] is False
    assert panel.unread_count() == 2

    assert panel.mark_read(a.id) is True
    assert panel.mark_read(a.id) is False  # 重复标记不改变任何东西
    assert panel.unread_count() == 1
    assert panel.list_messages(unread_only=True)[0]["title"] == "第二个"

    assert panel.mark_read("*") is True
    assert panel.unread_count() == 0


def test_inbox_is_append_only(sa_home):
    """已读状态不能靠改 jsonl 实现——这里确认它确实存在单独的 read.json。"""
    panel = PanelStore(sa_home)
    panel.add_message(source="system", title="x")
    panel.mark_read("*")
    assert (sa_home / "panel" / "read.json").exists()
    assert (sa_home / "panel" / "inbox.jsonl").exists()


# --------------------------------------------------------------- 2. todos
def test_todos_crud(sa_home):
    panel = PanelStore(sa_home)
    t1 = panel.add_todo("写周报")
    t2 = panel.add_todo("看下 diff", kind="session", ref={"space_id": "sp1", "session_id": "se1"})

    items = panel.list_todos()
    assert len(items) == 2
    assert items[0]["kind"] == "text"

    done = panel.update_todo(t1.id, done=True)
    assert done.done is True and done.done_at
    assert panel.list_todos()[0]["id"] == t2.id  # 完成的沉底

    assert panel.update_todo(t1.id, text="写月报").text == "写月报"
    assert panel.delete_todo(t2.id) is True
    assert panel.delete_todo(t2.id) is False
    assert len(panel.list_todos()) == 1


def test_todos_persist(sa_home):
    """换一个 store 实例读，数据还在（真的落盘了）。"""
    panel = PanelStore(sa_home)
    panel.add_todo("记得关服务")
    again = PanelStore(sa_home).list_todos()
    assert [t["text"] for t in again] == ["记得关服务"]


# --------------------------------------------------------------- 3. 结构化摘要
def test_summary_counts_files_and_calls():
    messages = [
        {"role": "user", "content": "改一下"},
        {
            "role": "assistant",
            "content": "好",
            "tool_calls": [
                {
                    "id": "c1",
                    "function": {
                        "name": "write_file",
                        "arguments": '{"path":"a.py","content":"x"}',
                    },
                },
                {
                    "id": "c2",
                    "function": {
                        "name": "edit_file",
                        "arguments": '{"path":"a.py","old":"x","new":"y"}',
                    },
                },
                {"id": "c3", "function": {"name": "read_file", "arguments": '{"path":"a.py"}'}},
            ],
        },
        {"role": "tool", "tool_call_id": "c3", "content": "x"},
        {"role": "assistant", "content": "改完了，一共动了 a.py"},
    ]
    s = summarize(messages)
    assert s["files_changed"] == 1  # a.py 写+改算一个文件
    assert s["files"] == ["a.py"]
    assert s["tool_calls"] == 3
    assert s["turns"] == 1
    assert s["last_text"] == "改完了，一共动了 a.py"
    assert "改了 1 个文件" in one_line(s)


def test_summary_truncates_long_text():
    long = "字" * 500
    s = summarize([{"role": "assistant", "content": long}])
    assert s["last_text"].endswith("…")
    assert len(s["last_text"]) == 121  # 120 字 + 省略号


def test_summary_empty():
    s = summarize([])
    assert s["files_changed"] == 0
    assert one_line(s) == "没有文件改动"


# --------------------------------------------------------------- 4. HTTP 接口
def test_panel_api(config, sa_home):
    store = SpaceStore(sa_home)
    space = store.create_space(SpaceSpec(name="t", kind="generic", profile="a"))
    session = store.create_session(space.id)
    base = _serve(config)

    summary = _get_json(f"{base}/api/panel/summary")
    assert summary["opened_spaces"] == 1
    assert summary["unread"] == 0
    assert summary["todos"] == 0
    assert "today_tokens" in summary

    s = _get_json(f"{base}/api/sessions/{session.id}/summary")
    assert s["session_id"] == session.id
    assert s["line"] == "没有文件改动"

    msg = _req("POST", f"{base}/api/inbox", {"title": "手动一条", "body": "b", "level": "warn"})
    assert msg["source"] == "manual"
    assert _get_json(f"{base}/api/inbox")[0]["title"] == "手动一条"
    assert _get_json(f"{base}/api/inbox?unread=1")[0]["read"] is False

    _req("POST", f"{base}/api/inbox/{msg['id']}/read", {})
    assert not _get_json(f"{base}/api/inbox?unread=1")

    todo = _req(
        "POST",
        f"{base}/api/todos",
        {"text": "备忘", "kind": "session", "ref": {"session_id": session.id}},
    )
    assert todo["kind"] == "session"
    assert len(_get_json(f"{base}/api/todos")) == 1
    _req("PATCH", f"{base}/api/todos/{todo['id']}", {"done": True})
    assert _get_json(f"{base}/api/todos")[0]["done"] is True
    _req("DELETE", f"{base}/api/todos/{todo['id']}")
    assert _get_json(f"{base}/api/todos") == []


def test_panel_api_bad_input(config, sa_home):
    base = _serve(config)
    for url, data in [
        (f"{base}/api/inbox", {"body": "没标题"}),
        (f"{base}/api/todos", {"text": "  "}),
    ]:
        try:
            _req("POST", url, data)
        except urllib.error.HTTPError as e:
            assert e.code == 400
        else:
            raise AssertionError("应当 400")
