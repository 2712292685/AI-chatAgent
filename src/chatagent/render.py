"""终端输出：横幅、提示符、回复与错误。

本版只输出纯文本，不做颜色、不做边框。
"""

from __future__ import annotations

import sys

from .persona import Persona


def print_banner(persona: Persona) -> None:
    """打印启动横幅（docs/01 §7.1 的无边框纯文本版本）。"""
    print(f"  {persona.name}")
    print(f"  {persona.role}")
    print()
    print("  输入内容开始对话。Ctrl+D 或输入 exit 退出。")
    print()


def prompt_user() -> str:
    """读一行输入；空行与纯空白由调用方决定怎么处理。"""
    return input("你 › ")


def print_reply(name: str, text: str) -> None:
    """输出一句以人设名开头的回复。"""
    print(f"{name} › {text}")


def print_error(msg: str) -> None:
    """把错误写到 stderr，避免污染重定向后的对话正文。"""
    print(f"✗ {msg}", file=sys.stderr)


def print_warning(msg: str) -> None:
    """把警告写到 stderr；不属于对话正文。"""
    print(f"! {msg}", file=sys.stderr)


def stream_chunk(text: str) -> None:
    """逐片段输出，不换行；写完立即 flush，让字符尽快可见。"""
    sys.stdout.write(text)
    sys.stdout.flush()


def end_stream() -> None:
    """回复结束：换行，让光标停在下一行行首。"""
    sys.stdout.write("\n")
    sys.stdout.flush()
