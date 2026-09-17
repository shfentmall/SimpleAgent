"""Trace：把每次 LLM 请求/响应完整落盘，用来观察真实发出去的上下文。

目录结构：<home>/traces/<session_id>/0001.json, 0002.json, ...
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any


def new_session_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)


class Tracer:
    def __init__(self, root: Path, session_id: str, enabled: bool = True, raw_chunks: bool = False):
        self.dir = root / session_id
        self.enabled = enabled
        self.raw_chunks = raw_chunks
        self._seq = 0

    def record(self, data: dict[str, Any]) -> Path | None:
        if not self.enabled:
            return None
        self._seq += 1
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / f"{self._seq:04d}.json"
        payload = {"seq": self._seq, **data}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), "utf-8")
        return path
