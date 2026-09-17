"""控制面板的落盘数据：消息（inbox）与备忘（todos）。

放在 `~/.simpleagent/panel/` 下，跟 spaces/ 同一套习惯：

- `inbox.jsonl`：只追加。消息是流水，改的只是「已读」这种个别字段，所以已读状态
  另外记在 `read.json` 里，不去改已经写出去的行（改中间行要重写整个文件）。
- `todos.json`：可变，整体原子写（先写临时文件再 rename），断电不会写成半截。

inbox 的 source 是开放的字符串（`system` / `schedule` / `mail` / ...），
v1 只有 system 会真的写东西，其它留给 M4 定时任务和邮件适配器——
这样以后接入新来源不用改数据模型。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from simpleagent.config import home_dir

PANEL_DIRNAME = "panel"
INBOX_FILENAME = "inbox.jsonl"
READ_FILENAME = "read.json"
TODOS_FILENAME = "todos.json"


def _now() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="milliseconds")


def _new_id(prefix: str) -> str:
    """跟空间 / 会话同款的 id：`<前缀>_<毫秒时间戳>_<随机>`。"""
    import secrets
    import time

    return f"{prefix}_{int(time.time() * 1000)}_{secrets.token_hex(3)}"


@dataclass
class InboxItem:
    """一条进站消息。ref 指向要看的对象：`{"space_id":..., "session_id":...}` 或 `{"url":...}`。"""

    id: str
    source: str  # system | schedule | mail | ...
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
    def __init__(self, home: Path | None = None) -> None:
        self.home = home or home_dir()
        self.dir = self.home / PANEL_DIRNAME

    # ------------------------------------------------------------------ 路径
    def _inbox_file(self) -> Path:
        return self.dir / INBOX_FILENAME

    def _read_file(self) -> Path:
        return self.dir / READ_FILENAME

    def _todos_file(self) -> Path:
        return self.dir / TODOS_FILENAME

    # ------------------------------------------------------------------ 消息
    def add_message(
        self,
        *,
        source: str,
        title: str,
        body: str = "",
        level: str = "info",
        ref: dict[str, str] | None = None,
    ) -> InboxItem:
        """追加一条消息。同一时刻的同类消息不合并——控制面板要的就是流水感。"""
        self.dir.mkdir(parents=True, exist_ok=True)
        item = InboxItem(
            id=_new_id("ms"),
            source=source,
            title=title,
            body=body,
            ts=_now(),
            level=level,
            ref=ref or {},
        )
        with self._inbox_file().open("a", encoding="utf-8") as f:
            f.write(json.dumps(item.to_dict(), ensure_ascii=False) + "\n")
        return item

    def list_messages(self, limit: int = 50, unread_only: bool = False) -> list[dict[str, Any]]:
        """最近的消息，新的在前。已读状态从 read.json 补上。"""
        path = self._inbox_file()
        if not path.exists():
            return []
        items: list[InboxItem] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                items.append(InboxItem.from_dict(json.loads(line)))
        read = self._read_ids()
        out = []
        for it in reversed(items):  # 新的在前
            d = it.to_dict()
            d["read"] = it.id in read
            if unread_only and d["read"]:
                continue
            out.append(d)
            if len(out) >= limit:
                break
        return out

    def unread_count(self) -> int:
        path = self._inbox_file()
        if not path.exists():
            return 0
        read = self._read_ids()
        total = sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
        return max(0, total - len(read))

    def mark_read(self, item_id: str) -> bool:
        """标记一条已读。id 为 `*`（或 all）时全部标记。返回是否真的改了。"""
        path = self._inbox_file()
        if not path.exists():
            return False
        read = self._read_ids()
        if item_id in ("*", "all"):
            ids = [
                json.loads(line)["id"]
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            if set(ids) <= read:
                return False
            read |= set(ids)
        else:
            if item_id in read:
                return False
            read.add(item_id)
        self._write_read(read)
        return True

    def _read_ids(self) -> set[str]:
        path = self._read_file()
        if not path.exists():
            return set()
        try:
            return set(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            return set()

    def _write_read(self, ids: set[str]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        _write_atomic(self._read_file(), json.dumps(sorted(ids), ensure_ascii=False))

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
