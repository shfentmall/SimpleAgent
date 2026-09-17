# M3：权限、会话持久化与无人值守执行

日期：2026-09-17 · 对应里程碑 M3（权限 + 会话 + Headless）

M3 之前的状态是：**除了客户端模式会问一句，终端里的 `bash` / `write_file` / `edit_file`
完全裸奔**。这一篇记的是补这条线时想明白的三件事：判定和询问要分开、历史不能一行一条地存、
以及"没人在线"本身就是一种必须显式处理的运行状态。

## 1. 判定（Policy）和询问（Approver）必须拆成两层

一开始的想法是给 `Tool` 加一个 `permission` 字段，每个前端自己决定怎么问。走两步就发现不对：
REPL 记得拦 `rm -rf /`，定时任务忘了拦，就会出现"凌晨三点 agent 自己格式化磁盘"。
**危险判定必须放在所有前端共享的地方**，而不是靠每个前端各自的自觉。

于是拆成两层：

| | 职责 | 有没有 IO | 谁实现 |
|---|---|---|---|
| `Policy.decide()` | 这次该 allow / ask / deny | 无 IO，纯函数 | 一份，所有人共用 |
| `Approver.request()` | 真的去问人，返回允许/拒绝 | 有（读终端、推 SSE） | 每个前端各写一份 |

调用顺序在 `ToolRegistry.execute` 里，只有一句话：**先问 Policy，只有结果是 ask 才轮到 Approver**。
所以 deny 的路径根本不经过审批器——测试用例 `test_deny_never_reaches_the_approver`
断言的就是这点（越界的写操作不该跑到用户面前打扰他）。

`decide()` 内部的顺序是有讲究的，越界检查和危险命令必须排在工具默认等级**之前**：

```python
if permission == "deny":  # 1. 工具被整体禁用
    return DENY
for path in scope.paths:
    if not self._inside(path):  # 2. 目标在工作目录之外 → 拒绝，不可协商
        return DENY
if scope.command and (r := inspect_command(...)):  # 3. bash 的必然危险
    return DENY
if permission == "allow":  # 4. 默认是只读工具
    return ALLOW
return ASK  # 5. 其余都得问
```

反过来写（先放行 allow 再查边界）的话，任何工具只要标 `permission="allow"` 就能穿透边界。

## 2. Scope：让工具自己声明碰了什么，别在 Policy 里反射

Policy 需要知道"这次调用要改哪个文件"。两种做法：

- **集中分派**：在 `permissions.py` 里给每个工具写一段提取逻辑。问题是加一个工具要回来改 Policy，
  MCP / Skills 注册进来的工具更是无从下手。
- **工具自带 `scope` 钩子**（选了这个）：`@tool(scope=lambda args, ctx: Scope(paths=(...)))`，
  扩展点跟着 Tool 走，符合"注册式扩展"那条设计原则。

```python
@tool(name="write_file", ..., readonly=False, permission="ask",
      scope=lambda args, ctx: Scope(paths=(ctx.resolve(args.path),)))
```

两个约定值得记住：

- **`Scope.paths` 只放"会被改动"的路径**。只读工具不报 paths，因此天然不受目录边界限制。
  读 `~/.zshrc` 是日常需求，为它弹一次确认很不划算；真正的风险在写。
- **`resolve()` 在 scope 里就做完**。`../..` 必须在交给 Policy 之前展开，
  否则 `../secret` 能绕过检查。
- **scope 提取器抛异常时退化成 ask**，不是 deny。判断不了的时候，交给能决定的人，不替用户做主。

## 3. 危险命令的尺子要窄

`inspect_command` 只回答一个问题：**有没有"问了也不该答应"的命令**。尺子定得太宽会有反效果——
模型会学会绕开审批（改用别的命令达到同样目的），那比不拦更危险。所以只有这三类进 deny：

1. **破坏无法撤销**：`rm -rf` 指向家目录、系统根目录或整个工作目录；`mkfs` / `dd` / `fdisk` / `diskutil`。
2. **整机失去响应**：`shutdown` / `reboot` / fork bomb / 管道直接喂 `sh` `python`（远程脚本执行）。
3. **写裸块设备**：`> /dev/sda`。

删 `node_modules`、装错包、`npm install`、`git status` 这些**可弥补**的都不拦，照常走 ask。

实现上有两个细节：

- **按 shell 分隔符切段再看**：`echo hi; rm -rf /` 的第一个词是 `echo`，不切分就会漏掉；
  所以 `; && || |` 换行都是切点，每段单独取命令名。
- **管道喂解释器必须在原文上匹配**：`curl x | sh` 一旦被 `|` 切开就认不出来了，
  所以 `_RAW_PATTERNS` 是在整条命令上跑的，排在最前面。
- **通配符按所在目录算**：`rm -rf *` 等价于删空当前目录，`rm -rf ~/*` 等价于删空家目录。

边界的判定规则最后是「看目标本身，不看它的祖先」这一版：

```
rm -rf /            → target == /          → deny
rm -rf ~            → target == home       → deny
rm -rf /usr         → target.parent == /   → deny
rm -rf ~/Downloads  → target.parent == home→ deny
rm -rf node_modules → parent 是项目目录     → ask（项目里删依赖是常规操作）
```

第一版写成了"祖先里出现关键目录就拒绝"，结果 `rm -rf node_modules` 也被拦了——
这是真实使用中很常见的操作，把这种操作推给"请不要执行"会让人很快想关掉整个权限系统。

## 4. 三个审批器，同一个接口

```python
class Approver(Protocol):
    async def request(self, req: ApprovalRequest) -> ApprovalDecision: ...
```

