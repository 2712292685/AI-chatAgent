"""终端输出：配色、横幅、提示符、流式片段、错误与重试提示。

所有写屏幕的代码都集中在这里。是否着色由 :func:`init_terminal` 在启动时
检测一次决定：非 TTY 环境（输出被重定向到文件或管道）时输出中不含任何
转义码，避免日志文件里混进 ``\\x1b[36m`` 之类的乱码。
"""

from __future__ import annotations

import contextlib
import ctypes
import sys
import unicodedata
from typing import Any

from .persona import Persona

# ANSI 转义码：人设名用 bold cyan，提示符与边框用 dim，错误用 red，重试提示用 yellow。
_RESET = "\x1b[0m"
_BOLD_CYAN = "\x1b[1;36m"
_DIM = "\x1b[2m"
_RED = "\x1b[31m"
_YELLOW = "\x1b[33m"

_PROMPT = "你 › "
_CONTINUATION_PROMPT = "   ... "
_HINT = "  输入内容开始对话。Ctrl+D 或输入 exit 退出。"

# 横幅方框：docs/01 §7.1。宽度按显示列数算，右框线在中文人设名下也对齐。
_BOX_TOP_LEFT = "╭"
_BOX_TOP_RIGHT = "╮"
_BOX_BOTTOM_LEFT = "╰"
_BOX_BOTTOM_RIGHT = "╯"
_BOX_HORIZONTAL = "─"
_BOX_VERTICAL = "│"
_BANNER_INDENT = "  "
_BANNER_MIN_INNER = 44
_WIDTH_WIDE = frozenset({"W", "F"})

# Windows 控制台常量：STD_OUTPUT_HANDLE / STD_ERROR_HANDLE 与 VT 处理开关。
_STD_OUTPUT_HANDLE = -11
_STD_ERROR_HANDLE = -12
_ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004

_is_tty = False
_stdout_color = False
_stderr_color = False


def init_terminal() -> None:
    """启动时调用一次：强制 UTF-8 输出，并按终端能力决定是否着色。

    Windows 上尝试用 ``SetConsoleMode`` 打开 ANSI 转义支持；拿不到控制台
    句柄（重定向）或调用失败时整体降级为无颜色，绝不因此抛异常。
    """
    _force_utf8()

    global _is_tty, _stdout_color, _stderr_color
    _is_tty = _isatty(sys.stdout)
    _stdout_color = _is_tty and _enable_windows_vt(_STD_OUTPUT_HANDLE)
    _stderr_color = _isatty(sys.stderr) and _enable_windows_vt(_STD_ERROR_HANDLE)


def print_banner(persona: Persona) -> None:
    """打印启动横幅。

    TTY 里画方框（docs/01 §7.1）；非 TTY 降级为无边框两行，管道里看不到框线。
    """
    if _is_tty:
        for line in banner_lines(persona.name, persona.role, color=_stdout_color):
            print(line)
    else:
        print(f"  {persona.name}")
        print(f"  {persona.role}")
    print()
    print(_HINT)
    print()


def banner_lines(name: str, role: str, *, color: bool) -> list[str]:
    """生成方框横幅的四行文本，宽度按显示列数补齐。

    纯函数：``color`` 为 False 时返回纯文本，便于在无终端环境里校验对齐。
    """
    inner = max(_BANNER_MIN_INNER, 2 + display_width(name), 2 + display_width(role))
    horizontal = _BOX_HORIZONTAL * inner
    bar = _paint(_BOX_VERTICAL, _DIM, color)
    top = _paint(_BOX_TOP_LEFT + horizontal + _BOX_TOP_RIGHT, _DIM, color)
    bottom = _paint(_BOX_BOTTOM_LEFT + horizontal + _BOX_BOTTOM_RIGHT, _DIM, color)
    name_row = bar + "  " + _paint(name, _BOLD_CYAN, color) + _padding(name, inner) + bar
    role_row = bar + "  " + role + _padding(role, inner) + bar
    return [
        _BANNER_INDENT + top,
        _BANNER_INDENT + name_row,
        _BANNER_INDENT + role_row,
        _BANNER_INDENT + bottom,
    ]


