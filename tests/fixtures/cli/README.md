# CLI 事件流样本

外部 agent 的无头模式输出是 NDJSON（一行一个 JSON）。项目测试**不联网**，所以解析器只能
对着录下来的样本测——这里的文件就是「录音」。

## 怎么来的

| 文件 | 来源 | 说明 |
|---|---|---|
| `opencode-bash-pwd.jsonl` | 真实录制，2026-09-17 | `opencode run "用 bash 跑一次 pwd…" --format json --model deepseek/deepseek-v4-flash`，一次完整成功运行：工具调用 → 工具结果 → 文本 → 结束 |
| `claude-not-logged-in.jsonl` | 真实录制，2026-09-17 | `claude -p "…" --output-format stream-json --verbose --include-partial-messages`，本机 claude 未登录，录到的是**失败路径**（`init` → `status` → `assistant` → `result`），信封字段是真的，工具事件没录到 |

录完做了脱敏：临时目录、session id、uuid、本机用户名与 home 路径都替换成了固定值，
其余结构、字段名、数值一字未改。

## 重录方法

```bash
cd "$(mktemp -d)"   # 别在仓库里跑，免得吃到本仓库的 AGENTS.md
claude -p "用 bash 跑一次 pwd，然后用一句话总结当前目录" \
  --output-format stream-json --verbose --include-partial-messages \
  --allowedTools "Bash(pwd)" > claude.jsonl
OPENCODE_QUIET=1 opencode run "用 bash 跑一次 pwd，然后用一句话总结当前目录" \
  --format json --model deepseek/deepseek-v4-flash > opencode.jsonl
```

录完记得走一遍上面的脱敏（`/Users/<你>/` → `/Users/you/redacted`、session id 固定值）。

## 两个已经踩到的坑

1. **claude 的 `result.subtype` 是 `success`，`is_error` 却是 `true`**——成败只能看 `is_error`，
   看 `subtype` 会把「没登录」当成成功。
2. **opencode 的 token 和 cost 都是「本步增量」**（每个 `step_finish` 只报这一步用了多少），
   要自己累加；`tokens.cache.read` 要算进 prompt tokens。
