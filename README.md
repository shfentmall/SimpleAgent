# SimpleAgent


目标：设计一个自用的 agent 。
1、学习的功能，在这个过程中能够熟悉了解当前 agent 的核心功能，可以方便测试当前业界新发的 feature 
	在实现的过程中，希望是能够一步步的完善
2、他能够在本地启动解决真实的问题。

## 文档

- [架构设计](docs/ARCHITECTURE.md)：选型、设计原则、模块划分
- [路线图](docs/ROADMAP.md)：M0–M9 里程碑与当前进度
- [学习笔记](docs/notes/)：每个里程碑学到的东西和踩过的坑

## 安装

用 [uv](https://docs.astral.sh/uv/) 装成全局命令，之后在任意目录都能直接敲 `sa`（本机没有 Python 3.12 时 uv 会自动下载）：

```bash
uv tool install --editable .     # 在仓库根目录执行；editable：改完代码立刻生效，不用重装
sa --version                     # 确认装好了
```

- 不加 `--editable` 是装一份当前代码的快照，改代码后要 `uv tool install --reinstall .` 才会更新
- 在别的电脑上直接从 GitHub 装：`uv tool install git+ssh://git@github.com/shfentmall/SimpleAgent.git`
- 卸载：`uv tool uninstall simpleagent`（`~/.simpleagent/` 里的配置和会话不会删）
- 命令装在 `~/.local/bin/`；不在 PATH 里的话执行一次 `uv tool update-shell`

同时会装一个长名字 `simple_agent`，和 `sa` 完全等价。macOS 自带一个 `/usr/sbin/sa`（系统记账统计），
平时 `~/.local/bin` 排在 PATH 前面没问题；但 launchd、cron 这类 PATH 很短的环境会找到系统那个，
这时写绝对路径 `~/.local/bin/sa`，或者用 `simple_agent`。

不想装也可以：在仓库目录下 `uv sync` 后用 `uv run sa` 代替下面的 `sa`。

## 快速开始

```bash
sa init                          # 生成 ~/.simpleagent/config.toml
echo 'DEEPSEEK_API_KEY=sk-...' >> ~/.simpleagent/.env && chmod 600 ~/.simpleagent/.env
                                 # 默认 profile 是 deepseek；也可以直接 export 环境变量
sa                               # 进入对话
sa -m local                      # 指定 profile，比如本地 Ollama
sa sessions                      # 列出已保存的会话
sa --resume                      # 接着最近一次继续聊
sa run "整理 downloads"           # headless：跑一个任务就退出，不交互
sa run "..." --allow write_file,edit_file   # 放行指定的写工具
sa serve                         # 启动本地 API（HTTP + SSE），供桌面客户端连接
uv run pytest 2>&1 | sa inbox push -t "夜间测试" --level warn
                                 # 往控制面板投一条消息（正文可走管道），不需要 serve 在跑
```

REPL 命令：`/model [name]` 切换模型、`/tools` 列出可用工具、`/clear` 清空历史、`/usage` 查看用量、`/help`、`/exit`。
模型可以自己调用工具（`list_dir`、`read_file`、`write_file`、`edit_file`、`glob`、`grep`、`bash`），终端里灰色显示调用和结果预览；
一轮对话最多请求模型 `max_steps` 次（默认 20）；过长的工具输出只把开头回给模型，完整内容存在 `~/.simpleagent/tool_outputs/`。
每次请求的完整请求体和响应都记录在 `~/.simpleagent/traces/` 下，会话历史写在 `~/.simpleagent/sessions/`（退出 REPL 不清空）。

## 权限

读操作（查看目录、读文件、搜索）直接执行；写文件、改文件、跑命令会在终端问一句：
`y` 允许、`a` 本次会话都允许、其他键拒绝。以下情况连问都不问，直接拒绝并把原因告诉模型：

- 目标路径在工作目录之外（`../secret.txt`、`/etc/hosts`）
- 必然造成不可恢复损失的命令（`rm -rf ~`、`rm -rf /usr`、`mkfs`、`curl … | sh`、fork bomb 等）

理由会回给模型，它通常能自己换成别的办法。`sa run` 是无人值守模式，没人可以按 y——
写操作一律按拒绝处理，也可以用 `--allow bash,write_file` 显式放行。

## 开发

```bash
uv run pytest        # 测试（不联网）
uv run ruff check    # lint
uv run ruff format   # 格式化
```

