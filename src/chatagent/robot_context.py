"""工具调用感知的上下文估算与裁剪。

普通聊天只发送 ``user`` / ``assistant`` 文本；机器人会话还会发送 assistant 的
``tool_calls`` 以及紧随其后的 ``tool`` 结果。裁剪时必须把一条 user 消息及其后
所有 assistant/tool 消息视为一个完整轮次，绝不能把 ``tool_calls`` 和对应的
``tool`` 结果拆开，否则下一次请求会被模型服务直接拒绝。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from .context import estimate_tokens

Role = Literal["system", "user", "assistant", "tool"]
MessageDict = dict[str, Any]


@dataclass(frozen=True)
class RobotMessage:
    """一条机器人会话消息；``content`` 在仅含工具调用时可以为 None。"""

    role: Role
    content: str | None = None
    tool_calls: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    tool_call_id: str | None = None
    name: str | None = None

    def __post_init__(self) -> None:
        # 调用方可能传入 list；统一存成 tuple，避免冻结对象内部被悄悄改写。
        if not isinstance(self.tool_calls, tuple):
            object.__setattr__(self, "tool_calls", tuple(self.tool_calls))

    @classmethod
    def from_dict(cls, message: Mapping[str, Any]) -> RobotMessage:
        """把 OpenAI 风格的消息字典转成 :class:`RobotMessage`。"""
        raw_calls = message.get("tool_calls")
        calls: tuple[dict[str, Any], ...] = ()
        if isinstance(raw_calls, Sequence) and not isinstance(raw_calls, (str, bytes)):
            calls = tuple(dict(call) for call in raw_calls if isinstance(call, Mapping))
        role = message.get("role")
        if role not in {"system", "user", "assistant", "tool"}:
            raise ValueError(f"未知消息角色：{role!r}")
        content = message.get("content")
        tool_call_id = message.get("tool_call_id")
        name = message.get("name")
        return cls(
            role=role,
            content=content if isinstance(content, str) else None,
            tool_calls=calls,
            tool_call_id=tool_call_id if isinstance(tool_call_id, str) else None,
            name=name if isinstance(name, str) else None,
        )

    def to_dict(self) -> MessageDict:
        """转成 OpenAI SDK 接受的消息字典，省略没有值的可选字段。"""
        message: MessageDict = {"role": self.role}
        if self.content is not None:
            message["content"] = self.content
        elif self.role == "assistant" and self.tool_calls:
            message["content"] = None
        else:
            message["content"] = ""
        if self.tool_calls:
            message["tool_calls"] = [dict(call) for call in self.tool_calls]
        if self.tool_call_id is not None:
            message["tool_call_id"] = self.tool_call_id
        if self.name is not None:
            message["name"] = self.name
        return message


def as_message(value: RobotMessage | Mapping[str, Any]) -> RobotMessage:
    """把支持的消息表示统一成 :class:`RobotMessage`。"""
    return value if isinstance(value, RobotMessage) else RobotMessage.from_dict(value)


def as_dict(value: RobotMessage | Mapping[str, Any]) -> MessageDict:
    """把支持的消息表示统一成 OpenAI 消息字典。"""
    return value.to_dict() if isinstance(value, RobotMessage) else dict(value)


def estimate_message_tokens(message: RobotMessage | Mapping[str, Any]) -> int:
    """估算一条消息的 token 数，包含工具名、参数与调用 id。"""
    item = as_message(message)
    total = 4
    if item.content:
        total += estimate_tokens(item.content)
    if item.tool_call_id:
        total += estimate_tokens(item.tool_call_id)
    if item.name:
        total += estimate_tokens(item.name)
    for call in item.tool_calls:
        encoded = json.dumps(call, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        total += estimate_tokens(encoded) + 4
    return total


def estimate_messages_tokens(
    messages: Sequence[RobotMessage | Mapping[str, Any]],
    system_prompt: str = "",
) -> int:
    """估算一组消息加上 system prompt 的总 token 数。"""
    used = estimate_tokens(system_prompt) + 8
    return used + sum(estimate_message_tokens(message) for message in messages)


def trim_messages(
    history: Sequence[RobotMessage | Mapping[str, Any]],
    system_prompt: str,
    budget: int,
) -> list[MessageDict]:
    """按完整轮次裁剪历史，返回本次发送用的消息字典列表。

    - 以 user 消息为轮次边界；同一条 user 之后的所有 assistant / tool 消息
      都属于同一轮，裁剪时整体保留或整体丢弃。
    - 最新一轮即使超预算也会整体保留，避免吞掉用户刚说的话或半截工具调用。
    - 不修改传入的历史。
    """
    normalized = [as_dict(message) for message in history]
    while normalized and normalized[0].get("role") != "user":
        normalized.pop(0)
    if not normalized:
        return []

    rounds: list[list[MessageDict]] = []
    current: list[MessageDict] = []
    for message in normalized:
        if message.get("role") == "user" and current:
            rounds.append(current)
            current = []
        current.append(message)
    if current:
        rounds.append(current)

    used = estimate_tokens(system_prompt) + 8
    picked: list[list[MessageDict]] = []
    for group in reversed(rounds):
        cost = sum(estimate_message_tokens(message) for message in group)
        if picked and used + cost > budget:
            break
        picked.append(group)
        used += cost
    picked.reverse()
    return [dict(message) for group in picked for message in group]


# 与原有 context.build_payload 同名的别名，方便调用方迁移理解。
build_payload = trim_messages
build_robot_payload = trim_messages
trim_history = trim_messages
