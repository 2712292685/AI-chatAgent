"""会话状态：持有 system prompt 与历史，流式产出回复。"""

from __future__ import annotations

from collections.abc import Generator

from .config import Config
from .context import Message, build_payload, estimate_tokens
from .llm import chat_stream
from .persona import Persona
from .render import print_warning


class ChatSession:
    """一次进程内的对话会话；历史只在内存里，进程退出即丢失。"""

    def __init__(self, persona: Persona, system_prompt: str, cfg: Config) -> None:
        self.persona = persona
        self.system_prompt = system_prompt
        self.cfg = cfg
        self.history: list[Message] = []
        self.total_input_tokens = 0
        self.total_output_tokens = 0

    def ask(self, user_text: str) -> Generator[str, None, None]:
        """发一轮请求并逐块产出回复。

        历史里只放 user / assistant，system prompt 每次单独放在最前面。
        user 在请求前入历史，assistant 只有生成器正常读完才入历史——失败或
        被中断都不会留下残缺回复（写入代码在 ``finally`` 之后，提前关闭生成器
        时根本执行不到）。
        """
        self.history.append(Message(role="user", content=user_text))
        budget = self.cfg.context.budget_tokens

        needed = estimate_tokens(self.system_prompt) + 8 + estimate_tokens(user_text) + 4
        if needed > budget:
            print_warning(f"本轮消息约 {needed} token，已超出上下文预算 {budget}，仍照常发送")

        payload = build_payload(self.history, self.system_prompt, budget)
        self.total_input_tokens += estimate_tokens(self.system_prompt) + sum(
            estimate_tokens(message.content) for message in payload
        )

        chunks: list[str] = []
        stream = chat_stream(self.system_prompt, payload, self.cfg)
        try:
            for chunk in stream:
                chunks.append(chunk)
                yield chunk
        finally:
            # 消费者提前退出时，把底层流的连接一并释放。
            stream.close()

        reply = "".join(chunks)
        if reply:
            self.history.append(Message(role="assistant", content=reply))
            self.total_output_tokens += estimate_tokens(reply)
