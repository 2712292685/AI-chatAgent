"""robot_llm：流式文本、工具调用累积、重试边界与关闭行为。"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx2
import openai
import pytest

from chatagent import robot_llm
from chatagent.config import ApiConfig, Config, ContextConfig, ModelConfig
from chatagent.errors import NetworkError
from chatagent.robot_llm import TurnResult, chat_turn

CFG = Config(
    api=ApiConfig(
        base_url="https://api.deepseek.com",
        model="deepseek-flash",
        timeout_seconds=60,
        max_retries=2,
    ),
    model=ModelConfig(thinking=False, temperature=0.8, max_tokens=4096),
    context=ContextConfig(budget_tokens=32000),
)
MESSAGES = [{"role": "user", "content": "前进一米"}]
TOOLS = [{"type": "function", "function": {"name": "robot_status"}}]
SYSTEM = "system prompt"


@dataclass
class FakeFunction:
    name: str | None = None
    arguments: str | None = None


@dataclass
class FakeToolCall:
    index: int = 0
    id: str | None = None
    type: str | None = None
    function: FakeFunction | None = None


@dataclass
class FakeDelta:
    content: str | None = None
    tool_calls: list[FakeToolCall] = field(default_factory=list)


@dataclass
class FakeChoice:
    delta: FakeDelta
    finish_reason: str | None = None


@dataclass
class FakeChunk:
    choices: list[FakeChoice]


@dataclass
class FakeStream:
    chunks: list[FakeChunk]
    error: Exception | None = None
    closed: bool = False

    def __iter__(self) -> Iterator[FakeChunk]:
        yield from self.chunks
        if self.error is not None:
            raise self.error

    def close(self) -> None:
        self.closed = True


@dataclass
class FakeCompletions:
    outcomes: list[object]
    calls: int = 0
    kwargs: list[dict[str, Any]] = field(default_factory=list)

    def create(self, **kwargs: Any) -> object:
        self.calls += 1
        self.kwargs.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@dataclass
class FakeClient:
    completions: FakeCompletions

    @property
    def chat(self) -> object:
        return type("Chat", (), {"completions": self.completions})()


def install(monkeypatch: pytest.MonkeyPatch, outcomes: list[object]) -> FakeCompletions:
    completions = FakeCompletions(outcomes)
    client = FakeClient(completions)
    monkeypatch.setattr(robot_llm, "_build_client", lambda cfg: client)
    monkeypatch.setattr(robot_llm.time, "sleep", lambda seconds: sleeps.append(seconds))
    return completions


sleeps: list[float] = []


@pytest.fixture(autouse=True)
def reset_sleeps() -> Iterator[None]:
    sleeps.clear()
    yield
    sleeps.clear()


def chunk(
    text: str | None = None,
    *,
    tool_calls: list[FakeToolCall] | None = None,
    finish: str | None = None,
) -> FakeChunk:
    return FakeChunk(
        [FakeChoice(FakeDelta(content=text, tool_calls=tool_calls or []), finish_reason=finish)]
    )


def status_error(status: int) -> openai.APIStatusError:
    request = httpx2.Request("POST", "https://api.deepseek.com/chat/completions")
    response = httpx2.Response(status, request=request, json={"error": {"message": "boom"}})
    return openai.APIStatusError(f"status {status}", response=response, body=None)


def test_streams_text_and_sends_tool_options(monkeypatch: pytest.MonkeyPatch) -> None:
    completions = install(monkeypatch, [FakeStream([chunk("你"), chunk("好", finish="stop")])])
    result = TurnResult()

    assert list(chat_turn([*MESSAGES], CFG, TOOLS, result)) == ["你", "好"]
    assert result.text == "你好"
    assert result.tool_calls == []
    kwargs = completions.kwargs[0]
    assert kwargs["model"] == "deepseek-flash"
    assert kwargs["tools"] == TOOLS
    assert kwargs["tool_choice"] == "auto"
    assert kwargs["stream"] is True
    assert kwargs["temperature"] == 0.8
    assert kwargs["max_tokens"] == 4096
    assert kwargs["extra_body"] == {"thinking": {"type": "disabled"}}


def test_without_tools_omits_tool_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    completions = install(monkeypatch, [FakeStream([chunk("好", finish="stop")])])

    list(chat_turn(MESSAGES, CFG, None, TurnResult()))

    assert "tools" not in completions.kwargs[0]
    assert "tool_choice" not in completions.kwargs[0]


def test_accumulates_one_tool_call_split_across_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream = FakeStream(
        [
            chunk(
                tool_calls=[
                    FakeToolCall(
                        id="call_1",
                        type="function",
                        function=FakeFunction(name="robot_move", arguments='{"direction":'),
                    )
                ]
            ),
            chunk(
                tool_calls=[
                    FakeToolCall(function=FakeFunction(arguments='"forward","distance_m":1}'))
                ]
            ),
            chunk(finish="tool_calls"),
        ]
    )
    install(monkeypatch, [stream])
    result = TurnResult()

    assert list(chat_turn(MESSAGES, CFG, TOOLS, result)) == []
    assert stream.closed is True
    assert result.tool_calls == [
        {
            "id": "call_1",
            "type": "function",
            "function": {
                "name": "robot_move",
                "arguments": '{"direction":"forward","distance_m":1}',
            },
        }
    ]
    assert result.finish_reason == "tool_calls"


def test_accumulates_multiple_tool_calls_by_index(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = FakeStream(
        [
            chunk(
                tool_calls=[
                    FakeToolCall(
                        index=1,
                        id="call_2",
                        type="function",
                        function=FakeFunction(name="robot_status", arguments="{}"),
                    ),
                    FakeToolCall(
                        index=0,
                        id="call_1",
                        type="function",
                        function=FakeFunction(name="robot_stop", arguments="{}"),
                    ),
                ]
            )
        ]
    )
    install(monkeypatch, [stream])
    result = TurnResult()

    list(chat_turn(MESSAGES, CFG, TOOLS, result))

    assert [call["id"] for call in result.tool_calls] == ["call_1", "call_2"]


def test_invalid_arguments_are_preserved_for_the_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream = FakeStream(
        [
            chunk(
                tool_calls=[
                    FakeToolCall(
                        id="call_1",
                        function=FakeFunction(name="robot_move", arguments="{broken"),
                    )
                ]
            )
        ]
    )
    install(monkeypatch, [stream])
    result = TurnResult()

    list(chat_turn(MESSAGES, CFG, TOOLS, result))

    assert result.tool_calls[0]["function"]["arguments"] == "{broken"


def test_length_finish_reason_warns_once(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    stream = FakeStream([chunk("半", finish="length"), chunk("句", finish="length")])
    install(monkeypatch, [stream])
    result = TurnResult()

    list(chat_turn(MESSAGES, CFG, TOOLS, result))

    assert result.truncated is True
    assert capsys.readouterr().err.count("回复被 max_tokens 截断") == 1


def test_retries_before_any_output(monkeypatch: pytest.MonkeyPatch) -> None:
    completions = install(
        monkeypatch,
        [status_error(503), FakeStream([chunk("好", finish="stop")])],
    )

    assert list(chat_turn(MESSAGES, CFG, TOOLS, TurnResult())) == ["好"]
    assert completions.calls == 2
    assert sleeps == [1.0]


def test_does_not_retry_after_text_was_yielded(monkeypatch: pytest.MonkeyPatch) -> None:
    request = httpx2.Request("POST", "https://api.deepseek.com/chat/completions")
    stream = FakeStream(
        [chunk("半")],
        error=openai.APIConnectionError(request=request),
    )
    completions = install(monkeypatch, [stream])
    received: list[str] = []

    with pytest.raises(NetworkError):
        for piece in chat_turn(MESSAGES, CFG, TOOLS, TurnResult()):
            received.append(piece)

    assert received == ["半"]
    assert completions.calls == 1
    assert sleeps == []


def test_does_not_retry_after_a_tool_call_delta(monkeypatch: pytest.MonkeyPatch) -> None:
    request = httpx2.Request("POST", "https://api.deepseek.com/chat/completions")
    stream = FakeStream(
        [
            chunk(
                tool_calls=[
                    FakeToolCall(
                        id="call_1",
                        function=FakeFunction(name="robot_move", arguments='{"direction":'),
                    )
                ]
            )
        ],
        error=openai.APIConnectionError(request=request),
    )
    completions = install(monkeypatch, [stream])
    result = TurnResult()

    with pytest.raises(NetworkError):
        list(chat_turn(MESSAGES, CFG, TOOLS, result))

    assert result.tool_calls[0]["id"] == "call_1"
    assert completions.calls == 1
    assert sleeps == []


def test_closing_the_generator_closes_the_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = FakeStream([chunk("一"), chunk("二"), chunk("三")])
    install(monkeypatch, [stream])

    generator = chat_turn(MESSAGES, CFG, TOOLS, TurnResult())
    assert next(generator) == "一"
    generator.close()

    assert stream.closed is True
