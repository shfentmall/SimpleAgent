"""空间（Space）与会话元信息（SessionMeta）的数据模型。

设计要点（见 docs/design/client-ui.md）：
- Space 是唯一真值，落在 spaces/<id>/space.toml
- 会话消息只追加，落在 spaces/<id>/sessions/<sid>.jsonl
- 可变元信息（标题/状态/验证）走 sidecar spaces/<id>/sessions/<sid>.meta.json
- Task 不单独建模，就是 Session
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC
from typing import Any, Literal


def _now() -> str:
    from datetime import datetime

    # 毫秒分辨率，保证同一秒内多次写入也能稳定排序
    return datetime.now(UTC).astimezone().isoformat(timespec="milliseconds")


@dataclass
class GenericConfig:
    """通用任务空间：无专有目录，用 spaces/<id>/tmp。"""

    tmp_dir: str = "auto"  # auto => spaces/<id>/tmp

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> GenericConfig:
        return cls(tmp_dir=d.get("tmp_dir", "auto"))


@dataclass
class AgentBinding:
    """绑定到某个外部 agent（claude code / opencode / simpleagent 自身）。"""

    name: str = "simpleagent"  # simpleagent | claude-code | opencode
    command: str = "simpleagent"
    args: list[str] = field(default_factory=list)
    cwd: str | None = None
    resume_flag: str = ""  # 用于追问，配合 meta 里的 agent_session_id

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AgentBinding:
        return cls(
            name=d.get("name", "simpleagent"),
            command=d.get("command", d.get("name", "simpleagent")),
            args=list(d.get("args", [])),
            cwd=d.get("cwd"),
            resume_flag=d.get("resume_flag", ""),
        )


@dataclass
class VerifyConfig:
    """验证命令：session 停止/一轮结束时跑，用退出码判定。"""

    command: str | None = None
    trigger: Literal["off", "on_stop", "on_turn"] = "off"
    timeout: int = 300

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> VerifyConfig:
        return cls(
            command=d.get("command"),
            trigger=d.get("trigger", "off"),
            timeout=int(d.get("timeout", 300)),
        )


@dataclass
class Verification:
    """单个 session 的验证状态。"""

    status: str = "unknown"  # unknown|running|passed|failed|stale
    command: str | None = None
    exit_code: int | None = None
    started_at: str | None = None
    finished_at: str | None = None
    output_ref: str | None = None  # 完整输出落盘路径（复用 tool_outputs 那套）
    fingerprint: str | None = None  # 验证通过时的目录指纹，用于判 stale
    source: str = "auto"  # auto|manual

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Verification:
        return cls(
            status=d.get("status", "unknown"),
            command=d.get("command"),
            exit_code=d.get("exit_code"),
            started_at=d.get("started_at"),
            finished_at=d.get("finished_at"),
            output_ref=d.get("output_ref"),
            fingerprint=d.get("fingerprint"),
            source=d.get("source", "auto"),
        )


@dataclass
class SessionMeta:
    """会话的可变元信息（不进 jsonl，单独 sidecar）。"""

    id: str
    space_id: str
    title: str = "新会话"
    status: str = "idle"  # running|idle|done|error|cancelled
    pinned: bool = False
    agent: str = "simpleagent"
    agent_session_id: str | None = None
    created_at: str = ""
    updated_at: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    verification: Verification = field(default_factory=Verification)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SessionMeta:
        return cls(
            id=d["id"],
            space_id=d["space_id"],
            title=d.get("title", "新会话"),
            status=d.get("status", "idle"),
            pinned=d.get("pinned", False),
            agent=d.get("agent", "simpleagent"),
            agent_session_id=d.get("agent_session_id"),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
            usage=d.get("usage", {}) or {},
            verification=Verification.from_dict(d.get("verification", {}) or {}),
        )


@dataclass
class SpaceSpec:
    """新建空间的入参（来自向导）。"""

    name: str
    kind: Literal["generic", "agent"]
    profile: str = "default"
    pin_dir: str | None = None
    agent_name: str = "simpleagent"
    cwd: str | None = None
    command: str | None = None
    args: list[str] = field(default_factory=list)
    verify_command: str | None = None
    verify_trigger: Literal["off", "on_stop", "on_turn"] = "off"
    verify_timeout: int = 300


@dataclass
class Space:
    """一类任务的容器：名字 + 工作目录 + 用哪个 agent 跑 + 验证方式。"""

    id: str
    name: str
    kind: Literal["generic", "agent"]
    profile: str = "default"
    opened: bool = True  # 是否在左栏显示（关闭只是不显示，不删数据）
    pinned: bool = False
    created_at: str = ""
    last_opened_at: str = ""
    keep_sessions: int = 50  # 超出只归档不删
    generic: GenericConfig | None = None
    agent: AgentBinding | None = None
    verify: VerifyConfig | None = None

    @classmethod
    def from_spec(cls, spec: SpaceSpec, space_id: str) -> Space:
        created = _now()
        # 验证命令两类空间都支持（generic 也能跑 pytest 之类），先在这里统一构造
        verify = None
        if spec.verify_command:
            verify = VerifyConfig(
                command=spec.verify_command,
                trigger=spec.verify_trigger,
                timeout=spec.verify_timeout,
            )
        if spec.kind == "generic":
            generic = GenericConfig(tmp_dir="auto")
            return cls(
                id=space_id,
                name=spec.name,
                kind="generic",
                profile=spec.profile,
                created_at=created,
                last_opened_at=created,
                generic=generic,
                verify=verify,
            )
        agent = AgentBinding(
            name=spec.agent_name,
            command=spec.command or spec.agent_name,
            args=list(spec.args),
            cwd=spec.cwd,
        )
        return cls(
            id=space_id,
            name=spec.name,
            kind="agent",
            profile=spec.profile,
            created_at=created,
            last_opened_at=created,
            agent=agent,
            verify=verify,
        )

    def to_dict(self) -> dict[str, Any]:
        """整份空间定义序列化为可 JSON 化的 dict（嵌套 dataclass 一并展开）。"""
        return asdict(self)
