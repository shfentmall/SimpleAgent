"""事件总线：把 agent 产出的事件帧按 session 分发给所有订阅者（SSE 连接）。

设计要点：
- 单 session 内 seq 单调递增，既是 SSE 的 `id`（用于 Last-Event-ID 断线续传），也用于客户端去重。
- 保留每个 session 最近的历史帧，新订阅者带 `Last-Event-ID` 时能重放断线期间的帧。
- 全部用线程安全原语（Lock + queue.Queue）：发布方在 runner 的 asyncio 线程，订阅方在
  HTTP server 的线程，两边通过队列解耦。
- 不依赖 asyncio：SSE handler 是同步的 http.server 线程，从队列阻塞取帧即可。
"""

from __future__ import annotations

import json
import queue
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

# 每个 session 保留的历史帧上限，防止长会话内存无限增长
HISTORY_LIMIT = 2000


def _now() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="milliseconds")


@dataclass
class Frame:
    """一帧事件。seq / ts 由总线在 publish 时填写。"""

    session_id: str
    type: str
    payload: dict[str, Any]
    seq: int = 0
    ts: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "session_id": self.session_id,
            "type": self.type,
            "ts": self.ts,
            "payload": self.payload,
        }


class EventBus:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        # session_id -> 订阅队列列表
        self._subs: dict[str, list[queue.Queue[Frame]]] = {}
        # session_id -> 下一个 seq（已发放 +1）
        self._seqs: dict[str, int] = {}
        # session_id -> 历史帧（用于重放）
        self._history: dict[str, list[Frame]] = {}

    def subscribe(
        self, session_id: str, last_event_id: str | None = None
    ) -> tuple[queue.Queue[Frame], list[Frame]]:
        """订阅一个 session。返回 (队列, 重放帧列表)。

        重放帧 = 历史里 seq 大于 last_event_id 的帧；不带 last_event_id 则重放为空。
        """
        q: queue.Queue[Frame] = queue.Queue()
        with self._lock:
            self._subs.setdefault(session_id, []).append(q)
            history = self._history.get(session_id, [])
            start = int(last_event_id) if last_event_id and last_event_id.isdigit() else 0
            replay = [f for f in history if f.seq > start]
        return q, replay

    def unsubscribe(self, session_id: str, q: queue.Queue[Frame]) -> None:
        with self._lock:
            subs = self._subs.get(session_id)
            if subs and q in subs:
                subs.remove(q)

    def publish(self, frame: Frame) -> None:
        """给帧分配 seq / ts，记入历史，并投递给所有订阅者。"""
        with self._lock:
            seq = self._seqs.get(frame.session_id, 0) + 1
            self._seqs[frame.session_id] = seq
            frame.seq = seq
            frame.ts = _now()
            history = self._history.setdefault(frame.session_id, [])
            history.append(frame)
            if len(history) > HISTORY_LIMIT:
                del history[: len(history) - HISTORY_LIMIT]
            queues = list(self._subs.get(frame.session_id, []))
        for q in queues:
            q.put(frame)

    def next_seq(self, session_id: str) -> int:
        """预览下一个 seq（只读，用于测试或客户端提示；publish 仍会真正分配）。"""
        with self._lock:
            return self._seqs.get(session_id, 0) + 1


def frame_to_sse(frame: Frame) -> str:
    """把一帧渲染成 SSE 文本。seq 作为事件 id，客户端断线后用它做 Last-Event-ID。"""
    data = json.dumps(frame.to_json(), ensure_ascii=False)
    return f"id: {frame.seq}\nevent: {frame.type}\ndata: {data}\n\n"
