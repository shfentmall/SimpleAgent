#!/bin/sh
# 假的 opencode / claude：把收到的参数存成一行一个，然后把事件流吐出来。
# 用它测 Runner 的 CLI 路径，这样测试既不联网也不依赖本机装没装那些 CLI。
#
# FAKE_CLI_ARGV   —— 把参数写到这个文件（一行一个，方便断言）
# FAKE_CLI_EVENTS —— 要吐的 NDJSON 样本文件
# FAKE_CLI_SLEEP  —— 吐事件之前先睡几秒（用来测取消）
# FAKE_CLI_EXIT   —— 退出码，默认 0
printf '%s\n' "$@" > "$FAKE_CLI_ARGV"
if [ -n "$FAKE_CLI_SLEEP" ]; then
  sleep "$FAKE_CLI_SLEEP"
fi
cat "$FAKE_CLI_EVENTS"
exit "${FAKE_CLI_EXIT:-0}"