| 实现 | 场景 | 行为 |
|---|---|---|
| `ConsoleApprover`（`ui/approve.py`） | REPL | y / a（本次会话都允许）/ 其他键拒绝 |
| `WhitelistApprover`（`permissions.py`） | `sa run`、以后的 `sa daemon` | 名单外一律拒绝，并把原因回给模型 |
| `APIApprover`（`serve/approval.py`） | 桌面客户端 | 推一帧 SSE 给客户端，`await Future` 挂起等回调 |

坑：**终端里不能直接 `input()`**。approver 是在协程里被 await 的，同步阻塞会把整个事件循环卡住，
已经连着的 SSE 客户端和并行跑到一半的工具任务全都停摆。所以读输入走 `asyncio.to_thread`。

`approval.py` 原来自己定义了 `Approver` 和 `ApprovalDecision`。M3 把它们移到了 `permissions.py`
（协议属于核心，不属于某一端），serve 那边改成导入并重新导出，客户端代码不用改。
顺带把 `request` 的签名从三个关键字参数改成单个 `ApprovalRequest` 对象——
参数以后还会加（这次就加了 `reason`），传对象比传一串散参数好改。

## 5. 会话 JSONL：为什么一行一条消息不够

`~/.simpleagent/sessions/<id>.jsonl`，每行一条**操作记录**：

```json
{"op": "meta", "id": "...", "profile": "deepseek", "cwd": "...", "created_at": "..."}
{"op": "append", "message": {...}}
{"op": "truncate", "n": 5}
{"op": "stats", "requests": 3, "usage": {...}}
```

关键是 `truncate` 这一条。Ctrl+C 中断时 `Agent._repair` 要**撤回**已经写进历史的半条对话
（只留没问题的部分，或者整轮撤掉）。如果 JSONL 是一行一条消息，撤回就意味着全量重写文件——
进程崩在写一半的时候会丢掉整份历史。改成追加一条 `truncate` 记录后，任何时候重放出来的
都是最后一次的一致状态（和 `spaces/` 那边 jsonl 的取舍一样）。

配套的两个小保障：

- **追加前先看文件是不是以换行结尾**。不是就先补一个换行，把被 kill 时写坏的那半行隔离掉，
  否则新记录会接在它后面，两条一起报废。
- **写盘失败静默**。持久化是加分项，磁盘满了也不该让对话中断。

为了不漏持久化，`Session.messages` 只能通过 `add` / `add_many` / `truncate` 改：
直接 `session.messages.append(...)` 会让恢复出来的历史少内容，而且不报错，很难查。

## 6. headless：没人在线是一种要显式处理的状态

`sa run "..."` 跑一次就退出。它的审批器是 `WhitelistApprover`——
**需要问但没有可以问的人时，一律按拒绝处理，并且把原因回给模型**。这一条很重要：
如果这里不是"拒绝"而是"等待"，无人值守任务就会永远挂在那里。

实测（deepseek）的效果比预期好：

```
→ write_file {"path": "tmp.txt", "content": "hello\n"}
  错误：工具 write_file 会改动文件或执行命令；已被拒绝
创建失败 —— `write_file` 被权限策略拒绝了，文件没有生成。
我没有改用其他方式（比如 `bash` 里的 `echo`）绕过这个限制，因为那等于规避同一条策略。
```

模型读懂了拒绝原因，并且**主动说明自己不去绕开**——这正是把原因写清楚的价值：
拒绝消息是给模型看的第二种输入，写得含糊它就会去找 workaround。

另一个观察：白名单管不到 `Policy`。`--allow bash` 也不代表 `rm -rf ~` 能过，
危险命令那道关卡在更前面。

## 7. 命令行

```bash
uv run sa sessions              # 列出已保存的会话（按最近修改倒序）
uv run sa --resume              # 接着最近那次继续聊
uv run sa --resume 20260917-195109-ab47
uv run sa run "整理一下 downloads"            # headless，写操作一律拒绝
uv run sa run "..." --allow write_file,edit_file
uv run sa run "..." --cwd ~/Downloads --allow write_file
```

`run` 子命令里加了 `-m/--profile`，它的 default 是 `argparse.SUPPRESS` 而不是 `None`——
子解析器用 `None` 会把父级的 `-m` 值覆盖掉，`SUPPRESS` 则在不传时不写这个属性，父级的值得以保留。

## 8. 踩到的坑

- **`ToolContext.approver` 被删掉了**。W2 为了给客户端审批先用了一个"工具自己去问"的路径，
  引入 Policy 之后它成了第二条审批通道，谁也说不清该走哪条。审批统一收回到注册表。
- **一处循环导入**（顺带修的）：`tools/registry.py` 在运行时 `from simpleagent.serve.approval import`。
  跨层类型依赖一律用 `if TYPE_CHECKING:`，包的 `__init__` 里别做重导入。
- **会话落盘位置**：`SessionStore` 挂在 `~/.simpleagent/sessions/`，和 `spaces/`（工作台的）
  是两套东西。前者是命令行会话，后者是空间维度的 multi-session，别合并。

## 9. 下一步

M4（`sa daemon`）会直接复用这里的三样：白名单审批器、Policy、以及会话落盘。
还没做的两件：

- **权限规则还不进配置文件**。现在是工具自带默认等级，想要"这个项目里 bash 一律放行"
  还没有地方写。M4 做定时任务时必须解决（`allowed_tools` 得能配）。
- **`--allow` 不支持模式匹配**，只能列工具名。`bash:*` 这类粒度等真实需求出现再加。
