"""``chatagent-robot`` 命令行入口。

输入解析、续行与退出语义与原有 ``chatagent`` 保持一致；新增机器人健康检查、
工具调用显示，以及工具执行期间 Ctrl+C 的停车处理。
"""

from __future__ import annotations

from collections.abc import Callable

from .cli import is_continuation, is_exit_word, strip_continuation
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
    print_tool_call,
    print_tool_result,
    print_warning,
    prompt_continuation,
    prompt_user,
    stream_chunk,
)
from .robot_client import RobotClient
from .robot_policy import build_robot_system_prompt
from .robot_session import RobotSession
from .robot_tools import ToolExecutor


def main(confirm_clear_estop: Callable[[], bool] | None = None) -> int:
    """启动机器人控制 REPL；返回进程退出码。"""
    init_terminal()
    try:
        cfg = load_config()
        persona = load_persona()
        load_api_key()
        client = RobotClient(cfg.robot)
    except ChatAgentError as exc:
        print_error(exc.message)
        return 1

    try:
        client.get_health()
    except ChatAgentError as exc:
        print_warning(f"机器人控制服务当前不可用：{exc.message}（仍可进入对话）")

    executor = ToolExecutor(
        client,
        cfg.robot,
        confirm_clear_estop=confirm_clear_estop or confirm_clear_estop_interactive,
    )
    session = RobotSession(
        persona,
        build_robot_system_prompt(persona),
        cfg,
        client=client,
        executor=executor,
        on_tool_call=print_tool_call,
        on_tool_result=print_tool_result,
    )

    print_banner(persona)
    return _chat_loop(session, client)


def confirm_clear_estop_interactive() -> bool:
    """解除急停前的终端二次确认；默认回答 No。"""
    print("解除急停会让机器狗重新取得控制权并站起来。")
    try:
        answer = input("确认解除急停？[y/N] ")
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return answer.strip().lower() in {"y", "yes"}


def _read_message() -> str:
    """读一条消息，沿用原 CLI 的反斜杠续行语义。"""
    line = prompt_user()
    parts: list[str] = []
    while True:
        parts.append(strip_continuation(line))
        if not is_continuation(line):
            return "\n".join(parts)
        line = prompt_continuation()


def _chat_loop(session: RobotSession, client: RobotClient) -> int:
    persona = session.persona
    while True:
        try:
            text = _read_message()
        except (KeyboardInterrupt, EOFError):
            print()
            _shutdown(client, persona)
            return 0

        if not text.strip():
            continue
        if "\n" not in text and is_exit_word(text):
            _shutdown(client, persona)
            return 0

        stream = session.ask(text)
        prefix_printed = False
        try:
            for chunk in stream:
                if not prefix_printed:
                    print_reply_prefix(persona.name)
                    prefix_printed = True
                stream_chunk(chunk)
        except KeyboardInterrupt:
            stream.close()
            _best_effort_stop(client, warn=True)
            if prefix_printed:
                end_stream()
            print_interrupted()
            continue
        except ChatAgentError as exc:
            stream.close()
            if prefix_printed:
                end_stream()
            print_error(exc.message)
            continue
        finally:
            stream.close()

        end_stream()


def _shutdown(client: RobotClient, persona: Persona) -> None:
    """退出时尽力停止运动，然后告别。"""
    _best_effort_stop(client, warn=False)
    print_reply(persona.name, persona.farewell_line())


def _best_effort_stop(client: RobotClient, *, warn: bool) -> None:
    try:
        client.stop()
    except ChatAgentError as exc:
        if warn:
            print_warning(f"停止机器人失败：{exc.message}")


def _say_farewell(persona: Persona) -> None:
    print_reply(persona.name, persona.farewell_line())


if __name__ == "__main__":  # pragma: no cover - 仅供 python -m 调试
    raise SystemExit(main())
