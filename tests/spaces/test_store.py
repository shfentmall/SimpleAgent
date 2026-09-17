"""W1 持久化层测试：空间与会话的落盘、最近 5 规则、标题截断、重载。"""

import threading
from pathlib import Path

import pytest

from simpleagent.spaces.models import SpaceSpec
from simpleagent.spaces.store import SpaceStore, new_id


def store(home: Path) -> SpaceStore:
    return SpaceStore(home=home)


def test_new_id_unique():
    a, b = new_id("sp"), new_id("sp")
    assert a != b and a.startswith("sp_")


def test_create_generic_space_makes_tmp(tmp_path: Path):
    st = store(tmp_path)
    sp = st.create_space(SpaceSpec(name="临时整理", kind="generic"))
    assert sp.kind == "generic"
    assert (tmp_path / "spaces" / sp.id / "space.toml").exists()
    assert (tmp_path / "spaces" / sp.id / "tmp").is_dir()
    assert (tmp_path / "spaces" / sp.id / "sessions").is_dir()


def test_create_agent_space_sets_cwd_and_no_tmp(tmp_path: Path):
    st = store(tmp_path)
    sp = st.create_space(
        SpaceSpec(
            name="SA",
            kind="agent",
            agent_name="claude-code",
            cwd="/Users/x/dev",
            verify_command="pytest -q",
            verify_trigger="on_stop",
        )
    )
    assert sp.agent is not None and sp.agent.cwd == "/Users/x/dev"
    assert sp.verify is not None and sp.verify.command == "pytest -q"
    # agent 空间不建 tmp 目录
    assert not (tmp_path / "spaces" / sp.id / "tmp").exists()


def test_list_spaces_opened_only(tmp_path: Path):
    st = store(tmp_path)
    a = st.create_space(SpaceSpec(name="a", kind="generic"))
    b = st.create_space(SpaceSpec(name="b", kind="generic"))
    st.close_space(b.id)
    assert {s.id for s in st.list_spaces(opened_only=True)} == {a.id}
    assert len(st.list_spaces(opened_only=False)) == 2


def test_recent_five_with_pin_and_running(tmp_path: Path):
    st = store(tmp_path)
    sp = st.create_space(SpaceSpec(name="sp", kind="generic"))
    metas = [st.create_session(sp.id) for _ in range(8)]
    # 第 3 个置顶、第 5 个运行中
    st.update_meta(sp.id, metas[2].id, pinned=True)
    st.update_meta(sp.id, metas[4].id, status="running")

    visible = st.list_sessions(sp.id, limit=5)
    ids = [m.id for m in visible]
    # pin 和 running 永远在，且不占 5 个名额
    assert metas[2].id in ids
    assert metas[4].id in ids
    assert len(ids) <= 1 + 1 + 5
    # pin 排在最前
    assert ids[0] == metas[2].id
    # 隐藏的仍能通过大 limit 取回
    assert len(st.list_sessions(sp.id, limit=100)) == 8


def test_title_from_first_user_message(tmp_path: Path):
    st = store(tmp_path)
    sp = st.create_space(SpaceSpec(name="sp", kind="generic"))
    m = st.create_session(sp.id)
    long_text = "这是一段很长的用户消息，用来测试标题截断是否生效，超过四十字的部分应当被切掉"
    st.append_message(sp.id, m.id, {"role": "user", "content": long_text})
    meta = st.get_session_meta(sp.id, m.id)
    assert meta.title == long_text[:40]
    assert len(meta.title) <= 40


def test_title_supports_multiblock_content(tmp_path: Path):
    st = store(tmp_path)
    sp = st.create_space(SpaceSpec(name="sp", kind="generic"))
    m = st.create_session(sp.id)
    st.append_message(
        sp.id,
        m.id,
        {
            "role": "user",
            "content": [{"type": "text", "text": "先读 conftest"}, {"type": "image", "url": "x"}],
        },
    )
    assert st.get_session_meta(sp.id, m.id).title == "先读 conftest"


def test_load_session_rebuilds_messages(tmp_path: Path):
    st = store(tmp_path)
    sp = st.create_space(SpaceSpec(name="sp", kind="generic"))
    m = st.create_session(sp.id)
    st.append_message(sp.id, m.id, {"role": "user", "content": "hi"})
    st.append_message(sp.id, m.id, {"role": "assistant", "content": "hello"})
    sess = st.load_session(sp.id, m.id)
    assert len(sess.messages) == 2
    assert sess.messages[0]["content"] == "hi"
    assert sess.requests == 2


def test_same_cwd_multiple_spaces_allowed(tmp_path: Path):
    st = store(tmp_path)
    s1 = st.create_space(SpaceSpec(name="c1", kind="agent", agent_name="claude-code", cwd="/p"))
    s2 = st.create_space(SpaceSpec(name="c2", kind="agent", agent_name="opencode", cwd="/p"))
    assert s1.id != s2.id
    assert {x.id for x in st.list_spaces()} == {s1.id, s2.id}


def test_delete_space(tmp_path: Path):
    st = store(tmp_path)
    sp = st.create_space(SpaceSpec(name="sp", kind="generic"))
    assert (tmp_path / "spaces" / sp.id).exists()
    st.delete_space(sp.id)
    assert not (tmp_path / "spaces" / sp.id).exists()


def test_persistence_across_reload(tmp_path: Path):
    st = store(tmp_path)
    sp = st.create_space(SpaceSpec(name="持久化", kind="generic"))
    m = st.create_session(sp.id)
    st.append_message(sp.id, m.id, {"role": "user", "content": "abc"})

    # 新建一个 store 实例，模拟重启
    st2 = SpaceStore(home=tmp_path)
    sp2 = st2.get_space(sp.id)
    assert sp2 is not None and sp2.name == "持久化"
    assert len(st2.list_sessions(sp.id)) == 1
    assert st2.load_session(sp.id, m.id).messages[0]["content"] == "abc"


def test_update_meta_rejects_unknown_field(tmp_path: Path):
    st = store(tmp_path)
    sp = st.create_space(SpaceSpec(name="sp", kind="generic"))
    m = st.create_session(sp.id)
    try:
        st.update_meta(sp.id, m.id, foo="bar")
        pytest.fail("应当拒绝未知字段")
    except ValueError:
        pass


def test_meta_readers_never_see_partial_write(tmp_path: Path):
    # serve 里 runner 线程写 meta、HTTP 线程同时读；写入必须是原子的，读者不能读到空文件
    st = store(tmp_path)
    sp = st.create_space(SpaceSpec(name="sp", kind="generic"))
    m = st.create_session(sp.id)
    stop = threading.Event()
    errors: list[Exception] = []

    def reader() -> None:
        while not stop.is_set():
            try:
                st.get_session_meta(sp.id, m.id)
                st.list_sessions(sp.id)
            except Exception as e:
                errors.append(e)
                return

    t = threading.Thread(target=reader)
    t.start()
    try:
        for i in range(500):
            st.update_meta(sp.id, m.id, title=f"t{i}")
            if errors:
                break
    finally:
        stop.set()
        t.join()
    assert errors == []
    assert list((tmp_path / "spaces" / sp.id / "sessions").glob("*.tmp")) == []
