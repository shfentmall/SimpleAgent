---
name: explainer
description: 给一个做完的新功能出一套给产品经理看的原理图：Artifact 网页 + 每张图一份 PNG，风格照 template.html（2026-09-24 指挥台调度那套）。只在用户敲 /explainer 时运行，在后台子 agent 里跑。
disable-model-invocation: true
context: fork
agent: general-purpose
argument-hint: "<变更记录路径或功能名> [补充说明]"
---

# /explainer：给新功能出原理图

任务：给「$ARGUMENTS」出一套介绍用的原理图，交付一个 Artifact 网页和一组 PNG。
用户要把它发给产品经理、发到群里。

你在后台子 agent 里跑，**看不到用户和主会话的对话**，需要的信息都从仓库里读。你也不能问用户：
信息不够时按下面的规则取舍，在汇报里说明。

本 skill 目录是 `.claude/skills/explainer/`，里面有：

- `template.html`：样板网页，用户点名喜欢的风格
- `shot.py`：把网页截成 PNG 的脚本

## 1. 弄清这个功能

- 参数是变更记录路径就先读它；是功能名就在 `docs/changelog/README.md` 的索引里找最接近的一份，
  找不到再用 `git log --oneline -30` 找对应的提交和 diff。
- 再读相关的 `docs/design/`、`docs/notes/`。
- 「什么时候会停下来问你」「写死在代码里的规矩」「现在还做不到的」这三类，要读代码确认，以代码为准。
- 读完要能用产品的话说清：一句话它是什么；以前和现在差在哪；一次完整流程怎么走；边界在哪。

## 2. 写网页

把 `template.html` 复制到 scratchpad（没有就用 `$TMPDIR`）下的 `explainer-<英文短名>/page.html` 再改，
不要放进仓库。

**样式照抄，只换内容。**整段 `<style>` 保留，图里的元素用现成的 class，不要重新设计版式和配色：

- 节点 `n-*`、连线 `e-*`、箭头 `m-*`、文字 `t-*` / `c-*`、泳道 `lifeline` / `act-*`、徽标 `badge b-*`
- 颜色含义固定：调度者 amber，内置空间 blue，Claude Code purple，用户的操作 green，最终结果 accent 蓝
- 实线表示派出，虚线表示交回，箭头上写具体内容
- 模板里没有的颜色，从 `src/simpleagent/web/styles.css` 取，浅色和深色两套 token 都要写

**页面结构：**

1. 开头：一句话讲清功能（`h1`），一段 `lede`，再用 `terms` 做 3 个左右的名词解释
2. 图，用手写的内联 SVG，按功能挑合适的一到三种：
   - 以前 vs 现在：同一件事左右对照，只差新加的那一层
   - 时序泳道图：一次完整流程，从上往下按时间排
   - 判断流程图：什么时候会停下来问人
3. 界面示意卡片、「几条写死在代码里的规矩」、「现在还做不到的」
4. 页脚注明图里的场景是举例

**写法：**

- 读者是产品经理：用产品里的词（空间、调度者、计划卡……），不出现函数名、字段名、文件路径。
- 例子要具体：虚构但可信的场景（模板用的是理财 / 健康 / 旅游三个空间 + 东京出差）。
  不写没验证过的效果承诺。
- 每个分节是 `<section class="x-block" id="<英文 id>">`，截图靠这个 id。
- `<title>` 用两到四个字的功能名。

## 3. 截图并自查

```bash
uv run --with pillow python .claude/skills/explainer/shot.py \
    <目录>/page.html <目录>/png <功能中文名> <分节id>:<图名> ...
```

- 只给画了图的分节截单张，脚本会另外出一张全页长图。
- 用 Read 看每张 PNG：文字有没有溢出框、互相压住，箭头有没有指错，SVG 有没有被截断。
  有问题就改 `page.html` 再截，最多三轮。
- 脚本已经处理了无头 Chrome 写完图不退出的问题。脚本报错就看报错，不要自己另起 Chrome 排查。

## 4. 发布和交付

- 用 Artifact 工具发布 `page.html`。工具要求先做的准备照做，但版式和配色以模板为准。
- 用 SendUserFile 把 PNG 发给用户（全页长图 + 每张单图）；这个工具用不了就在汇报里列出路径。

## 5. 汇报

汇报会被主会话转给用户，要简短：

- Artifact 链接，并提醒：默认只有自己能看，要在 Share 里开放才能发给别人
- PNG 文件列表
- 用了什么举例场景
- 哪些说法从代码确认过，哪些只凭文档；有没有把握不准、需要用户看一眼的地方
