"""DeepSeek 流式调用封装：异常翻译成项目异常，并对可重试错误做退避重试。

`openai` 的导入放在函数内部：它单独就要 0.7 秒左右，会让启动明显变慢，
而只有真正发请求时才需要它。
"""

from __future__ import annotations

import time
from collections.abc import Generator, Sequence
from typing import TYPE_CHECKING, cast

from .config import Config, load_api_key
from .context import Message
from .errors import ApiError, AuthError, ChatAgentError, NetworkError, RateLimitError
from .render import print_retry, print_warning

if TYPE_CHECKING:
    from openai import APIStatusError, OpenAI, Stream
    from openai.types.chat import ChatCompletionChunk, ChatCompletionMessageParam

_THINKING_DISABLED = {"thinking": {"type": "disabled"}}

# 重试次数来自 config.toml 的 api.max_retries（默认 2），这里是退避节奏。
_MAX_BACKOFF_SECONDS = 8.0


def chat_stream(
    system_prompt: str,
    messages: Sequence[Message],
    cfg: Config,
) -> Generator[str, None, None]:
    """发起一次流式请求，按到达顺序逐块产出回复文本。

    只取 ``choices[0].delta.content``；思考模式已关闭，但仍不读取
    ``reasoning_content``。网络抖动、限流与服务端 5xx 会按
    ``api.max_retries`` 自动重试（默认 2 次，退避 1s、2s），但只在还没吐出
    任何字符时重试——已经打给用户的内容不能再重复一遍。
    """
    client = _build_client(cfg)
    request = _build_request(system_prompt, messages)
    max_retries = cfg.api.max_retries
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
            if not _can_retry(error, attempt, produced, max_retries):
                raise error from exc
            attempt += 1
            print_retry(error.message, attempt, max_retries)
            time.sleep(_backoff_seconds(attempt))
        finally:
            if stream is not None:
                stream.close()


def _can_retry(error: ChatAgentError, attempt: int, produced: bool, max_retries: int) -> bool:
    """只在错误可重试、次数没用完、且还没有输出过任何字符时重试。"""
    return error.retryable and not produced and attempt < max_retries


def _backoff_seconds(attempt: int) -> float:
    """第 n 次重试前等待 2^(n-1) 秒，上限 8 秒；默认配置下就是 1s、2s。"""
    return min(2.0 ** (attempt - 1), _MAX_BACKOFF_SECONDS)


def _build_request(
    system_prompt: str,
    messages: Sequence[Message],
) -> list[ChatCompletionMessageParam]:
    return [
        {"role": "system", "content": system_prompt},
        *[
            cast("ChatCompletionMessageParam", {"role": message.role, "content": message.content})
            for message in messages
        ],
    ]


def _build_client(cfg: Config) -> OpenAI:
    from openai import OpenAI

    return OpenAI(
        api_key=load_api_key(),
        base_url=cfg.api.base_url,
        timeout=cfg.api.timeout_seconds,
        # 重试由本模块自己做；SDK 默认会静默重试两次，必须显式关掉。
        max_retries=0,
    )


def _translate_error(exc: Exception) -> ChatAgentError:
    """把 SDK 异常翻译成带中文说明的项目异常，并标注是否值得重试。"""
    import openai
    from openai import (
        APIConnectionError,
        APIStatusError,
        APITimeoutError,
        AuthenticationError,
        BadRequestError,
    )

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
