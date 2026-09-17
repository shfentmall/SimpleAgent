# M0 + M1 学习笔记：流式对话

## 1. Chat Completions 流式协议长什么样

`stream: true` 时，服务端返回 SSE，每个事件是一行 `data: {chunk JSON}`，最后以 `data: [DONE]` 结束。一次回复的 chunk 序列通常是：

```
{"choices":[{"index":0,"delta":{"role":"assistant","reasoning_content":"想"}}]}   ← 第一个 chunk 带 role
{"choices":[{"index":0,"delta":{"content":"你好"}}]}                              ← 正文增量
{"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}                       ← 结束原因
{"choices":[],"usage":{"prompt_tokens":12,"completion_tokens":3}}                 ← include_usage 时多出的最后一个 chunk
```

- **usage 默认不返回**：要发 `stream_options: {"include_usage": true}`，而且 usage 在一个 `choices` 为空的独立 chunk 里。不支持这个参数的服务，可以用 `quirks.stream_usage = false` 关掉。
- **finish_reason**：`stop` 表示正常结束，`length` 表示达到 max_tokens 被截断（REPL 会提示），`tool_calls` 表示模型要调用工具（M2 用到）。
- **想看原始 chunk**：把配置里的 `trace.raw_chunks` 设为 `true`，每个 chunk 都会写进 trace。

## 2. tool_calls 是分片到达的

```
{"tool_calls":[{"index":0,"id":"c0","function":{"name":"read_file","arguments":""}}]}
{"tool_calls":[{"index":0,"function":{"arguments":"{\"pa"}}]}
{"tool_calls":[{"index":0,"function":{"arguments":"th\": \"/tmp\"}"}}]}
```

- 同一个调用的多个分片靠 `index` 关联；并行调用时，不同 index 的分片可能交错到达。
- `arguments` 要逐片拼接；`name` 只取第一次出现的值，因为有的实现会在每个分片里重复带 name，直接拼接会变成 `read_fileread_file`。
- 拼好之后的 arguments 不保证是合法 JSON，M2 要处理这种情况。

实现见 `StreamAccumulator`（`src/simpleagent/llm/client.py`）。

## 3. 思考内容不是标准字段

- 思考内容不在 OpenAI 标准协议里，各家字段名不同：DeepSeek / GLM / Qwen 常用 `reasoning_content`，新版 Ollama 用 `reasoning`。所以通过 `quirks.reasoning_field` 配置。
- openai SDK 的响应模型是 pydantic `extra="allow"`，非标准字段会保留下来，`chunk.model_dump()` 里能拿到（`test_stream_end_to_end` 验证过）。
- 会话历史里统一存到 `reasoning_content`，发请求前再按 `reasoning_echo` 处理：
  - `none`：全部去掉。普通对话一般不需要回传，还能省 token。
  - `current_turn`：只回传最后一条 user 消息之后的。思考模式下的工具调用循环可能需要这一项。
  - `all`：全部回传。

## 4. 前缀缓存

- 命中缓存的 token 数各家字段不同：OpenAI 标准是 `usage.prompt_tokens_details.cached_tokens`，DeepSeek 是 `usage.prompt_cache_hit_tokens`。`Usage.from_dict` 两种都兼容。
- 缓存按请求前缀匹配，所以 **system prompt 在会话内必须保持不变**。环境信息里只写日期、不写时分秒，就是出于这个考虑。
- M6 做上下文压缩时也要记住：改动越靠前，缓存失效的部分越多。

## 5. extra_body：试验厂商私有参数

SDK 的 `create()` 不接受未知参数，私有参数要放进 `extra_body`，由 SDK 合并到请求体顶层。profile 里的 `extra_body` 原样透传，trace 记录的是合并之后的请求体，和实际发出去的一致。比如 GLM 的 `thinking = { type = "enabled" }`、Qwen 的 `enable_thinking = true`。

## 6. Ctrl+C 与 asyncio

- REPL 用 `asyncio.Runner` 在多轮之间复用同一个事件循环。不能每轮都调用 `asyncio.run()`，因为 `AsyncOpenAI` 的连接池绑定在事件循环上。实测第二轮的首字延迟从 0.27s 降到 0.01s，就是连接被复用的效果。
- `Runner.run()` 会接管 SIGINT：第一次 Ctrl+C 会取消当前任务，任务内部收到 `CancelledError`，于是可以在 `chat()` 里收尾：
  - 已经有输出：把部分回复保存进历史，下一轮能接上
  - 还没有输出：撤回这条 user 消息
- `LLMClient.stream()` 是 async generator，中断有两种形式：消费方被取消（`CancelledError`）和消费方提前退出（`GeneratorExit`）。两种情况都会写一条 `status = "cancelled"` 的 trace。
- 小坑：进程启动时导入 openai 需要几百毫秒，这段时间按 Ctrl+C 会直接退出，因为还没进入 Runner。

## 7. 踩坑：本机代理拦截 localhost

本机设置了 `http_proxy=http://127.0.0.1:7897`（Clash 之类的代理）时，SDK 底层的 httpx2 默认会读取代理环境变量，**连发往 `127.0.0.1` 的请求也交给代理**，结果返回 `HTTP 502`。本地 Ollama 同样会中招。

处理方式：`base_url` 是回环地址（`localhost` / `127.0.0.1` / `::1`）时，创建 `DefaultAsyncHttpxClient(trust_env=False)`，不读代理环境变量。局域网地址（比如另一台机器上的 Ollama）仍然走环境变量，需要的话自己设置 `NO_PROXY`。

## 8. 测试手段

- **openai SDK 3.x 底层 HTTP 库是 `httpx2`**，不是 `httpx`。测试时通过 `http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler))` 注入模拟的 SSE 响应，走的是真实 SDK 的解析路径。
- **FakeLLM** 把脚本化的回复切成 chunk，再交给同一个 `StreamAccumulator` 拼接，用来测 REPL 和后续的 agent loop。
- **端到端冒烟测试**：起一个本地的模拟 OpenAI 兼容 SSE 服务，配一个指向它的 profile，然后用管道给 `sa` 输入对话，并给真实进程发 SIGINT 验证中断。

## 待实测记录（有 key 之后补充）

| profile | 思考字段 | 流式 usage | 缓存字段 | extra_body 思考开关 | 备注 |
|---|---|---|---|---|---|
| deepseek | | | | | |
| glm | | | | | |
| qwen | | | | | |
| local (Ollama) | | | | | |
