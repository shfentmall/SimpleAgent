import asyncio
import sys
import threading
from pathlib import Path

import pytest

from simpleagent.tools import walk
from simpleagent.tools.base import ToolContext, ToolError
from simpleagent.tools.glob import GlobArgs, compile_pattern, glob
from simpleagent.tools.grep import GrepArgs, grep
from simpleagent.tools.walk import iter_files, run_in_thread

grep_module = sys.modules["simpleagent.tools.grep"]


def ctx(cwd: Path) -> ToolContext:
    return ToolContext(cwd=cwd)


async def run_glob(cwd: Path, pattern: str, **kwargs) -> str:
    return await glob.fn(GlobArgs(pattern=pattern, **kwargs), ctx(cwd))


async def run_grep(cwd: Path, pattern: str, **kwargs) -> str:
    return await grep.fn(GrepArgs(pattern=pattern, **kwargs), ctx(cwd))


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("import os\nclass Agent:\n    pass\n")
    (tmp_path / "src" / "b.txt").write_text("agent note\n")
    (tmp_path / "src" / "deep").mkdir()
    (tmp_path / "src" / "deep" / "c.py").write_text("agent = 1\n")
    # 忽略目录里的文件不应该被找到
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.py").write_text("agent = 2\n")
    return tmp_path


# ---------------------------------------------------------------------- glob


async def test_glob_finds_files_recursively(tree: Path):
    text = await run_glob(tree, "**/*.py")
    assert "匹配 2 个文件" in text
    assert [line for line in text.splitlines()[1:] if line] == ["src/a.py", "src/deep/c.py"]


async def test_glob_honours_start_path_and_limit(tree: Path):
    text = await run_glob(tree, "**/*.py", path="src", limit=1)
    assert "匹配 2 个文件" in text
    assert "a.py" in text and "src/a.py" not in text  # 路径相对起点
    assert "还有 1 个没列出" in text


async def test_glob_no_match(tmp_path: Path):
    text = await run_glob(tmp_path, "**/*.rs")
    assert "匹配 0 个文件" in text
    assert "(没有匹配的文件)" in text


async def test_glob_missing_path(tmp_path: Path):
    with pytest.raises(ToolError, match="路径不存在"):
        await run_glob(tmp_path, "*", path="nope")


@pytest.mark.parametrize(
    ("pattern", "path", "matches"),
    [
        ("**/*.py", "a.py", True),
        ("**/*.py", "x/y/z.py", True),
        ("*.py", "a.py", True),
        ("*.py", "x/a.py", False),  # * 不跨目录
        ("src/**/*.py", "src/a.py", True),
        ("src/**/*.py", "lib/a.py", False),
        ("a?.py", "ab.py", True),
        ("a?.py", "abc.py", False),
        ("**", "a/b/c", True),
    ],
)
def test_compile_pattern(pattern: str, path: str, matches: bool):
    assert bool(compile_pattern(pattern).match(path)) is matches


# ---------------------------------------------------------------------- grep


async def test_grep_finds_matches_with_line_numbers(tree: Path):
    text = await run_grep(tree, r"class \w+")
    assert "匹配 1 条" in text
    assert "src/a.py:2:class Agent:" in text


async def test_grep_include_filters_files(tree: Path):
    text = await run_grep(tree, "agent", include="*.py")
    assert "src/deep/c.py:1:agent = 1" in text
    assert "b.txt" not in text


async def test_grep_case_insensitive(tree: Path):
    text = await run_grep(tree, "AGENT", case_sensitive=False)
    assert "匹配 3 条" in text  # a.py 的 class Agent、c.py、b.txt
    assert "(没有匹配)" in await run_grep(tree, "AGENT")


async def test_grep_limit_stops_early(tree: Path):
    text = await run_grep(tree, "agent|a|import|pass|class", limit=2)
    assert "匹配 2 条" in text
    assert "可能还有更多匹配" in text


async def test_grep_single_file(tree: Path):
    text = await run_grep(tree, "import", path="src/a.py")
    assert "a.py:1:import os" in text


async def test_grep_invalid_regex(tmp_path: Path):
    with pytest.raises(ToolError, match="不是合法的正则表达式"):
        await run_grep(tmp_path, "[unclosed")


async def test_grep_skips_binary_and_reports(tmp_path: Path):
    (tmp_path / "a.bin").write_bytes(b"agent\x00\x01")
    (tmp_path / "a.txt").write_text("agent here\n")
    text = await run_grep(tmp_path, "agent")
    assert "a.txt:1:agent here" in text
    assert "已跳过 1 个" in text


async def test_grep_missing_path(tmp_path: Path):
    with pytest.raises(ToolError, match="路径不存在"):
        await run_grep(tmp_path, "x", path="nope")


# ---------------------------------------------------------------------- walk


async def test_symlinks_to_dirs_and_broken_links_are_not_files(tmp_path: Path):
    (tmp_path / "real").mkdir()
    (tmp_path / "real" / "a.py").write_text("x = 1\n")
    (tmp_path / "link_dir").symlink_to(tmp_path / "real")
    (tmp_path / "broken.py").symlink_to(tmp_path / "missing.py")
    (tmp_path / "link.py").symlink_to(tmp_path / "real" / "a.py")  # 指向文件的链接照常算文件
    text = await run_glob(tmp_path, "**/*")
    assert [line for line in text.splitlines()[1:] if line] == ["link.py", "real/a.py"]
    assert "已跳过" not in await run_grep(tmp_path, "x")


def test_iter_files_stops_when_asked(tmp_path: Path):
    for i in range(3):
        (tmp_path / f"d{i}").mkdir()
        (tmp_path / f"d{i}" / "f.txt").write_text("")
    stop = threading.Event()
    files = iter_files(tmp_path, stop)
    next(files)
    stop.set()
    assert list(files) == []


async def test_run_in_thread_signals_stop_on_cancel():
    started, stopped = threading.Event(), threading.Event()

    def work(stop: threading.Event) -> None:
        started.set()
        stopped.set() if stop.wait(timeout=5) else None

    task = asyncio.create_task(run_in_thread(work))
    while not started.is_set():
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await asyncio.to_thread(stopped.wait, 2)


async def test_grep_line_numbers_match_read_file(tmp_path: Path):
    # \f 不算换行：grep 报的行号拿去 read_file 要能对上
    (tmp_path / "a.txt").write_text("one\ftwo\nneedle\n")
    assert "a.txt:2:needle" in await run_grep(tmp_path, "needle")


async def test_grep_stops_walking_after_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    for i in range(20):
        (tmp_path / f"f{i:02d}.txt").write_text("hit\n")
    opened: list[Path] = []
    real = walk.iter_lines

    def spy(path: Path):
        opened.append(path)
        return real(path)

    monkeypatch.setattr(grep_module, "iter_lines", spy)
    text = await run_grep(tmp_path, "hit", limit=3)
    assert "匹配 3 条" in text
    assert len(opened) == 3  # 凑够 limit 就停，不再读剩下的文件
