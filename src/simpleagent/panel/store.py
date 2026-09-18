"""控制面板的落盘数据：消息（inbox）与备忘（todos）。

放在 `~/.simpleagent/panel/` 下，跟 spaces/ 同一套习惯：

- `inbox.jsonl`：只追加。服务端和 `sa inbox push`（另一个进程）都往里追加整行，
  谁都不改已经写出去的行（改中间行要重写整个文件，还会和别的写入方打架）。
- `state.json`：每条消息的可变状态——`read_at`（第一次点开）、`archived_at`（手动归档）。
  只有服务端写它，整体原子写。
- `todos.json`：可变，整体原子写（先写临时文件再 rename），断电不会写成半截。

**归档是算出来的，不是搬过去的**：

    archive_at = archived_at or read_at + archive_after     # 没点开过 → 永不自动归档
    archived   = now >= archive_at

不需要后台定时器，服务没开的那段时间也不会漏；改了 archive_after 对老消息立即生效。

inbox 的 source 是开放的字符串（`system` / `schedule` / `mail` / `cli` / ...），
接入新来源不用改数据模型。点开后做什么也不单独建字段，由 `ref` 推出来（见 `_action`）。
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from simpleagent.config import home_dir

PANEL_DIRNAME = "panel"
INBOX_FILENAME = "inbox.jsonl"
STATE_FILENAME = "state.json"
LEGACY_READ_FILENAME = "read.json"  # W5 的旧格式：只有已读 id 列表，没有时间
TODOS_FILENAME = "todos.json"

DEFAULT_ARCHIVE_AFTER_MINUTES = 30
# 列表只给预览，全文走 get_message：外部报告可能很长，归档一拉 200 条不该把全文都带上
PREVIEW_CHARS = 200
# 超长正文截断而不是拒收：例行任务的结论不该因为太长整条丢掉
MAX_BODY_CHARS = 64_000
LEVELS = ("info", "success", "warn", "error")

View = Literal["active", "archived"]


def _now_dt() -> datetime:
    return datetime.now(UTC).astimezone()


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="milliseconds")


def _now() -> str:
    return _iso(_now_dt())


def _parse(ts: Any) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.astimezone()  # 没带时区的按本地时间算


def _new_id(prefix: str) -> str:
    """跟空间 / 会话同款的 id：`<前缀>_<毫秒时间戳>_<随机>`。"""
    import secrets
    import time

    return f"{prefix}_{int(time.time() * 1000)}_{secrets.token_hex(3)}"


def _clip_body(body: str) -> str:
    if len(body) <= MAX_BODY_CHARS:
        return body
    return body[:MAX_BODY_CHARS] + f"\n\n…（正文过长已截断，原文 {len(body)} 字）"


def _preview(body: str) -> str:
    flat = " ".join(body.split())
    return flat if len(flat) <= PREVIEW_CHARS else flat[:PREVIEW_CHARS].rstrip() + "…"


def _action(ref: dict[str, str]) -> str:
    """点开之后做什么：指向框架内的会话就跳过去，否则当纯文本看全文。"""
    return "session" if ref.get("space_id") and ref.get("session_id") else "text"


@dataclass
class InboxItem:
    """一条进站消息。ref 指向要看的对象：`{"space_id":..., "session_id":...}` 或 `{"url":...}`。"""

    id: str
    source: str  # system | schedule | mail | cli | ...
    title: str
    body: str = ""
    ts: str = ""
    level: str = "info"  # info | success | warn | error
    ref: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> InboxItem:
        return cls(
            id=d.get("id", ""),
            source=d.get("source", "system"),
            title=d.get("title", ""),
            body=d.get("body", ""),
            ts=d.get("ts", ""),
            level=d.get("level", "info"),
            ref=dict(d.get("ref") or {}),
        )


@dataclass
class TodoItem:
    """一条备忘：纯文本，或引用某个空间的某个 session（点一下能跳过去）。"""

    id: str
    text: str
    done: bool = False
    kind: Literal["text", "session"] = "text"
    ref: dict[str, str] = field(default_factory=dict)
    created_at: str = ""
    done_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TodoItem:
        return cls(
            id=d.get("id", ""),
            text=d.get("text", ""),
            done=bool(d.get("done", False)),
            kind=d.get("kind", "text"),
            ref=dict(d.get("ref") or {}),
            created_at=d.get("created_at", ""),
            done_at=d.get("done_at"),
        )


class PanelStore:
    def __init__(
        self,
        home: Path | None = None,
        *,
        archive_after_minutes: int = DEFAULT_ARCHIVE_AFTER_MINUTES,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.home = home or home_dir()
        self.dir = self.home / PANEL_DIRNAME
        self.archive_after = timedelta(minutes=archive_after_minutes)
        # 可注入的时钟：测试里直接拨表验证「30 分钟后归档」，不用真的等
        self._clock = clock or _now_dt
        # HTTP 层是多线程的：两个请求同时改 state.json 会互相覆盖，读-改-写要串起来
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ 路径
    def _inbox_file(self) -> Path:
        return self.dir / INBOX_FILENAME

    def _state_file(self) -> Path:
        return self.dir / STATE_FILENAME

    def _todos_file(self) -> Path:
        return self.dir / TODOS_FILENAME

    # ------------------------------------------------------------------ 消息：写
    def add_message(
        self,
        *,
        source: str,
        title: str,
        body: str = "",
        level: str = "info",
        ref: dict[str, str] | None = None,
    ) -> InboxItem:
        """追加一条消息。同一时刻的同类消息不合并——控制面板要的就是流水感。

        服务端和 `sa inbox push` 可能同时追加：整行编码好后用一次 `os.write`（O_APPEND）写出去。
        不用带缓冲的文件对象，是因为缓冲会把长行拆成几次写，两个进程的内容就可能交错成一行。
        """
        self.dir.mkdir(parents=True, exist_ok=True)
        item = InboxItem(
            id=_new_id("ms"),
            source=source,
            title=title,
            body=_clip_body(body),
            ts=_iso(self._clock()),
            level=level if level in LEVELS else "info",
            ref=ref or {},
        )
        line = (json.dumps(item.to_dict(), ensure_ascii=False) + "\n").encode("utf-8")
        fd = os.open(self._inbox_file(), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)
        return item

    def mark_read(self, item_id: str) -> bool:
        """第一次点开时记下 read_at，归档倒计时从这一刻开始；再点不会重置。

        id 为 `*`（或 all）时把所有未读都标记。返回是否真的改了；不存在的 id 返回 False。
        """
        with self._lock:
            ids = {it.id for it in self._items()}
            targets = ids if item_id in ("*", "all") else ids & {item_id}
            state = self._state()
            now = _iso(self._clock())
            changed = False
            for i in targets:
                st = state.setdefault(i, {"read_at": None, "archived_at": None})
                if not st.get("read_at"):
                    st["read_at"] = now
                    changed = True
            if changed:
                self._write_state(state)
            return changed

    def archive(self, item_id: str) -> dict[str, Any] | None:
        """手动归档，不等倒计时。没点开过的顺带补 read_at：归档的一定算看过。

        返回归档后的条目；id 不存在返回 None。已经在归档里的不动，免得改掉它的归档时间。
        """
        with self._lock:
            item = self._find(item_id)
            if item is None:
                return None
            state = self._state()
            now = self._clock()
            st = state.setdefault(item_id, {"read_at": None, "archived_at": None})
            if not self._status(st, now)[2]:
                st["read_at"] = st.get("read_at") or _iso(now)
                st["archived_at"] = _iso(now)
                self._write_state(state)
            return self._view(item, st, now, full=False)

    # ------------------------------------------------------------------ 消息：读
    def list_messages(
        self, *, view: View = "active", limit: int = 50, unread_only: bool = False
    ) -> list[dict[str, Any]]:
        """某个视图下的消息，只带预览不带全文。

        active：没归档的，新的在前；archived：按归档时间倒序，刚被归档的在最上面——
        方便找「刚才那条去哪了」。
        """
        now = self._clock()
        state = self._state()
        rows: list[tuple[datetime | None, dict[str, Any]]] = []
        for it in reversed(self._items()):  # 文件是追加顺序，倒过来就是新的在前
            st = state.get(it.id) or {}
            read, archive_at, archived = self._status(st, now)
            if archived != (view == "archived") or (unread_only and read):
                continue
            rows.append((archive_at, self._view(it, st, now, full=False)))
        if view == "archived":
            # 稳定排序：归档时间相同的（比如一起「全部已读」）保持新消息在前
            rows.sort(key=lambda r: r[0] or now, reverse=True)
        return [v for _, v in rows[:limit]]

    def get_message(self, item_id: str) -> dict[str, Any] | None:
        """详情：完整正文 + 状态。"""
        item = self._find(item_id)
        if item is None:
            return None
        return self._view(item, self._state().get(item_id) or {}, self._clock(), full=True)

    def counts(self) -> dict[str, int]:
        """左栏角标用：只读 jsonl 和 state，不碰空间和会话，30 秒轮询一次也不心疼。"""
        now = self._clock()
        state = self._state()
        out = {"unread": 0, "active": 0, "archived": 0}
        for it in self._items():
            read, _, archived = self._status(state.get(it.id) or {}, now)
            out["archived" if archived else "active"] += 1
            if not read:
                out["unread"] += 1
        return out

    def unread_count(self) -> int:
        return self.counts()["unread"]

    # ------------------------------------------------------------------ 消息：内部
    def _items(self) -> list[InboxItem]:
        path = self._inbox_file()
        if not path.exists():
            return []
        items: list[InboxItem] = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue  # 别的进程正写到一半：跳过这行，下次读就完整了
            if isinstance(d, dict) and d.get("id"):
                items.append(InboxItem.from_dict(d))
        return items

    def _find(self, item_id: str) -> InboxItem | None:
        return next((it for it in self._items() if it.id == item_id), None)

    def _status(self, st: dict[str, Any], now: datetime) -> tuple[bool, datetime | None, bool]:
        """(已读, 归档时间, 是否已归档)。归档口径只在这一处。"""
        read_at = _parse(st.get("read_at"))
        archive_at = _parse(st.get("archived_at")) or (
            read_at + self.archive_after if read_at else None
        )
        return read_at is not None, archive_at, archive_at is not None and now >= archive_at

    def _view(
        self, item: InboxItem, st: dict[str, Any], now: datetime, *, full: bool
    ) -> dict[str, Any]:
        read, archive_at, archived = self._status(st, now)
        d = item.to_dict()
        if not full:
            d.pop("body")
        d["preview"] = _preview(item.body)
        d["action"] = _action(item.ref)
        d["read"] = read
        d["read_at"] = st.get("read_at")
        d["archive_at"] = _iso(archive_at) if archive_at else None
        d["archived"] = archived
        return d

    def _state(self) -> dict[str, dict[str, Any]]:
        path = self._state_file()
        if not path.exists():
            return self._migrate_legacy_read()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        return raw if isinstance(raw, dict) else {}

    def _migrate_legacy_read(self) -> dict[str, dict[str, Any]]:
        """W5 的 read.json 只记了「哪些读过」，没记什么时候读的，read_at 取文件的 mtime。

        mtime 多半早就过了归档时限，升级后这些老消息直接进归档，不会冒回列表里占位置。
        """
        legacy = self.dir / LEGACY_READ_FILENAME
        if not legacy.exists():
            return {}
        try:
            ids = json.loads(legacy.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        if not isinstance(ids, list):
            return {}
        read_at = _iso(datetime.fromtimestamp(legacy.stat().st_mtime, UTC).astimezone())
        state = {str(i): {"read_at": read_at, "archived_at": None} for i in ids if i}
        self._write_state(state)
        return state

    def _write_state(self, state: dict[str, dict[str, Any]]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        _write_atomic(self._state_file(), json.dumps(state, ensure_ascii=False, indent=1))

    # ------------------------------------------------------------------ 备忘
    def list_todos(self) -> list[dict[str, Any]]:
        path = self._todos_file()
        if not path.exists():
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
        # 未完成的在前，完成的沉底
        items = [TodoItem.from_dict(d) for d in raw]
        items.sort(key=lambda t: (t.done, t.created_at))
        return [t.to_dict() for t in items]

    def add_todo(
        self, text: str, kind: str = "text", ref: dict[str, str] | None = None
    ) -> TodoItem:
        items = [TodoItem.from_dict(d) for d in self.list_todos()]
        item = TodoItem(
            id=_new_id("td"),
            text=text.strip(),
            kind="session" if kind == "session" else "text",
            ref=ref or {},
            created_at=_now(),
        )
        items.append(item)
        self._write_todos(items)
        return item

    def update_todo(self, todo_id: str, **fields: Any) -> TodoItem | None:
        items = [TodoItem.from_dict(d) for d in self.list_todos()]
        for it in items:
            if it.id != todo_id:
                continue
            if "text" in fields:
                it.text = str(fields["text"]).strip()
            if "done" in fields:
                it.done = bool(fields["done"])
                it.done_at = _now() if it.done else None
            self._write_todos(items)
            return it
        return None

    def delete_todo(self, todo_id: str) -> bool:
        items = [TodoItem.from_dict(d) for d in self.list_todos()]
        left = [it for it in items if it.id != todo_id]
        if len(left) == len(items):
            return False
        self._write_todos(left)
        return True

    def _write_todos(self, items: list[TodoItem]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        data = json.dumps([it.to_dict() for it in items], ensure_ascii=False, indent=2)
        _write_atomic(self._todos_file(), data)


def _write_atomic(path: Path, data: str) -> None:
    """先写临时文件再 rename：写一半崩了不会留下坏掉的正本。"""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(data, encoding="utf-8")
    tmp.replace(path)
