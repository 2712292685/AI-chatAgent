"""DeepSeek 流式调用封装，以及把 SDK 异常翻译成项目自己的异常。"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import cast

import openai
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    OpenAI,
)
from openai.types.chat import ChatCompletionMessageParam

from .config import Config, load_api_key
from .context import Message
from .errors import ApiError, AuthError, ChatAgentError, NetworkError, RateLimitError
from .render import print_warning

_THINKING_DISABLED = {"thinking": {"type": "disabled"}}


def chat_stream(
    system_prompt: str,
    messages: Sequence[Message],
    cfg: Config,
) -> Iterator[str]:
    """发起一次流式请求，按到达顺序逐块产出回复文本。

    只取 ``choices[0].delta.content``；思考模式已关闭，但仍不读取
    ``reasoning_content``。本版不重试。
    """
    client = _build_client(cfg)
    request: list[ChatCompletionMessageParam] = [
        {"role": "system", "content": system_prompt},
        *[
            cast(ChatCompletionMessageParam, {"role": message.role, "content": message.content})
            for message in messages
        ],
    ]

    try:
        stream = client.chat.completions.create(
            model=cfg.api.model,
            messages=request,
            temperature=cfg.model.temperature,
            max_tokens=cfg.model.max_tokens,
            extra_body=_THINKING_DISABLED,
            stream=True,
        )
    except Exception as exc:
        raise _translate_error(exc) from exc

    truncated = False
    try:
        for chunk in stream:
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            text = choice.delta.content
            if text:
                yield text
            if choice.finish_reason == "length" and not truncated:
                truncated = True
                print_warning("回复被 max_tokens 截断")
    except Exception as exc:
        # 建流成功但传输中途出错时，也要给出人话而不是堆栈。
        raise _translate_error(exc) from exc
    finally:
        stream.close()


def _build_client(cfg: Config) -> OpenAI:
    return OpenAI(
        api_key=load_api_key(),
        base_url=cfg.api.base_url,
        timeout=cfg.api.timeout_seconds,
        # 本版不重试；SDK 默认会静默重试两次，必须显式关掉。
        max_retries=0,
    )


def _translate_error(exc: Exception) -> ChatAgentError:
    if isinstance(exc, AuthenticationError):
        return AuthError("密钥无效，请检查环境变量 DEEPSEEK_API_KEY")
    if isinstance(exc, openai.RateLimitError):
        return RateLimitError("请求过于频繁，稍等片刻再试")
    if isinstance(exc, (APITimeoutError, APIConnectionError)):
        return NetworkError("网络连接失败或超时，请检查网络后重试")
    if isinstance(exc, BadRequestError):
        return ApiError("请求被拒绝（可能是上下文过长）")
    if isinstance(exc, APIStatusError):
        return _status_error(exc)
    return ApiError("发生了未预期的错误")


def _status_error(exc: APIStatusError) -> ApiError:
    if exc.status_code == 402:
        return ApiError("账户余额不足，请前往开放平台充值")
    if exc.status_code >= 500:
        return ApiError("服务端暂时不可用，请稍后再试")
    return ApiError(f"请求失败（HTTP {exc.status_code}）")