def display_width(text: str) -> int:
    """按终端列数计算显示宽度：宽字符与全角字符占 2 列，其余占 1 列。"""
    return sum(2 if unicodedata.east_asian_width(char) in _WIDTH_WIDE else 1 for char in text)


def _padding(text: str, inner: int) -> str:
    """补足 ``两个空格 + text`` 到 ``inner`` 列的空白。"""
    return " " * (inner - 2 - display_width(text))


def prompt_user() -> str:
    """读一行输入；空行与纯空白由调用方决定怎么处理。"""
    return input(_out(_PROMPT, _DIM))


def prompt_continuation() -> str:
    """续行提示符，与 docs/01 §7.3 的 ``   ... `` 完全一致。"""
    return input(_out(_CONTINUATION_PROMPT, _DIM))


def print_reply_prefix(name: str) -> None:
    """打印回复前缀（``阿岩 › ``）；整轮只打印一次。"""
    stream_chunk(_out(f"{name} › ", _BOLD_CYAN))


def print_reply(name: str, text: str) -> None:
    """输出一句以人设名开头的完整文本（用于告别语等非流式内容）。"""
    print(_out(f"{name} › ", _BOLD_CYAN) + text)


def print_error(msg: str) -> None:
    """把错误写到 stderr，以 ``✗ `` 开头，不污染重定向后的对话正文。"""
    print(f"{_err('✗', _RED)} {msg}", file=sys.stderr)


def print_warning(msg: str) -> None:
    """把警告写到 stderr；不属于对话正文。"""
    print(f"{_err('!', _YELLOW)} {msg}", file=sys.stderr)


def print_retry(reason: str, attempt: int, total: int) -> None:
    """重试提示（docs/01 §7.3）：``! 请求失败（原因），正在重试 (n/2)...``。"""
    message = f"! 请求失败（{reason}），正在重试 ({attempt}/{total})..."
    print(_err(message, _YELLOW), file=sys.stderr)


def stream_chunk(text: str) -> None:
    """逐片段输出，不换行；写完立即 flush，让字符尽快可见。"""
    sys.stdout.write(text)
    sys.stdout.flush()


def end_stream() -> None:
    """回复结束：换行，让光标停在下一行行首。"""
    sys.stdout.write("\n")
    sys.stdout.flush()


def _out(text: str, code: str) -> str:
    return _paint(text, code, _stdout_color)


def _err(text: str, code: str) -> str:
    return _paint(text, code, _stderr_color)


def _paint(text: str, code: str, enabled: bool) -> str:
    if not enabled or not text:
        return text
    return f"{code}{text}{_RESET}"


def _isatty(stream: object) -> bool:
    isatty = getattr(stream, "isatty", None)
    if not callable(isatty):
        return False
    try:
        return bool(isatty())
    except (OSError, ValueError):
        return False


def _force_utf8() -> None:
    """把 stdout / stderr 固定为 UTF-8，避免 Windows 默认代码页把中文写坏。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        # 流已关闭或不支持重配置时静默跳过：编码问题不该挡住启动。
        with contextlib.suppress(OSError, ValueError):
            reconfigure(encoding="utf-8", errors="replace")


def _enable_windows_vt(std_handle: int) -> bool:
    """给控制台打开 ANSI 转义支持；非 Windows 恒成功，失败返回 False。"""
    if sys.platform != "win32":
        return True
    try:
        kernel32: Any = ctypes.WinDLL("kernel32", use_last_error=True)
    except (AttributeError, OSError):
        return False

    try:
        handle = kernel32.GetStdHandle(std_handle)
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        enabled = mode.value | _ENABLE_VIRTUAL_TERMINAL_PROCESSING
        return bool(kernel32.SetConsoleMode(handle, enabled))
    except (AttributeError, OSError, ValueError):
        return False
