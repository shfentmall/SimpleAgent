# sa 支持装成全局命令（uv tool install），补 --version 和撞名兜底

- 日期：2026-09-18
- 对比基线：`85dd131`（Merge pull request #4 from shfentmall/claude/dashboard-message-design-3f9bd6）
- 对应里程碑：无（独立的安装改进；`simple_agent` 长名字为 M4 的 launchd 常驻做铺垫）

## 功能变化

- 新增：`sa --version` / `sa -V`，输出 `simpleagent <版本号>`；读不到已安装包的版本号时输出 `unknown`。
- 新增：`pyproject.toml` 里追加脚本入口 `simple_agent`，和 `sa` 指向同一个 `simpleagent.cli:main`。原因：macOS 自带 `/usr/sbin/sa`（系统记账统计），在 launchd、cron 等 PATH 很短的环境里可能撞到系统的 `sa` 而不是本项目装的命令，这时可以改用 `simple_agent` 或写绝对路径。
- 升级：README 新增「安装」一节，说明 `uv tool install --editable .` 装成全局命令、如何更新/卸载、从 GitHub 直接安装、以及和系统 `sa` 撞名时的处理办法；「快速开始」里原来的 `uv run sa ...` 全部改写为直接执行 `sa ...`（含 rebase 时一并合入的 `sa inbox push` 那行）。
- 修复：REPL 启动时如果创建模型客户端失败（比如缺 API key），不再先写下一条 0 条消息的空会话——`SessionStore.start()` 挪到了 `_make_llm()` 成功之后再调用，避免这类失败留下的空会话出现在 `sa sessions` 列表里、也可能被 `sa --resume` 接上。`sa run`（Headless 路径）之前顺序就是对的，没有改动。

本次不涉及打包本身的改动：项目原来就能构建出正常工作的 wheel（`config.example.toml`、`web/` 静态文件都已经打进包里），这次补的是围绕全局安装的周边（版本号、命令名冲突、文档、一个启动时序 bug）。

## 函数级改动

### `src/simpleagent/cli.py`

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `package_version()` | 新增 | 用 `importlib.metadata.version("simpleagent")` 读已安装包的版本号；抛 `PackageNotFoundError`（比如直接跑源码、没 `pip install`/`uv tool install` 过）时返回 `"unknown"`。 |
| `main()` | 修改 | `argparse.ArgumentParser` 新增 `-V`/`--version` 参数（`action="version"`），版本字符串为 `f"simpleagent {package_version()}"`。 |

### `src/simpleagent/ui/repl.py`

| 函数 / 类 | 变化 | 说明 |
|---|---|---|
| `Repl.__init__()` | 修改 | 把 `store.start(self.session, ...)` 的调用从「创建 `Agent`/`_make_llm` 之前」挪到「之后」。原来的顺序会导致缺 API key 等启动期错误抛出前，会话已经被写进 `SessionStore`，留下一条空会话。 |

## 配置与依赖

- `pyproject.toml` 的 `[project.scripts]` 新增 `simple_agent = "simpleagent.cli:main"`（`sa` 保留，两者等价）。
- 需要手动处理：这次改动只在当前分支/worktree 里生效。**合并到 `main` 后，需要在主仓库目录 `~/dev_code/SimpleAgent`（不是这个 worktree）执行 `uv tool install --editable .`**，才会在 PATH 里装出全局的 `sa` / `simple_agent` 命令；不要在 worktree 里装，因为 worktree 清理之后 editable 安装引用的路径会失效，命令会跑不起来。命令安装到 `~/.local/bin/`，如果不在 PATH 里，需要执行一次 `uv tool update-shell`。
- 如果本机 PATH 里 `~/.local/bin` 排在 `/usr/sbin` 之后，`sa` 可能会解析到 macOS 自带的 `/usr/sbin/sa`；这种情况用 `simple_agent` 或者写绝对路径 `~/.local/bin/sa`。

## 测试

- `tests/test_config.py`：新增 `test_cli_version`，验证 `main(["--version"])` 以退出码 0 结束，且输出格式是 `simpleagent <数字开头的版本号>`。
- `tests/test_repl.py`：新增 `test_startup_failure_leaves_no_empty_session`，验证模型客户端创建失败（`ConfigError`）时 `SessionStore` 里不会留下空会话；同一个 store 后续正常启动一次，确认会话数变成 1（排除是 store 本身坏了）。撤掉 `repl.py` 的顺序修复会让这个测试失败。
- 测试结果：`uv run pytest -q` 313 passed。
- `uv run ruff check`：All checks passed。
- `uv run ruff format --check`：100 files already formatted。
- 手动验证（未写自动化测试）：`uv build` 出 wheel 后用 `uv tool install` 装到临时目录，`uv tool install` 报告 "Installed 2 executables: sa, simple_agent"；两个命令的 `--version` 都输出 `simpleagent 0.1.0`；缺 API key 的情况下启动一次，`sa sessions` 显示没有会话（对应上面那条 bug 修复）。

## 相关笔记

无
