"""robot_cli：启动告警、REPL、Ctrl+C 停车与二次确认。"""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest

from chatagent import robot_cli
from chatagent.config import RobotConfig
from chatagent.errors import ConfigError
from chatagent.persona import Persona
from chatagent.robot_client import RobotClientError

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
        self.interrupt = False

        def generator() -> Iterator[str]:
            yield from chunks
            if interrupt:
                raise KeyboardInterrupt
            if error is not None:
                raise error

        return generator()


class FakeClient:
    def __init__(
        self,
        *,
        health_error: Exception | None = None,
        stop_error: Exception | None = None,
    ) -> None:
        self.health_error = health_error
        self.stop_error = stop_error
        self.stops = 0
        self.health_calls = 0

    def get_health(self, **kwargs: Any) -> dict[str, Any]:
        self.health_calls += 1
        if self.health_error is not None:
            raise self.health_error
        return {"ok": True}

    def stop(self) -> dict[str, Any]:
        self.stops += 1
        if self.stop_error is not None:
            raise self.stop_error
        return {"ok": True}


def script_prompts(
    monkeypatch: pytest.MonkeyPatch,
    lines: list[str | type[BaseException]],
) -> None:
    prompts = iter(lines)

    def next_prompt() -> str:
        value = next(prompts)
        if isinstance(value, type) and issubclass(value, BaseException):
            raise value
        return value

    monkeypatch.setattr(robot_cli, "prompt_user", next_prompt)
    monkeypatch.setattr(robot_cli, "prompt_continuation", next_prompt)


def test_normal_turn_prints_prefix_once_and_reuses_exit_words(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    script_prompts(monkeypatch, ["在吗", "  QUIT  "])
    session = FakeSession(chunks=("你", "好"))
    client = FakeClient()

    assert robot_cli._chat_loop(session, client) == 0  # type: ignore[arg-type]

    out = capsys.readouterr().out
    assert session.asked == ["在吗"]
    assert out.count("阿岩 › ") == 2  # 回复一次 + 告别一次
    assert "你好" in out
    assert "拜拜" in out
    assert client.stops == 1


def test_multiline_message_keeps_continuation_semantics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script_prompts(monkeypatch, ["第一行\\", "exit", "exit"])
    session = FakeSession()

    assert robot_cli._chat_loop(session, FakeClient()) == 0  # type: ignore[arg-type]
    assert session.asked == ["第一行\nexit"]


def test_ctrl_c_during_turn_stops_and_keeps_the_session(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    script_prompts(monkeypatch, ["走一步", "还在吗", "exit"])
    session = FakeSession(chunks=("走",), interrupt=True)
    client = FakeClient()

    assert robot_cli._chat_loop(session, client) == 0  # type: ignore[arg-type]

    out = capsys.readouterr().out
    assert "[已中断]" in out
    assert session.asked == ["走一步", "还在吗"]
    assert client.stops == 2  # Ctrl+C 一次 + 退出一次


def test_request_error_returns_to_the_prompt(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    script_prompts(monkeypatch, ["第一句", "第二句", "exit"])
    session = FakeSession(error=RobotClientError("服务不可达"))

    assert robot_cli._chat_loop(session, FakeClient()) == 0  # type: ignore[arg-type]
    assert session.asked == ["第一句", "第二句"]
    assert "✗ 服务不可达" in capsys.readouterr().err


def test_eof_farewell_attempts_one_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    script_prompts(monkeypatch, [EOFError])
    client = FakeClient()

    assert robot_cli._chat_loop(FakeSession(), client) == 0  # type: ignore[arg-type]
    assert client.stops == 1


def test_stop_failure_on_ctrl_c_is_reported_but_loop_continues(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    script_prompts(monkeypatch, ["走", "exit"])
    session = FakeSession(interrupt=True)
    client = FakeClient(stop_error=RobotClientError("停止接口失败"))

    assert robot_cli._chat_loop(session, client) == 0  # type: ignore[arg-type]

    err = capsys.readouterr().err
    assert "停止机器人失败" in err
    assert "停止接口失败" in err


def test_clear_estop_confirmation_accepts_only_yes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("builtins.input", lambda prompt="": " YES ")

    assert robot_cli.confirm_clear_estop_interactive() is True

    monkeypatch.setattr("builtins.input", lambda prompt="": "")
    assert robot_cli.confirm_clear_estop_interactive() is False


def test_clear_estop_confirmation_treats_eof_as_no(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def raise_eof(prompt: str = "") -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", raise_eof)

    assert robot_cli.confirm_clear_estop_interactive() is False
    assert capsys.readouterr().out.endswith("\n")


def test_main_warns_when_health_check_fails_but_continues(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeClient(health_error=RobotClientError("connection refused"))
    session = FakeSession()
    monkeypatch.setattr(robot_cli, "init_terminal", lambda: None)
    monkeypatch.setattr(
        robot_cli,
        "load_config",
        lambda: SimpleNamespace(robot=RobotConfig()),
    )
    monkeypatch.setattr(robot_cli, "load_persona", lambda: PERSONA)
    monkeypatch.setattr(robot_cli, "load_api_key", lambda: "sk-test")
    monkeypatch.setattr(robot_cli, "RobotClient", lambda cfg: client)
    monkeypatch.setattr(robot_cli, "ToolExecutor", lambda *args, **kwargs: object())
    monkeypatch.setattr(robot_cli, "RobotSession", lambda *args, **kwargs: session)
    script_prompts(monkeypatch, ["exit"])

    assert robot_cli.main() == 0
    assert client.health_calls == 1
    assert client.stops == 1
    assert "机器人控制服务当前不可用" in capsys.readouterr().err


def test_main_returns_1_on_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(robot_cli, "init_terminal", lambda: None)

    def boom() -> object:
        raise ConfigError("找不到配置文件 config.toml")

    monkeypatch.setattr(robot_cli, "load_config", boom)

    assert robot_cli.main() == 1
    assert "找不到配置文件" in capsys.readouterr().err
