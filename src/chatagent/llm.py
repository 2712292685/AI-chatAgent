"""DeepSeek 流式调用封装：异常翻译成项目异常，并对可重试错误做退避重试。"""

from __future__ import annotations

import time
from collections.abc import Generator, Sequence
from typing import cast

import openai
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    OpenAI,
    Stream,
)
from openai.types.chat import ChatCompletionChunk, ChatCompletionMessageParam

from .config import Config, load_api_key
from .context import Message
from .errors import ApiError, AuthError, ChatAgentError, NetworkError, RateLimitError
from .render import print_retry, print_warning

_THINKING_DISABLED = {"thinking": {"type": "disabled"}}

# 最多重试 2 次，间隔 1s、2s（docs/02 §9.3）。
_MAX_RETRIES = 2
_RETRY_DELAYS = (1.0, 2.0)


def chat_stream(
    system_prompt: str,
    messages: Sequence[Message],
    cfg: Config,
) -> Generator[str, None, None]:
    """发起一次流式请求，按到达顺序逐块产出回复文本。

    只取 ``choices[0].delta.content``；思考模式已关闭，但仍不读取
    ``reasoning_content``。网络抖动、限流与服务端 5xx 会自动重试，但只在
    还没吐出任何字符时重试——已经打给用户的内容不能再重复一遍。
    """
    client = _build_client(cfg)
    request = _build_request(system_prompt, messages)
    attempt = 0

    while True:
        produced = False
        stream: Stream[ChatCompletionChunk] | None = None
        try:
            stream = client.chat.completions.create(
                model=cfg.api.model,
                messages=request,
                temperature=cfg.model.temperature,
                max_tokens=cfg.model.max_tokens,
                extra_body=_THINKING_DISABLED,
                stream=True,
            )
            truncated = False
            for chunk in stream:
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                text = choice.delta.content
                if text:
                    produced = True
                    yield text
                if choice.finish_reason == "length" and not truncated:
                    truncated = True
                    print_warning("回复被 max_tokens 截断")
            return
        except Exception as exc:
            error = _translate_error(exc)
            if not _can_retry(error, attempt, produced):
                raise error from exc
            attempt += 1
            print_retry(error.message, attempt, _MAX_RETRIES)
            time.sleep(_RETRY_DELAYS[attempt - 1])
        finally:
            if stream is not None:
                stream.close()


def _can_retry(error: ChatAgentError, attempt: int, produced: bool) -> bool:
    """只在错误可重试、次数没用完、且还没有输出过任何字符时重试。"""
    return error.retryable and not produced and attempt < _MAX_RETRIES


def _build_request(
    system_prompt: str,
    messages: Sequence[Message],
) -> list[ChatCompletionMessageParam]:
    return [
        {"role": "system", "content": system_prompt},
        *[
            cast(ChatCompletionMessageParam, {"role": message.role, "content": message.content})
            for message in messages
        ],
    ]


def _build_client(cfg: Config) -> OpenAI:
    return OpenAI(
        api_key=load_api_key(),
        base_url=cfg.api.base_url,
        timeout=cfg.api.timeout_seconds,
        # 重试由本模块自己做；SDK 默认会静默重试两次，必须显式关掉。
        max_retries=0,
    )


def _translate_error(exc: Exception) -> ChatAgentError:
    """把 SDK 异常翻译成带中文说明的项目异常，并标注是否值得重试。"""
    if isinstance(exc, AuthenticationError):
        return AuthError("密钥无效，请检查环境变量 DEEPSEEK_API_KEY")
    if isinstance(exc, openai.RateLimitError):
        return RateLimitError("请求过于频繁，稍等片刻再试", retryable=True)
    if isinstance(exc, (APITimeoutError, APIConnectionError)):
        return NetworkError("网络连接失败或超时，请检查网络后重试", retryable=True)
    if isinstance(exc, BadRequestError):
        return ApiError("请求被拒绝（可能是上下文过长）")
    if isinstance(exc, APIStatusError):
        return _status_error(exc)
    return ApiError("发生了未预期的错误")


def _status_error(exc: APIStatusError) -> ApiError:
    if exc.status_code == 402:
        return ApiError("账户余额不足，请前往开放平台充值")
    if exc.status_code >= 500:
        return ApiError("服务端暂时不可用，请稍后再试", retryable=True)
    return ApiError(f"请求失败（HTTP {exc.status_code}）")
