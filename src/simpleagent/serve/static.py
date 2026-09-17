"""把 `src/simpleagent/web/` 下的前端文件喂给浏览器。

设计要点：
- 资源放在包目录里，用 `importlib.resources` 读，装成 wheel / 打包成二进制后照样能用，
  不依赖「当前工作目录在哪」。
- 只认白名单后缀 + 只取文件名（`PurePosixPath(name).name`），目录穿越在这里就被掐掉。
- 这里的职责只有「按名字拿字节」，路由和 MIME 之外的决定都在 `serve/app.py`。
"""

from __future__ import annotations

from importlib import resources
from pathlib import PurePosixPath

MIME_TYPES: dict[str, str] = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".json": "application/json; charset=utf-8",
}

PACKAGE = "simpleagent.web"


def asset_bytes(name: str) -> tuple[bytes, str] | None:
    """按文件名取静态资源，返回 (内容, Content-Type)。

    拿不到（不在白名单、文件不存在、是目录）一律返回 None，由调用方决定 404。
    """
    safe = PurePosixPath(name).name  # 丢掉任何目录成分，防 /assets/../config.py
    suffix = PurePosixPath(safe).suffix
    content_type = MIME_TYPES.get(suffix)
    if content_type is None:
        return None
    try:
        data = resources.files(PACKAGE).joinpath(safe).read_bytes()
    except (FileNotFoundError, IsADirectoryError, OSError, ValueError):
        return None
    return data, content_type
