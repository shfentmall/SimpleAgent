# 客户端 UI 静态原型去除本机用户名

- 日期：2026-09-17
- 对比基线：`757e208`（Add changelog for client UI doc path fix）
- 对应里程碑：无（文档维护）

## 功能变化

- 修复：`docs/design/client-ui-mockup.html` 中残留的本机用户名和绝对路径示例，改成不暴露本机身份的写法，避免公开文档泄露本机用户名。
  - 5 处示例路径（space-path、cwd 展示、`DATA` 会话数据里的 `cwd` 字段）从家目录下的绝对路径改成 `~/dev_code/SimpleAgent`。
  - 1 处 `ls -ld` 命令输出示例里的文件属主从本机用户名改成占位的 `user`。
  - 纯静态原型页面，仅示意用文本改动，不涉及任何逻辑或交互行为。
  - 此前 `2026-09-17-client-ui-doc-redact-username.md` 已处理了 `docs/design/client-ui.md`，这次补上配套的 HTML 原型，至此 `docs/design/` 下不再含本机用户名。

## 函数级改动

无（本次改动仅涉及 `docs/design/client-ui-mockup.html` 静态文本，`src/` 下无任何改动）。

## 配置与依赖

- 无变化，用户无需手动处理。

## 测试

- 本次只改了文档中的静态展示文本，没有代码改动，未跑测试（工作区中另有会话正在进行的未提交代码改动会影响测试结果，故本次不跑）。
- 上一次包含代码改动的推送（`65d3068`）时：`uv run pytest -q` 177 passed，`uv run ruff check` 通过。

## 相关笔记

无
