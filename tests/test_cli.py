"""cli 对话循环：退出、空行、中断、错误与启动失败。"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from chatagent import cli
from chatagent.errors import ApiError, ConfigError
from chatagent.persona import Persona

PERSONA = Persona(name="阿岩", role="你的深夜自习室同桌", personality="慢热", style="短句")


class FakeSession:
    def __init__(
        self,
        chunks: tuple[str, ...] = ("好",),
        error: Exception | None = None,
        interrupt: bool = False,
    ) -> None:
        self.persona = PERSONA
        self.asked: list[str] = []
        self.chunks = chunks
        self.error = error
        self.interrupt = interrupt

    def ask(self, text: str) -> Iterator[str]:
        self.asked.append(text)
        chunks = self.chunks
        error = self.error
        interrupt = self.interrupt

        def generator() -> Iterator[str]:
            yield from chunks
            if interrupt:
                raise KeyboardInterrupt
            if error is not None:
                raise error

        return generator()


def script_prompts(monkeypatch: pytest.MonkeyPatch, lines: list[str | type[BaseException]]) -> None:
    prompts = iter(lines)

    def next_prompt() -> str:
        value = next(prompts)
        if isinstance(value, type) and issubclass(value, BaseException):
            raise value
        return value

    monkeypatch.setattr(cli, "prompt_user", next_prompt)
    monkeypatch.setattr(cli, "prompt_continuation", next_prompt)


def test_loop_exits_on_exit_word(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    script_prompts(monkeypatch, ["exit"])
    session = FakeSession()

    assert cli._chat_loop(session) == 0
    assert session.asked == []
    assert "阿岩 › 拜拜，早点休息。" in capsys.readouterr().out


def test_loop_exits_on_quit_with_padding(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    script_prompts(monkeypatch, ["  QUIT  "])

    assert cli._chat_loop(FakeSession()) == 0
    assert "拜拜" in capsys.readouterr().out


def test_loop_says_goodbye_on_eof(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    script_prompts(monkeypatch, [EOFError])

    assert cli._chat_loop(FakeSession()) == 0
    assert "拜拜" in capsys.readouterr().out


def test_loop_says_goodbye_on_idle_interrupt(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    script_prompts(monkeypatch, [KeyboardInterrupt])

    assert cli._chat_loop(FakeSession()) == 0
    assert "拜拜" in capsys.readouterr().out


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_blank_input_is_re_prompted_without_a_request(
    monkeypatch: pytest.MonkeyPatch, blank: str
) -> None:
    script_prompts(monkeypatch, [blank, "exit"])
    session = FakeSession()

    assert cli._chat_loop(session) == 0
    assert session.asked == []


def test_normal_turn_prints_prefix_and_chunks(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    script_prompts(monkeypatch, ["在吗", "exit"])
    session = FakeSession(chunks=("你", "好"))

    assert cli._chat_loop(session) == 0
    out = capsys.readouterr().out
    assert session.asked == ["在吗"]
    assert "阿岩 › 你好\n" in out


def test_multiline_message_containing_exit_is_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script_prompts(monkeypatch, ["第一行\\", "exit", "exit"])
    session = FakeSession()

    assert cli._chat_loop(session) == 0
    assert session.asked == ["第一行\nexit"]


def test_request_error_returns_to_the_prompt(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    script_prompts(monkeypatch, ["第一句", "第二句", "exit"])
    session = FakeSession(error=ApiError("服务端暂时不可用"))

    assert cli._chat_loop(session) == 0
    assert session.asked == ["第一句", "第二句"]
    assert "✗ 服务端暂时不可用" in capsys.readouterr().err


def test_interrupt_during_generation_keeps_the_session(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    script_prompts(monkeypatch, ["说一段", "还在吗", "exit"])
    session = FakeSession(chunks=("说", "到"), interrupt=True)

    assert cli._chat_loop(session) == 0
    out = capsys.readouterr().out
    assert "[已中断]" in out
    assert session.asked == ["说一段", "还在吗"]


def test_main_reports_startup_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> object:
        raise ConfigError("找不到配置文件 config.toml")

    monkeypatch.setattr(cli, "init_terminal", lambda: None)
    monkeypatch.setattr(cli, "load_config", boom)

    assert cli.main() == 1


def test_main_starts_the_loop(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "init_terminal", lambda: None)
    monkeypatch.setattr(cli, "load_config", lambda: object())
    monkeypatch.setattr(cli, "load_persona", lambda: PERSONA)
    monkeypatch.setattr(cli, "load_api_key", lambda: "sk-test")
    monkeypatch.setattr(cli, "ChatSession", lambda *args: FakeSession())
    script_prompts(monkeypatch, ["exit"])

    assert cli.main() == 0
    out = capsys.readouterr().out
    assert "阿岩" in out
    assert "拜拜" in out
