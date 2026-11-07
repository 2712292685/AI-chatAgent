"""命令行入口：加载输入、打印横幅、进入对话循环。"""

from __future__ import annotations

from .config import load_api_key, load_config
from .errors import ChatAgentError
from .persona import Persona, load_persona
from .render import (
    end_stream,
    init_terminal,
    print_banner,
    print_error,
    print_interrupted,
    print_reply,
    print_reply_prefix,
    prompt_continuation,
    prompt_user,
    stream_chunk,
)
from .session import ChatSession

_EXIT_WORDS = frozenset({"exit", "quit"})
_FAREWELL = "拜拜，早点休息。"


def main() -> int:
    """启动入口；返回进程退出码（0 正常，1 配置或鉴权错误）。"""
    init_terminal()
    try:
        cfg = load_config()
        persona = load_persona()
        load_api_key()
    except ChatAgentError as exc:
        print_error(exc.message)
        return 1

    print_banner(persona)
    session = ChatSession(persona, persona.to_system_prompt(), cfg)
    return _chat_loop(session)


def is_exit_word(line: str) -> bool:
    """整行精确匹配退出词：忽略大小写与首尾空白。"""
    return line.strip().lower() in _EXIT_WORDS


def is_continuation(line: str) -> bool:
    r"""行尾单个反斜杠表示续行；以 ``\\`` 结尾是字面反斜杠，不续行。"""
    return line.endswith("\\") and not line.endswith("\\\\")


def strip_continuation(line: str) -> str:
    r"""去掉续行用的单个反斜杠；行尾 ``\\`` 还原成一个 ``\``。"""
    if is_continuation(line):
        return line[:-1]
    if line.endswith("\\\\"):
        return line[:-2] + "\\"
    return line


def _read_message() -> str:
    r"""读一条消息；行尾 ``\`` 继续读下一行，多行用换行符拼成一条。"""
    line = prompt_user()
    parts: list[str] = []
    while True:
        parts.append(strip_continuation(line))
        if not is_continuation(line):
            return "\n".join(parts)
        line = prompt_continuation()


def _chat_loop(session: ChatSession) -> int:
    persona = session.persona
    while True:
        try:
            text = _read_message()
        except (KeyboardInterrupt, EOFError):
            print()
            _say_farewell(persona)
            return 0

        if not text.strip():
            continue
        # 退出词只在整条消息是单独一行时成立，续行拼出来的内容照常发给模型。
        if "\n" not in text and is_exit_word(text):
            _say_farewell(persona)
            return 0

        print_reply_prefix(persona.name)
        stream = session.ask(text)
        try:
            for chunk in stream:
                stream_chunk(chunk)
        except KeyboardInterrupt:
            # 中断只结束这一轮：关掉生成器释放连接，回到提示符，会话继续。
            stream.close()
            print_interrupted()
            continue
        except ChatAgentError as exc:
            stream.close()
            end_stream()
            print_error(exc.message)
            continue
        finally:
            stream.close()

        end_stream()


def _say_farewell(persona: Persona) -> None:
    print_reply(persona.name, _FAREWELL)
