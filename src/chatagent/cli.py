"""命令行入口：加载输入、打印横幅、进入对话循环。"""

from __future__ import annotations

from .config import load_api_key, load_config
from .errors import ChatAgentError
from .llm import chat_once
from .persona import Persona, load_persona
from .render import print_banner, print_error, print_reply, prompt_user

_EXIT_WORDS = {"exit", "quit"}
_FAREWELL = "拜拜，早点休息。"


def main() -> int:
    """启动入口；返回进程退出码（0 正常，1 配置或鉴权错误）。"""
    try:
        load_config()
        persona = load_persona()
        load_api_key()
    except ChatAgentError as exc:
        print_error(exc.message)
        return 1

    print_banner(persona)
    return _chat_loop(persona, persona.to_system_prompt())


def _chat_loop(persona: Persona, system_prompt: str) -> int:
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

        try:
            reply = chat_once(system_prompt, text)
        except ChatAgentError as exc:
            print_error(exc.message)
            return 1
        except KeyboardInterrupt:
            print()
            _say_farewell(persona)
            return 0

        print_reply(persona.name, reply)


def _say_farewell(persona: Persona) -> None:
    print_reply(persona.name, _FAREWELL)
