import os
import sys
from pathlib import Path

import pytest

from simpleagent.tools import ToolRegistry
from simpleagent.tools.base import ToolContext, ToolError
from simpleagent.tools.read_file import ReadFileArgs, read_file

read_file_module = sys.modules["simpleagent.tools.read_file"]


def ctx(cwd: Path) -> ToolContext:
    return ToolContext(cwd=cwd)


async def read(cwd: Path, path: str, **kwargs) -> str:
    """@tool 装饰后拿到的是 Tool 对象，真正执行的函数在 .fn 上。"""
    return await read_file.fn(ReadFileArgs(path=path, **kwargs), ctx(cwd))


def resolved(tmp_path: Path, name: str) -> str:
    return str((tmp_path / name).resolve())


async def test_reads_with_line_numbers(tmp_path: Path):
    (tmp_path / "a.txt").write_text("first\nsecond\nthird\n")
    text = await read(tmp_path, "a.txt")
    assert text == (f"{resolved(tmp_path, 'a.txt')}（共 3 行）\n1\tfirst\n2\tsecond\n3\tthird\n")


async def test_offset_and_limit_read_part_of_file(tmp_path: Path):
    (tmp_path / "a.txt").write_text("".join(f"line{i}\n" for i in range(1, 11)))
    text = await read(tmp_path, "a.txt", offset=4, limit=3)
    assert "4\tline4\n5\tline5\n6\tline6\n" in text
    assert "[还有 4 行没读；用 offset=7 继续]" in text


async def test_line_numbers_are_padded_from_start_offset(tmp_path: Path):
    (tmp_path / "a.txt").write_text("".join(f"line{i}\n" for i in range(1, 21)))
    text = await read(tmp_path, "a.txt", offset=9, limit=2)
    assert " 9\tline9\n10\tline10\n" in text  # 行号右对齐，模型能照抄 offset


async def test_empty_file(tmp_path: Path):
    (tmp_path / "empty.txt").write_text("")
    text = await read(tmp_path, "empty.txt")
    assert "共 0 行" in text
    assert "(空文件)" in text


async def test_offset_beyond_end_of_file(tmp_path: Path):
    (tmp_path / "a.txt").write_text("only one\n")
    with pytest.raises(ToolError, match="offset=5 超出范围"):
        await read(tmp_path, "a.txt", offset=5)


async def test_missing_file_and_directory(tmp_path: Path):
    with pytest.raises(ToolError, match="文件不存在"):
        await read(tmp_path, "nope.txt")
    (tmp_path / "d").mkdir()
    with pytest.raises(ToolError, match="是一个目录"):
        await read(tmp_path, "d")


async def test_binary_file_is_rejected(tmp_path: Path):
    (tmp_path / "a.bin").write_bytes(b"abc\x00\x01\x02")
    with pytest.raises(ToolError, match="二进制"):
        await read(tmp_path, "a.bin")


async def test_non_utf8_text_is_rejected(tmp_path: Path):
    (tmp_path / "a.txt").write_bytes(b"abc\xff\xfe")  # 没有 NUL：不是二进制，但解不出 UTF-8
    with pytest.raises(ToolError, match="UTF-8"):
        await read(tmp_path, "a.txt")


async def test_large_file_can_be_read_by_offset(tmp_path: Path):
    # 3MB 的文件以前直接被拒绝，offset/limit 也读不了；现在流式读取，只返回请求的那几行
    (tmp_path / "big.log").write_text("".join(f"{i:07d} {'x' * 90}\n" for i in range(1, 30001)))
    text = await read(tmp_path, "big.log", offset=20000, limit=2)
    assert "（共 30000 行）" in text
    assert "20000\t0020000 " in text and "20001\t0020001 " in text
    assert "[还有 9999 行没读；用 offset=20002 继续]" in text


async def test_output_char_budget_stops_early_with_offset_hint(tmp_path: Path):
    (tmp_path / "wide.txt").write_text("".join(f"{'y' * 1000}\n" for _ in range(100)))
    text = await read(tmp_path, "wide.txt")
    budget = read_file_module.MAX_OUTPUT_CHARS
    assert len(text) < budget + 2000
    assert f"（本次输出已达约 {budget:,} 字符上限）；用 offset=31 继续]" in text


async def test_first_line_is_returned_even_if_over_budget(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(read_file_module, "MAX_OUTPUT_CHARS", 10)
    (tmp_path / "a.txt").write_text("a" * 50 + "\nsecond\n")
    text = await read(tmp_path, "a.txt")
    assert "1\t" + "a" * 50 in text  # 一行都不返回的话，模型永远前进不了
    assert "用 offset=2 继续" in text


async def test_very_long_line_is_truncated(tmp_path: Path):
    (tmp_path / "min.js").write_text("z" * 5000 + "\nok\n")
    text = await read(tmp_path, "min.js")
    assert "z" * 2000 + "…[本行共 5,000 字符，已截断]" in text
    assert "2\tok" in text


async def test_line_numbers_only_split_on_newline(tmp_path: Path):
    # \f 和 \u2028 不算换行：行号要和 grep -n、编辑器一致
    (tmp_path / "a.txt").write_text("one\fstill one\u2028same\ntwo\n")
    text = await read(tmp_path, "a.txt")
    assert "（共 2 行）" in text
    assert "2\ttwo" in text


async def test_crlf_line_endings_are_stripped(tmp_path: Path):
    (tmp_path / "a.txt").write_bytes(b"first\r\nsecond\r\n")
    text = await read(tmp_path, "a.txt")
    assert "1\tfirst\n2\tsecond\n" in text


@pytest.mark.skipif(
    not hasattr(os, "geteuid") or os.geteuid() == 0, reason="需要 POSIX 权限且不能是 root"
)
async def test_unreadable_file_reports_permission(tmp_path: Path):
    path = tmp_path / "secret.txt"
    path.write_text("hi")
    path.chmod(0)
    try:
        with pytest.raises(ToolError, match="没有权限读取"):  # 以前会误报成“二进制文件”
            await read(tmp_path, "secret.txt")
    finally:
        path.chmod(0o644)


async def test_registry_does_not_truncate_read_file(tmp_path: Path):
    """read_file 自己分页；注册表截断会把“用 offset 继续”的提示截掉，还会另存一份副本。"""
    (tmp_path / "a.txt").write_text("".join(f"line{i}\n" for i in range(1, 1001)))
    registry = ToolRegistry([read_file], max_output_chars=1000, max_output_lines=10)
    ctx = ToolContext(cwd=tmp_path, output_dir=tmp_path / "outputs")
    call = {
        "id": "c1",
        "function": {"name": "read_file", "arguments": '{"path": "a.txt", "limit": 600}'},
    }
    result = await registry.execute(call, ctx)
    assert "输出过长已截断" not in result.content
    assert "[还有 400 行没读；用 offset=601 继续]" in result.content
    assert not (tmp_path / "outputs").exists()
