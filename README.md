# SimpleAgent


目标：设计一个自用的 agent 。
1、学习的功能，在这个过程中能够熟悉了解当前 agent 的核心功能，可以方便测试当前业界新发的 feature 
	在实现的过程中，希望是能够一步步的完善
2、他能够在本地启动解决真实的问题。

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
```

REPL 命令：`/model [name]` 切换模型、`/clear` 清空历史、`/usage` 查看用量、`/help`、`/exit`。
每次请求的完整请求体和响应都记录在 `~/.simpleagent/traces/` 下。

## 开发

```bash
uv run pytest        # 测试（不联网）
uv run ruff check    # lint
uv run ruff format   # 格式化
```

