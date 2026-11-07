"""llm 错误映射与重试测试：全部用假客户端，不接触网络。"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

import httpx2
import openai
import pytest

from chatagent import llm
from chatagent.config import ApiConfig, Config, ContextConfig, ModelConfig
from chatagent.context import Message
from chatagent.errors import ApiError, AuthError, NetworkError

CFG = Config(
    api=ApiConfig(
        base_url="https://api.deepseek.com",
        model="deepseek-flash",
        timeout_seconds=60,
        max_retries=0,
    ),
    model=ModelConfig(thinking=False, temperature=0.8, max_tokens=4096),
    context=ContextConfig(budget_tokens=32000),
)
SYSTEM = "system prompt"
MESSAGES = [Message(role="user", content="你好")]


@dataclass
class FakeDelta:
    content: str | None


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
    kwargs: list[dict[str, object]] = field(default_factory=list)

    def create(self, **kwargs: object) -> object:
        self.calls += 1
        self.kwargs.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@dataclass
class FakeChat:
    completions: FakeCompletions


@dataclass
class FakeClient:
    chat: FakeChat


def make_client(outcomes: list[object]) -> tuple[FakeClient, FakeCompletions]:
    completions = FakeCompletions(outcomes)
    return FakeClient(chat=FakeChat(completions=completions)), completions


def install(monkeypatch: pytest.MonkeyPatch, outcomes: list[object]) -> FakeCompletions:
    client, completions = make_client(outcomes)
    monkeypatch.setattr(llm, "_build_client", lambda cfg: client)
    monkeypatch.setattr(llm.time, "sleep", lambda seconds: sleeps.append(seconds))
    return completions


sleeps: list[float] = []


@pytest.fixture(autouse=True)
def reset_sleeps() -> Iterator[None]:
    sleeps.clear()
    yield
    sleeps.clear()


def chunk(text: str | None, finish: str | None = None) -> FakeChunk:
    return FakeChunk(choices=[FakeChoice(delta=FakeDelta(content=text), finish_reason=finish)])


def status_error(status: int) -> openai.APIStatusError:
    request = httpx2.Request("POST", "https://api.deepseek.com/chat/completions")
    response = httpx2.Response(status, request=request, json={"error": {"message": "boom"}})
    return openai.APIStatusError(f"status {status}", response=response, body=None)


def _response(status: int) -> httpx2.Response:
    request = httpx2.Request("POST", "https://api.deepseek.com/chat/completions")
    return httpx2.Response(status, request=request, json={"error": {"message": "boom"}})


def auth_error() -> openai.AuthenticationError:
    return openai.AuthenticationError("bad key", response=_response(401), body=None)


def rate_limit_error() -> openai.RateLimitError:
    return openai.RateLimitError("slow down", response=_response(429), body=None)


def bad_request_error() -> openai.BadRequestError:
    return openai.BadRequestError("bad request", response=_response(400), body=None)


def test_streams_text_chunks_and_ignores_empty_deltas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream = FakeStream([chunk("你"), chunk(""), chunk(None), chunk("好", "stop")])
    install(monkeypatch, [stream])

    assert list(llm.chat_stream(SYSTEM, MESSAGES, CFG)) == ["你", "好"]
    assert stream.closed is True
    assert sleeps == []


def test_request_carries_model_options_and_system_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completions = install(monkeypatch, [FakeStream([chunk("好", "stop")])])

    list(llm.chat_stream(SYSTEM, MESSAGES, CFG))

    kwargs = completions.kwargs[0]
    assert kwargs["model"] == "deepseek-flash"
    assert kwargs["stream"] is True
    assert kwargs["temperature"] == 0.8
    assert kwargs["max_tokens"] == 4096
    assert kwargs["extra_body"] == {"thinking": {"type": "disabled"}}
    assert kwargs["messages"] == [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": "你好"},
    ]


def test_length_finish_reason_warns_once(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    stream = FakeStream([chunk("半", "length"), chunk("句", "length")])
    install(monkeypatch, [stream])

    list(llm.chat_stream(SYSTEM, MESSAGES, CFG))

    captured = capsys.readouterr()
    assert captured.err.count("回复被 max_tokens 截断") == 1


def test_retryable_error_is_retried_once_then_succeeds(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    completions = install(monkeypatch, [status_error(500), FakeStream([chunk("好", "stop")])])

    assert list(llm.chat_stream(SYSTEM, MESSAGES, CFG)) == ["好"]
    assert completions.calls == 2
    assert sleeps == [1.0]
    assert "正在重试 (1/2)" in capsys.readouterr().err


def test_retries_exhausted_raises_the_mapped_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    completions = install(
        monkeypatch,
        [status_error(503), status_error(503), status_error(503)],
    )

    with pytest.raises(ApiError) as excinfo:
        list(llm.chat_stream(SYSTEM, MESSAGES, CFG))

    assert excinfo.value.message == "服务端暂时不可用，请稍后再试"
    assert completions.calls == 3
    assert sleeps == [1.0, 2.0]
    assert capsys.readouterr().err.count("正在重试") == 2


def test_auth_error_is_not_retried(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    completions = install(monkeypatch, [auth_error()])

    with pytest.raises(AuthError):
        list(llm.chat_stream(SYSTEM, MESSAGES, CFG))

    assert completions.calls == 1
    assert sleeps == []
    assert "正在重试" not in capsys.readouterr().err


def test_rate_limit_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    completions = install(
        monkeypatch,
        [rate_limit_error(), FakeStream([chunk("好", "stop")])],
    )

    assert list(llm.chat_stream(SYSTEM, MESSAGES, CFG)) == ["好"]
    assert completions.calls == 2


def test_timeout_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    request = httpx2.Request("POST", "https://api.deepseek.com/chat/completions")
    completions = install(
        monkeypatch,
        [openai.APITimeoutError(request), FakeStream([chunk("好", "stop")])],
    )

    assert list(llm.chat_stream(SYSTEM, MESSAGES, CFG)) == ["好"]
    assert completions.calls == 2


def test_connection_error_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    request = httpx2.Request("POST", "https://api.deepseek.com/chat/completions")
    completions = install(
        monkeypatch,
        [openai.APIConnectionError(request=request), FakeStream([chunk("好", "stop")])],
    )

    assert list(llm.chat_stream(SYSTEM, MESSAGES, CFG)) == ["好"]
    assert completions.calls == 2


def test_bad_request_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    completions = install(monkeypatch, [bad_request_error()])

    with pytest.raises(ApiError) as excinfo:
        list(llm.chat_stream(SYSTEM, MESSAGES, CFG))

    assert "上下文过长" in excinfo.value.message
    assert completions.calls == 1


def test_failure_after_partial_output_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = httpx2.Request("POST", "https://api.deepseek.com/chat/completions")
    stream = FakeStream([chunk("半")], error=openai.APIConnectionError(request=request))
    completions = install(monkeypatch, [stream])

    received: list[str] = []
    with pytest.raises(NetworkError):
        for piece in llm.chat_stream(SYSTEM, MESSAGES, CFG):
            received.append(piece)

    assert received == ["半"]
    assert completions.calls == 1
    assert stream.closed is True


def test_closing_the_generator_closes_the_http_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream = FakeStream([chunk("一"), chunk("二"), chunk("三")])
    install(monkeypatch, [stream])

    generator = llm.chat_stream(SYSTEM, MESSAGES, CFG)
    assert next(generator) == "一"
    generator.close()

    assert stream.closed is True


@pytest.mark.parametrize(
    ("error", "expected", "retryable"),
    [
        (auth_error(), "密钥无效，请检查环境变量 DEEPSEEK_API_KEY", False),
        (status_error(402), "账户余额不足，请前往开放平台充值", False),
        (status_error(403), "请求失败（HTTP 403）", False),
        (rate_limit_error(), "请求过于频繁，稍等片刻再试", True),
        (status_error(500), "服务端暂时不可用，请稍后再试", True),
        (bad_request_error(), "请求被拒绝（可能是上下文过长）", False),
    ],
)
def test_status_errors_map_to_chinese_messages(
    error: Exception, expected: str, retryable: bool
) -> None:
    mapped = llm._translate_error(error)

    assert mapped.message == expected
    assert mapped.retryable is retryable


def test_unexpected_error_maps_to_generic_message() -> None:
    mapped = llm._translate_error(ValueError("boom"))

    assert mapped.message == "发生了未预期的错误"
    assert mapped.retryable is False


def test_build_client_disables_sdk_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")

    client = llm._build_client(CFG)

    assert client.max_retries == 0
    assert str(client.base_url).startswith("https://api.deepseek.com")
