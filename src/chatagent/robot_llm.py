"""带工具调用的流式模型请求。

``llm.py`` 保持原样；本模块只服务于 ``chatagent-robot``。文本 delta 按到达顺序
产出，同时以 ``index`` 为键累积工具调用：流式响应会把一个工具调用拆成多个
chunk，``id``、``name`` 和 ``arguments`` 必须分别拼接。
"""

from __future__ import annotations

import time
from collections.abc import Generator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .config import Config, load_api_key
from .errors import ChatAgentError
from .llm import _backoff_seconds, _translate_error
from .render import print_retry, print_warning

if TYPE_CHECKING:
    from openai import OpenAI, Stream
    from openai.types.chat import ChatCompletionChunk

_THINKING_DISABLED = {"thinking": {"type": "disabled"}}


@dataclass
class TurnResult:
    """一轮流式响应的完整结果；工具调用在流结束时已经拼接完整。"""

    text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str | None = None
    truncated: bool = False

    @property
    def has_output(self) -> bool:
        """本轮是否已经产出任何文本或工具调用。"""
        return bool(self.text or self.tool_calls or self.finish_reason)


@dataclass
class _PartialToolCall:
    index: int
    call_id: str = ""
    name: str = ""
    arguments: str = ""
    kind: str = "function"


def chat_turn(
    messages: Sequence[Mapping[str, Any]],
    cfg: Config,
    tools: Sequence[Mapping[str, Any]] | None = None,
    result: TurnResult | None = None,
) -> Generator[str, None, None]:
    """发起一次流式请求，边产出文本边把工具调用写入 ``result``。

    只在“本轮还没有任何文本或工具调用”时重试网络错误、限流与 5xx；一旦已经
    看到工具调用 delta，就绝不自动重试，避免重复执行真实动作。
    """
    outcome = result if result is not None else TurnResult()
    partials: dict[int, _PartialToolCall] = {}
    client = _build_client(cfg)
    request = list(messages)
    max_retries = cfg.api.max_retries
    attempt = 0

    while True:
        produced_before = outcome.has_output
        stream: Stream[ChatCompletionChunk] | None = None
        try:
            kwargs: dict[str, Any] = {
                "model": cfg.api.model,
                "messages": request,
                "temperature": cfg.model.temperature,
                "max_tokens": cfg.model.max_tokens,
                "extra_body": _THINKING_DISABLED,
                "stream": True,
            }
            if tools:
                kwargs["tools"] = list(tools)
                kwargs["tool_choice"] = "auto"
            stream = client.chat.completions.create(**kwargs)

            for chunk in stream:
                choices = getattr(chunk, "choices", None)
                if not choices:
                    continue
                choice = choices[0]
                delta = getattr(choice, "delta", None)
                if delta is not None:
                    text = getattr(delta, "content", None)
                    if isinstance(text, str) and text:
                        outcome.text += text
                        yield text
                    _accumulate_tool_calls(delta, partials, outcome)
                finish_reason = getattr(choice, "finish_reason", None)
                if isinstance(finish_reason, str):
                    outcome.finish_reason = finish_reason
                    if finish_reason == "length" and not outcome.truncated:
                        outcome.truncated = True
                        print_warning("回复被 max_tokens 截断")
            return
        except Exception as exc:
            error = _translate_error(exc)
            if not _can_retry(
                error,
                attempt=attempt,
                produced=produced_before or outcome.has_output,
                max_retries=max_retries,
            ):
                raise error from exc
            attempt += 1
            print_retry(error.message, attempt, max_retries)
            time.sleep(_backoff_seconds(attempt))
        finally:
            if stream is not None:
                stream.close()


def _accumulate_tool_calls(
    delta: object,
    partials: dict[int, _PartialToolCall],
    outcome: TurnResult,
) -> None:
    raw_calls = getattr(delta, "tool_calls", None) or []
    for raw in raw_calls:
        raw_index = getattr(raw, "index", 0)
        index = raw_index if isinstance(raw_index, int) else 0
        part = partials.setdefault(index, _PartialToolCall(index=index))
        call_id = getattr(raw, "id", None)
        if isinstance(call_id, str) and call_id:
            part.call_id = call_id
        kind = getattr(raw, "type", None)
        if isinstance(kind, str) and kind:
            part.kind = kind
        function = getattr(raw, "function", None)
        if function is not None:
            name = getattr(function, "name", None)
            if isinstance(name, str) and name:
                part.name += name
            arguments = getattr(function, "arguments", None)
            if isinstance(arguments, str) and arguments:
                part.arguments += arguments
        _sync_tool_calls(partials, outcome)


def _sync_tool_calls(
    partials: dict[int, _PartialToolCall],
    outcome: TurnResult,
) -> None:
    calls: list[dict[str, Any]] = []
    for index in sorted(partials):
        part = partials[index]
        calls.append(
            {
                "id": part.call_id,
                "type": part.kind,
                "function": {"name": part.name, "arguments": part.arguments},
            }
        )
    outcome.tool_calls = calls


def _can_retry(
    error: ChatAgentError,
    *,
    attempt: int,
    produced: bool,
    max_retries: int,
) -> bool:
    return error.retryable and not produced and attempt < max_retries


def _build_client(cfg: Config) -> OpenAI:
    from openai import OpenAI

    return OpenAI(
        api_key=load_api_key(),
        base_url=cfg.api.base_url,
        timeout=cfg.api.timeout_seconds,
        max_retries=0,
    )


# 供测试或调用方显式表达“这一轮的结果类型”。
ChatTurnResult = TurnResult
