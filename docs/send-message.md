# 给 sa 发消息

写给「sa 之外」的一方：脚本、cron / launchd 定时任务、别的目录里跑的 Claude Code / OpenCode，
任何想把一条结论告诉 sa 的程序。

消息会进工作台控制面板的「消息」栏（inbox）：左栏「控制面板」旁出现未读角标，点开看全文，
点开 30 分钟后自动归档。这里只管**通知**；想让 sa 去干活，见文末 [派任务](#不是通知是派任务)。

## 选哪种方式

| 方式 | 需要 `sa serve` 在跑 | 能带链接（`ref`） | 适合 |
|---|---|---|---|
| **`sa inbox push`**（推荐） | 不需要 | 不能 | shell 脚本、cron、任何能起子进程的程序 |
| **`POST /api/inbox`** | 需要 | 能 | 起不了本地命令、或要附链接的调用方 |

两条路写的是同一个文件 `~/.simpleagent/panel/inbox.jsonl`，截断、级别校验口径一致（都在
`src/simpleagent/panel/store.py`）。**不要自己往这个文件里追加**：并发写入的保证在 store 里，
格式以后也可能变。

## 方式一：`sa inbox push`

```bash
sa inbox push -t "备份完成" -b "NAS 增量备份 12.3 GB"
./nightly.sh 2>&1 | sa inbox push -t "夜间任务" --level warn --source schedule
```

| 参数 | 必填 | 说明 |
|---|---|---|
| `-t` / `--title` | 是 | 标题，面板列表里显示的那一行 |
| `-b` / `--body` | 否 | 正文，见下面三种给法 |
| `--level` | 否 | `info`（默认）/ `success` / `warn` / `error`，给别的值直接报错 |
| `--source` | 否 | 来源标签，默认 `cli`（面板上显示「脚本」），约定见 [字段说明](#字段说明) |

正文三种给法：

- `-b "文本"`：直接给；
- 不给 `-b`，且 stdin 是管道：自动读 stdin（上面 `nightly.sh` 那个例子）；终端里直接敲不会卡住等输入；
- `-b -`：强制读 stdin。

结果：

- 成功：stdout 打印新消息的 id（如 `ms_1789694475105_8f56d3`），退出码 0；
- 标题为空：stderr 打印原因，退出码 1；
- 参数不对（比如 `--level fatal`）：argparse 报错，退出码 2。

**标题或正文以 `-` 开头时**，`-t "-x"` 会被当成选项报错。标题写成 `--title=-x`；
正文走 stdin：`printf '%s' "$body" | sa inbox push --title="$title" -b -`。

**cron / launchd 里写绝对路径**：macOS 自带一个 `/usr/sbin/sa`（系统记账），PATH 很短的环境里
`sa` 会找到它。用 `~/.local/bin/simple_agent`（和 `sa` 完全等价的长名字）：

```cron
0 7 * * * cd ~/invest && ./daily.sh 2>&1 | $HOME/.local/bin/simple_agent inbox push -t "投资日报" --source schedule
```

## 方式二：`POST /api/inbox`

先有人跑着 `sa serve`（默认 `http://127.0.0.1:8384`）。

```bash
curl -s -X POST http://127.0.0.1:8384/api/inbox \
  -H 'Content-Type: application/json' \
  -d '{"title": "日报已生成", "body": "今天 3 只票触发提醒", "level": "success",
       "source": "invest", "ref": {"url": "https://example.com/report"}}'
```

请求体（JSON）：

| 字段 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `title` | 是 | — | 去掉首尾空白后不能为空 |
| `body` | 否 | `""` | 正文 |
| `level` | 否 | `info` | 不认识的值**不报错**，按 `info` 存 |
| `source` | 否 | `manual` | 来源标签 |
| `ref` | 否 | `{}` | 点开后指向什么，见 [字段说明](#字段说明) |

响应：

| 状态码 | 什么时候 | 响应体 |
|---|---|---|
| `201` | 投递成功 | 存下来的整条消息：`{"id", "source", "title", "body", "ts", "level", "ref"}` |
| `400` | 标题为空；**请求体不是合法 JSON 也是这个** | `{"error": "title 不能为空"}` |
| 连接被拒 | `sa serve` 没开 | — 退回用 `sa inbox push` |

## 字段说明

**level**：决定卡片颜色。

| 值 | 面板显示 | 什么时候用 |
|---|---|---|
| `info` | 信息 | 例行结论，看不看都行 |
| `success` | 成功 | 跑完了、结果正常 |
| `warn` | 注意 | 跑完了但有要看的东西（测试有失败、指标越线） |
| `error` | 错误 | 任务本身挂了 |

**source**：开放字符串，面板上显示成小标签。这几个有中文名，其它原样显示：

| 值 | 显示 | 约定 |
|---|---|---|
| `schedule` | 定时 | 定时任务投的 |
| `cli` | 脚本 | `sa inbox push` 的默认值 |
| `manual` | 手动 | `POST /api/inbox` 的默认值 |
| `mail` | 邮件 | 留给邮件适配器 |
| `system` | 系统 | **sa 自己用**（会话完成 / 失败、验证未通过），外部别用 |

想按项目区分的话直接用项目名（`invest`、`health`），面板上就显示这个词。

**ref**：点开这条消息后做什么。只有 HTTP 能设。

| `ref` | 点开后 |
|---|---|
| `{}`（默认） | 弹层看全文 |
| `{"url": "https://..."}` | 弹层看全文，底部多一个「打开链接」；只认 `http(s)://`，别的协议不给按钮 |
| `{"space_id": "...", "session_id": "..."}` | 跳到 sa 里的那个会话；会话已删就退回弹层看全文 |

**正文怎么显示**：纯文本，保留换行；支持 ```` ``` ```` 代码块和 `` `行内代码` ``，其它 Markdown
不渲染，HTML 会被转义。列表里只显示前 200 字的预览（换行拍平），全文在点开的弹层里。

## 常见写法

### 按脚本成败定级别

管道的退出码是 `sa` 的，不是脚本的。要按成败区分，先把输出收起来：

```bash
if out=$(./backup.sh 2>&1); then level=success; else level=error; fi
printf '%s\n' "$out" | ~/.local/bin/simple_agent inbox push -t "NAS 备份" --level "$level" --source schedule
```

### Python：能走 HTTP 就走，不行退回 CLI

只用标准库，可以直接抄进别的项目：

```python
import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request

SA_URL = "http://127.0.0.1:8384/api/inbox"


def notify_sa(
    title: str, body: str = "", level: str = "info", source: str = "script", url: str | None = None
) -> str:
    """往 sa 控制面板投一条消息，返回消息 id。"""
    payload = {"title": title, "body": body, "level": level, "source": source}
    if url:
        payload["ref"] = {"url": url}
    req = urllib.request.Request(
        SA_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            return json.load(resp)["id"]
    except urllib.error.HTTPError:
        raise  # 400：参数错了，换 CLI 也一样
    except OSError:
        pass  # 连不上：sa serve 没开，退回 CLI（CLI 带不了链接）
    exe = shutil.which("simple_agent") or os.path.expanduser("~/.local/bin/simple_agent")
    done = subprocess.run(
        [exe, "inbox", "push", f"--title={title}", "-b", "-", "--level", level, "--source", source],
        input=body,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()
```

`--title=` 和 `-b -` 是为了标题、正文以 `-` 开头时也不出错。

### 让别的目录里的 agent 通知你

在那个项目的 `AGENTS.md` / `CLAUDE.md` 里加一段，Claude Code / OpenCode 就知道干完活该怎么说：

```markdown
## 完成通知

任务做完（或做不下去）时，用一条命令把结论发到 SimpleAgent 控制面板：

    ~/.local/bin/simple_agent inbox push --title="<一句话结论，30 字内>" --level <success|warn|error> --source <本项目名> -b - <<'EOF'
    <正文：做了什么、结果、需要人看的地方>
    EOF

只在有结论时发一条，不要发进度。
```

## 投递之后

- **什么时候能看到**：浏览器里的工作台每 30 秒拉一次，最多等 30 秒出现在列表和角标里。
  还没有系统通知（macOS 通知中心），留给 M4 的 `notify`。
- **不去重**：同样的标题发两次就是两条。例行任务想少打扰，就只在有东西要看时发。
- **归档**：点开后 30 分钟自动进「归档」（`config.toml` 里 `[panel] archive_after_minutes` 可改）；
  没点开过的一直留在「当前」。
- **存在哪**：`~/.simpleagent/panel/inbox.jsonl`，只追加。设了 `SIMPLEAGENT_HOME` 的话在
  `$SIMPLEAGENT_HOME/panel/` 下——调用方和工作台的 `SIMPLEAGENT_HOME` 不一致，消息就投到别处去了。

## 限制与注意

- **正文上限 64 000 字**，超出截断并在末尾注明原长度，不会整条丢掉。标题不限长，但列表只有一行，30 字以内为好。
- **不要放密钥、密码**：消息明文落盘，工作台也会原样显示。
- **HTTP 接口没有鉴权**，靠只监听 `127.0.0.1` 保证只有本机能投。不要 `sa serve --host 0.0.0.0` 暴露到不可信网络，
  否则谁都能往你的面板里塞消息。
- 投出去的消息不能撤回、改不了，只能在面板上归档。

## 不是通知，是派任务？

想让 sa 替你做事而不只是看结论：

- **一次性任务**：`sa run "整理 downloads" --cwd ~/Downloads`，跑完就退出；写操作默认拒绝，
  要放行用 `--allow write_file,edit_file`。见 [README](../README.md#快速开始)。
- **投给工作台里某个空间**：`POST /api/spaces/{space_id}/sessions` 新建会话，再
  `POST /api/sessions/{session_id}/input`（`{"text": "..."}`，返回 `202`），跑完 sa 会自己往 inbox
  落一条 `system` 消息。接口全表见 [client-ui.md 6.2 路由](design/client-ui.md#62-路由)。
