"""ChatSession 历史维护、告警与中断语义测试（chat_stream 全部打桩）。"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from chatagent import session as session_mod
from chatagent.config import ApiConfig, Config, ContextConfig, ModelConfig
from chatagent.context import Message
from chatagent.persona import Persona
from chatagent.session import ChatSession

PERSONA = Persona(name="阿岩", role="你的深夜自习室同桌", personality="慢热", style="短句")


def make_config(budget: int = 32000) -> Config:
    return Config(
        api=ApiConfig(
            base_url="https://api.deepseek.com",
            model="deepseek-flash",
            timeout_seconds=60,
            max_retries=0,
        ),
        model=ModelConfig(thinking=False, temperature=0.8, max_tokens=4096),
        context=ContextConfig(budget_tokens=budget),
    )


class Recorder:
    def __init__(self) -> None:
        self.payloads: list[list[Message]] = []
        self.closed = 0

    def stream(self, chunks: list[str]) -> object:
        def generator(system_prompt: str, messages: list[Message], cfg: Config) -> Iterator[str]:
            self.payloads.append(list(messages))
            try:
                yield from chunks
            finally:
                self.closed += 1

        return generator


def install(monkeypatch: pytest.MonkeyPatch, chunks: list[str]) -> Recorder:
    recorder = Recorder()
    monkeypatch.setattr(session_mod, "chat_stream", recorder.stream(chunks))
    return recorder


def make_session(budget: int = 32000) -> ChatSession:
    return ChatSession(PERSONA, "系统提示", make_config(budget))


def test_ask_streams_chunks_and_records_both_sides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = install(monkeypatch, ["你", "好"])
    session = make_session()

    assert list(session.ask("在吗")) == ["你", "好"]
    assert session.history == [
        Message(role="user", content="在吗"),
        Message(role="assistant", content="你好"),
    ]
    assert [message.role for message in recorder.payloads[0]] == ["user"]


def test_second_turn_payload_carries_the_first_round(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = install(monkeypatch, ["好"])
    session = make_session()

    list(session.ask("第一句"))
    list(session.ask("第二句"))

    second = recorder.payloads[1]
    assert [message.content for message in second] == ["第一句", "好", "第二句"]


def test_system_prompt_never_enters_the_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install(monkeypatch, ["好"])
    session = make_session()

    list(session.ask("在吗"))

    assert all(message.role != "system" for message in session.history)


def test_closing_mid_stream_drops_the_partial_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = install(monkeypatch, ["一", "二", "三"])
    session = make_session()

    stream = session.ask("说点什么")
    assert next(stream) == "一"
    stream.close()

    assert session.history == [Message(role="user", content="说点什么")]
    assert recorder.closed == 1


def test_empty_reply_is_not_written_to_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install(monkeypatch, [])
    session = make_session()

    assert list(session.ask("在吗")) == []
    assert session.history == [Message(role="user", content="在吗")]


def test_oversized_message_warns_but_is_still_sent(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    recorder = install(monkeypatch, ["好"])
    session = make_session(budget=2000)
    long_message = "长" * 3000

    list(session.ask(long_message))

    assert recorder.payloads[0][-1].content == long_message
    captured = capsys.readouterr()
    assert "已超出上下文预算 2000" in captured.err
    assert "仍照常发送" in captured.err


def test_long_session_trims_the_oldest_rounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = install(monkeypatch, ["回复" * 100])
    session = make_session(budget=2000)

    for index in range(12):
        list(session.ask(f"第{index}轮" + "字" * 50))

    counts = [len(payload) for payload in recorder.payloads]
    assert counts[0] == 1
    assert counts[-1] < counts[-1 - 1] + 2
    assert max(counts) < 2 * 12
    assert len(session.history) == 24
    assert recorder.payloads[-1][0].role == "user"
