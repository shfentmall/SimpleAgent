from pathlib import Path

import pytest

from simpleagent.tools.base import ToolContext, ToolError
from simpleagent.tools.edit_file import EditFileArgs, edit_file
from simpleagent.tools.write_file import WriteFileArgs, write_file


def ctx(cwd: Path) -> ToolContext:
    return ToolContext(cwd=cwd)


async def write(cwd: Path, path: str, content: str) -> str:
    return await write_file.fn(WriteFileArgs(path=path, content=content), ctx(cwd))


async def edit(cwd: Path, path: str, old_string: str, new_string: str = "", **kwargs) -> str:
    args = EditFileArgs(path=path, old_string=old_string, new_string=new_string, **kwargs)
    return await edit_file.fn(args, ctx(cwd))


async def test_write_creates_file_and_parent_dirs(tmp_path: Path):
    result = await write(tmp_path, "sub/dir/a.txt", "hello\n")
    assert (tmp_path / "sub" / "dir" / "a.txt").read_text() == "hello\n"
    assert "已新建" in result and "1 行 / 6 字节" in result


async def test_write_overwrites_existing_file(tmp_path: Path):
    (tmp_path / "a.txt").write_text("old content\n")
    result = await write(tmp_path, "a.txt", "new\n")
    assert (tmp_path / "a.txt").read_text() == "new\n"
    assert "已覆盖" in result


async def test_write_rejects_directory(tmp_path: Path):
    (tmp_path / "d").mkdir()
    with pytest.raises(ToolError, match="是一个目录"):
        await write(tmp_path, "d", "x")


async def test_edit_replaces_unique_string_and_returns_diff(tmp_path: Path):
    (tmp_path / "a.py").write_text("def f():\n    return 1\n")
    result = await edit(tmp_path, "a.py", "return 1", "return 2")
    assert (tmp_path / "a.py").read_text() == "def f():\n    return 2\n"
    assert "已修改" in result and "替换 1 处" in result
    assert "-    return 1" in result and "+    return 2" in result  # unified diff


async def test_edit_can_delete_lines(tmp_path: Path):
    (tmp_path / "a.txt").write_text("keep\n drop me\nkeep\n")
    await edit(tmp_path, "a.txt", " drop me\n")
    assert (tmp_path / "a.txt").read_text() == "keep\nkeep\n"


async def test_edit_missing_string_reports_hint(tmp_path: Path):
    (tmp_path / "a.txt").write_text("hello\n")
    with pytest.raises(ToolError, match="没找到 old_string"):
        await edit(tmp_path, "a.txt", "nope")


async def test_edit_ambiguous_match_is_rejected(tmp_path: Path):
    (tmp_path / "a.txt").write_text("x = 1\nx = 1\n")
    with pytest.raises(ToolError, match="出现 2 次"):
        await edit(tmp_path, "a.txt", "x = 1", "x = 2")
    assert (tmp_path / "a.txt").read_text() == "x = 1\nx = 1\n"  # 报错了就没改


async def test_edit_replace_all(tmp_path: Path):
    (tmp_path / "a.txt").write_text("a\na\na\n")
    result = await edit(tmp_path, "a.txt", "a", "b", replace_all=True)
    assert (tmp_path / "a.txt").read_text() == "b\nb\nb\n"
    assert "替换 3 处" in result


async def test_edit_requires_existing_file(tmp_path: Path):
    with pytest.raises(ToolError, match="文件不存在"):
        await edit(tmp_path, "nope.txt", "a")


async def test_edit_rejects_empty_old_string(tmp_path: Path):
    (tmp_path / "a.txt").write_text("hello\n")
    with pytest.raises(ToolError, match="old_string 不能为空"):
        await edit(tmp_path, "a.txt", "")


async def test_edit_preserves_crlf_line_endings(tmp_path: Path):
    path = tmp_path / "win.txt"
    path.write_bytes(b"first\r\nsecond\r\nthird\r\n")
    # 模型给的 old_string 用 \n 换行，也要能匹配 CRLF 文件
    result = await edit(tmp_path, "win.txt", "first\nsecond", "first\nSECOND")
    assert path.read_bytes() == b"first\r\nSECOND\r\nthird\r\n"  # 其他行的换行符不变
    assert "+SECOND" in result and "\r" not in result


async def test_edit_keeps_lf_files_lf(tmp_path: Path):
    path = tmp_path / "unix.txt"
    path.write_bytes(b"a\nb\n")
    await edit(tmp_path, "unix.txt", "b", "c")
    assert path.read_bytes() == b"a\nc\n"
