"""把原理图网页截成 PNG：一张全页长图，再给每个分节各截一张。

用法：
    uv run --with pillow python .claude/skills/explainer/shot.py \\
        <页面.html> <输出目录> <文件名前缀> <分节id>:<图名> [<分节id>:<图名> ...]

例：
    ... shot.py page.html out 权限模式 before-after:以前和现在 decide:什么时候问你
产出 out/权限模式-0-全页.png、out/权限模式-1-以前和现在.png、out/权限模式-2-什么时候问你.png。

页面是 Artifact 用的片段（没有 <!doctype>），这里包一层固定浅色主题的外壳再交给无头 Chrome，
`?only=<分节id>` 时只显示那一节。截完按背景色裁边，四周各留 48 个 CSS 像素。
"""

import contextlib
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from PIL import Image, ImageChops

CHROME = os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
WIDTH = 1000  # CSS 像素
SCALE = 2
PAD = 48 * SCALE

HEAD = """<!doctype html><html data-theme="light"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"></head><body style="margin:0">
"""
TAIL = """
<script>
  (function () {
    var only = new URLSearchParams(location.search).get("only");
    if (!only) return;
    document.querySelectorAll(".x-block").forEach(function (el) { el.hidden = el.id !== only; });
    var foot = document.querySelector("footer");
    if (foot) foot.hidden = true;
  })();
</script></body></html>
"""


def chrome_shot(url: str, out: Path, height: int) -> None:
    # 无头 Chrome 常常写完截图却不退出，所以不等它退出：文件出现且大小稳定就结束整个进程组。
    # 每次用独立 profile，45 秒还没出图就重试。
    for attempt in range(3):
        out.unlink(missing_ok=True)
        profile = tempfile.mkdtemp(prefix="chrome-shot-")
        proc = subprocess.Popen(
            [
                CHROME,
                "--headless=new",
                "--disable-gpu",
                "--hide-scrollbars",
                "--no-first-run",
                "--no-default-browser-check",
                f"--user-data-dir={profile}",
                "--virtual-time-budget=3000",
                f"--force-device-scale-factor={SCALE}",
                f"--window-size={WIDTH},{height}",
                f"--screenshot={out}",
                url,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            last_size = -1
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                time.sleep(0.5)
                size = out.stat().st_size if out.exists() else -1
                if size > 0 and size == last_size:
                    return
                last_size = size
                if proc.poll() is not None and size <= 0:
                    break  # Chrome 自己退出了却没出图
            print(f"  第 {attempt + 1} 次截图没出图，重试", flush=True)
        finally:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            shutil.rmtree(profile, ignore_errors=True)
    sys.exit(f"截图失败：{url}")


def shot(render: Path, out: Path, only: str | None) -> None:
    url = render.as_uri() + (f"?only={only}" if only else "")
    raw = out.with_name(f".raw-{out.name}")
    height = 2400 if only else 8000
    for _ in range(4):
        chrome_shot(url, raw, height)
        img = Image.open(raw).convert("RGB")
        bg = img.getpixel((2, 2))
        box = (
            ImageChops.difference(img, Image.new("RGB", img.size, bg))
            .point(lambda v: 255 if v > 6 else 0)
            .getbbox()
        )
        if box is None:
            sys.exit(f"截出来是空白：{url}（分节 id 写错了？）")
        if box[3] < img.height - 2:
            break
        height *= 2  # 内容碰到窗口底边，说明没截全，窗口加高再截
    else:
        sys.exit(f"页面太长，截不全：{url}")
    raw.unlink()
    left, top, right, bottom = box
    canvas = Image.new("RGB", (right - left + 2 * PAD, bottom - top + 2 * PAD), bg)
    canvas.paste(img.crop(box), (PAD, PAD))
    canvas.save(out, optimize=True)
    print(f"{out}  {canvas.size[0]}x{canvas.size[1]}", flush=True)


def main() -> None:
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    page, out_dir, prefix = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
    sections = [arg.split(":", 1) for arg in sys.argv[4:]]
    out_dir.mkdir(parents=True, exist_ok=True)
    render = out_dir / ".render.html"
    render.write_text(HEAD + page.read_text() + TAIL)
    try:
        shot(render, out_dir / f"{prefix}-0-全页.png", None)
        for i, (sid, name) in enumerate(sections, 1):
            shot(render, out_dir / f"{prefix}-{i}-{name}.png", sid)
    finally:
        render.unlink()


if __name__ == "__main__":
    main()
