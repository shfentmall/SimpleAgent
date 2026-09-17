"""会话的结构化摘要：任务卡上那句「完成 · 改了 2 个文件，验证通过 · 用时 48 秒」。

按 W5 定下的口径——**不额外调模型**。控制面板是「看一眼就知道发生了什么」的地方，
为了一句人话总结去堵一次调用、多等一两秒不划算；真想要人话，做成卡片上的按需按钮。

摘要里每一项都能从 jsonl 消息流和 meta 里直接读出来，不依赖模型输出格式。
"""

from __future__ import annotations

from typing import Any

# 算「最后一句助手说了什么」时截断的长度
TAIL_LIMIT = 120


def summarize(messages: list[dict[str, Any]], meta: Any | None = None) -> dict[str, Any]:
    """把一个会话压成一张任务卡要用的几个数。

    返回：
        files_changed  改动过的文件数（write_file / edit_file 去重后）
        tool_calls     工具调用总次数
        tool_errors    报错的工具结果条数
        verification   验证状态（unknown/passed/failed/stale/running）
        last_text      最后一条助手消息（截断）
        turns          用户消息条数
    """
    files: set[str] = set()
    tool_calls = 0
    tool_errors = 0
    user_turns = 0
    last_text = ""

    for m in messages:
        role = m.get("role")
        if role == "user":
            user_turns += 1
        elif role == "assistant":
            content = m.get("content")
            if isinstance(content, str) and content.strip():
                last_text = content.strip()
            for tc in m.get("tool_calls") or []:
                tool_calls += 1
                name = ((tc or {}).get("function") or {}).get("name")
                if name in ("write_file", "edit_file"):
                    path = _arg_path((tc.get("function") or {}).get("arguments"))
                    if path:
                        files.add(path)
        elif role == "tool":
            text = str(m.get("content") or "")
            if text.strip().startswith(("错误", "Error", "error")) or "Traceback" in text:
                tool_errors += 1

    verification = "unknown"
    if meta is not None and getattr(meta, "verification", None) is not None:
        verification = meta.verification.status or "unknown"

    if len(last_text) > TAIL_LIMIT:
        last_text = last_text[:TAIL_LIMIT].rstrip() + "…"

    return {
        "files_changed": len(files),
        "files": sorted(files),
        "tool_calls": tool_calls,
        "tool_errors": tool_errors,
        "verification": verification,
        "last_text": last_text,
        "turns": user_turns,
    }


def _arg_path(arguments: Any) -> str | None:
    """从工具参数 JSON 里取 path。解析失败就放弃，不值得为此报错。"""
    if not arguments:
        return None
    if isinstance(arguments, dict):
        path = arguments.get("path")
    else:
        import json

        try:
            path = json.loads(arguments).get("path")
        except (ValueError, TypeError, AttributeError):
            return None
    return str(path) if path else None


def one_line(summary: dict[str, Any]) -> str:
    """任务卡上那一行结论。"""
    parts: list[str] = []
    if summary["files_changed"]:
        parts.append(f"改了 {summary['files_changed']} 个文件")
    if summary["tool_calls"]:
        parts.append(f"调用 {summary['tool_calls']} 次工具")
    if summary["tool_errors"]:
        parts.append(f"{summary['tool_errors']} 次报错")
    v = summary["verification"]
    if v == "passed":
        parts.append("验证通过")
    elif v == "failed":
        parts.append("验证未通过")
    elif v == "stale":
        parts.append("验证已失效")
    elif v == "running":
        parts.append("验证中")
    return " · ".join(parts) if parts else "没有文件改动"
