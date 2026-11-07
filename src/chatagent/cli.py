"""命令行入口：加载输入、打印横幅、进入对话循环。"""

from __future__ import annotations

from .config import Config, load_api_key, load_config
from .context import Message
from .errors import ChatAgentError
from .llm import chat_stream
from .persona import Persona, load_persona
from .render import (
    end_stream,
    print_banner,
    print_error,
    print_reply,
    prompt_user,
    stream_chunk,
)

_EXIT_WORDS = {"exit", "quit"}
_FAREWELL = "拜拜，早点休息。"


def main() -> int:
    """启动入口；返回进程退出码（0 正常，1 配置或鉴权错误）。"""
    try:
        cfg = load_config()
        persona = load_persona()
        load_api_key()
    except ChatAgentError as exc:
        print_error(exc.message)
        return 1

    print_banner(persona)
    return _chat_loop(persona, persona.to_system_prompt(), cfg)


def _chat_loop(persona: Persona, system_prompt: str, cfg: Config) -> int:
    while True:
        try:
            line = prompt_user()
        except (KeyboardInterrupt, EOFError):
            print()
            _say_farewell(persona)
            return 0

        text = line.strip()
        if not text:
            continue
        if text.lower() in _EXIT_WORDS:
            _say_farewell(persona)
            return 0

        stream_chunk(f"{persona.name} › ")
        try:
            for chunk in chat_stream(system_prompt, [Message(role="user", content=text)], cfg):
                stream_chunk(chunk)
        except ChatAgentError as exc:
            end_stream()
            print_error(exc.message)
            return 1
        except KeyboardInterrupt:
            end_stream()
            print()
            _say_farewell(persona)
            return 0

        end_stream()


def _say_farewell(persona: Persona) -> None:
    print_reply(persona.name, _FAREWELL)
