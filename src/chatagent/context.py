"""上下文预算：token 估算与按预算裁剪。

本模块是纯逻辑：不读文件、不发请求、不打印，便于直接单测。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

Role = Literal["system", "user", "assistant"]


@dataclass(frozen=True)
class Message:
    """一条对话消息；``role`` 只允许 system / user / assistant。"""

    role: Role
    content: str


def estimate_tokens(text: str) -> int:
    """保守估算一段文本的 token 数：CJK 每字 1.0，其余字符每字 0.4，另加 1。

    不引入 tokenizer 依赖。DeepSeek 官方口径约「1 汉字 ≈ 0.6 token」，
    这里按 1.0 计，宁可高估、不可低估。
    """
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    other = len(text) - cjk
    return int(cjk * 1.0 + other * 0.4) + 1


def build_payload(
    history: Sequence[Message],
    system_prompt: str,
    budget: int,
) -> list[Message]:
    """从最新往回按预算取历史，返回本次要发送的消息列表。

    纯函数：不修改传入的 ``history``，同样输入永远得到同样的输出。

    三条不变量（由单测覆盖）：

    1. 返回列表若非空，首条必为 ``user``——从最早处裁掉孤立的 assistant，
       不允许以 assistant 开头。
    2. 返回列表的末条是传入历史中最新的一条消息。本项目在追加本轮 user 之后
       调用本函数，因此它就是本轮用户消息（FR-05：最后一条必定是本轮 user）。
    3. 返回消息的估算总量（含 system prompt 与固定开销）不超过 ``budget``；
       唯一例外是最新那条消息自身已超预算——此时仍返回它，不吞用户输入。
    """
    used = estimate_tokens(system_prompt) + 8
    picked: list[Message] = []
    for message in reversed(history):
        cost = estimate_tokens(message.content) + 4
        if picked and used + cost > budget:
            break
        picked.append(message)
        used += cost
    picked.reverse()

    # 不允许以 assistant 开头：丢掉被预算截断的那半轮。
    while picked and picked[0].role != "user":
        picked.pop(0)
    return picked
