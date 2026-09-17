import os
from pathlib import Path

import pytest

from simpleagent.tools import ToolContext, ToolError
from simpleagent.tools.list_dir import ListDirArgs, format_size, list_dir


def make_tree(root: Path, spec: dict) -> None:
    """dict 表示目录，str 表示文件内容。"""
    for name, value in spec.items():
        path = root / name
        if isinstance(value, dict):
            path.mkdir()
            make_tree(path, value)
        else:
            path.write_text(value)


async def ls(cwd: Path, **kwargs) -> list[str]:
    output = await list_dir.fn(ListDirArgs(**kwargs), ToolContext(cwd=cwd))
    return output.splitlines()


async def test_dirs_first_sorted_with_sizes(tmp_path: Path):
    make_tree(
        tmp_path,
        {"b.txt": "x" * 2048, "A.md": "hi", "src": {"main.py": ""}, "docs": {}, ".DS_Store": ""},
    )
    assert await ls(tmp_path) == [
        str(tmp_path.resolve()),
        "docs/",
        "src/",
        "  main.py 0B",
        "A.md 2B",
        "b.txt 2.0K",
    ]


async def test_depth_controls_expansion(tmp_path: Path):
    make_tree(tmp_path, {"a": {"b": {"c": {"d.txt": ""}}}})
    assert (await ls(tmp_path, depth=1))[1:] == ["a/"]
    assert (await ls(tmp_path, depth=3))[1:] == ["a/", "  b/", "    c/"]


async def test_ignored_dirs_listed_but_not_expanded(tmp_path: Path):
    make_tree(tmp_path, {".git": {"HEAD": ""}, "node_modules": {"x": {}}, "app.py": ""})
    assert (await ls(tmp_path, depth=3))[1:] == [
        ".git/ [已忽略]",
        "node_modules/ [已忽略]",
        "app.py 0B",
    ]


async def test_limit_truncates_deepest_level_first(tmp_path: Path):
    make_tree(
        tmp_path, {"a": {f"a{i}.txt": "" for i in range(5)}, "b": {"b.txt": ""}, "top.txt": ""}
    )
    lines = await ls(tmp_path, limit=4)
    # 按层扫描：第一层 3 项全部保留，剩下 1 个名额给 a/ 的第一个子项
    assert lines[1:5] == ["a/", "  a0.txt 0B", "b/", "top.txt 0B"]
    assert lines[5].startswith("[已达到 limit=4")
    assert len(lines) == 6


async def test_limit_exactly_reached_is_not_truncated(tmp_path: Path):
    make_tree(tmp_path, {"x": {}, "y.txt": ""})
    assert (await ls(tmp_path, limit=2))[1:] == ["x/", "y.txt 0B"]


async def test_relative_path_resolved_against_ctx_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    make_tree(tmp_path, {"sub": {"f.txt": ""}})
    monkeypatch.chdir("/")
    assert await ls(tmp_path, path="sub") == [str(tmp_path.resolve() / "sub"), "f.txt 0B"]


async def test_empty_dir(tmp_path: Path):
    assert await ls(tmp_path) == [str(tmp_path.resolve()), "(空目录)"]


async def test_missing_path_and_file_raise_tool_error(tmp_path: Path):
    (tmp_path / "f.txt").write_text("")
    with pytest.raises(ToolError, match="路径不存在"):
        await ls(tmp_path, path="nope")
    with pytest.raises(ToolError, match="不是目录"):
        await ls(tmp_path, path="f.txt")


async def test_symlinks_are_shown_not_followed(tmp_path: Path):
    make_tree(tmp_path, {"real": {"f.txt": ""}})
    (tmp_path / "loop").symlink_to(tmp_path)  # 指向自己：跟进去就会无限递归
    (tmp_path / "real" / "back").symlink_to("..")
    assert (await ls(tmp_path, depth=5))[1:] == [
        "real/",
        "  back -> ..",
        "  f.txt 0B",
        f"loop -> {tmp_path}",
    ]


@pytest.mark.skipif(
    not hasattr(os, "geteuid") or os.geteuid() == 0, reason="需要 POSIX 权限且不能是 root"
)
async def test_unreadable_subdir_is_marked(tmp_path: Path):
    make_tree(tmp_path, {"locked": {"secret.txt": ""}, "open": {"f.txt": ""}})
    locked = tmp_path / "locked"
    locked.chmod(0)
    try:
        lines = await ls(tmp_path)
    finally:
        locked.chmod(0o755)
    assert lines[1:] == ["locked/ [无权限]", "open/", "  f.txt 0B"]


@pytest.mark.parametrize(
    ("size", "expected"),
    [(0, "0B"), (1023, "1023B"), (1024, "1.0K"), (1536, "1.5K"), (5 * 1024**2, "5.0M")],
)
def test_format_size(size: int, expected: str):
    assert format_size(size) == expected


def test_args_bounds_in_schema():
    properties = list_dir.schema()["function"]["parameters"]["properties"]
    assert properties["depth"] == {
        "type": "integer",
        "default": 2,
        "minimum": 1,
        "maximum": 5,
        "description": "展开层数，1 表示只列直接子项",
    }
