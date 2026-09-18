# SimpleAgent

最开始的想法是做一个自用的 agent，方便了解一些 agent 的功能；慢慢的到后面就像做一个个人工作台。

我目前使用 AI 主要的方式 1、基于目录，在目录下启动某个对应的客户端 如 claude 或 opencode，我尽量让自己的项目不依赖任何一个 agent；我会有投资，，健康管理，旅游等一些目录 ；2、随时进行 chat 这个不限于哪个 AI 以网页的形式。

所以目前这个项目的目标：
1、个人的 AI工作台，能够管理那些目录下运行的 AI 任务，可以分配任务，了解任务状态，可以获取例行任务的结果。
2、学习各种 agent 的技术实现。


## 文档

- [架构设计](docs/ARCHITECTURE.md)：选型、设计原则、模块划分
- [路线图](docs/ROADMAP.md)：M0–M9 里程碑与当前进度
- [学习笔记](docs/notes/)：每个里程碑学到的东西和踩过的坑

## 快速开始

```bash
uv sync                          # 安装依赖（Python 3.12）
uv run sa init                   # 生成 ~/.simpleagent/config.toml
echo 'DEEPSEEK_API_KEY=sk-...' >> ~/.simpleagent/.env && chmod 600 ~/.simpleagent/.env
                                 # 默认 profile 是 deepseek；也可以直接 export 环境变量
uv run sa                        # 进入对话
uv run sa -m local               # 指定 profile，比如本地 Ollama
uv run sa sessions               # 列出已保存的会话
uv run sa --resume               # 接着最近一次继续聊
uv run sa run "整理 downloads"    # headless：跑一个任务就退出，不交互
uv run sa run "..." --allow write_file,edit_file   # 放行指定的写工具
uv run sa serve                  # 启动本地 API（HTTP + SSE），供桌面客户端连接
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

