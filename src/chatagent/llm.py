"""DeepSeek 调用封装：一次非流式请求，以及把 SDK 异常翻译成项目自己的异常。"""

from __future__ import annotations

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

from .config import Config, load_api_key, load_config
from .errors import ApiError, AuthError, NetworkError, RateLimitError

_THINKING_DISABLED = {"thinking": {"type": "disabled"}}


def chat_once(system_prompt: str, user_text: str) -> str:
    """发送一轮对话并返回完整回复（本版不流式、不带历史）。"""
    config = load_config()
    client = _build_client(config)
    messages: list[ChatCompletionMessageParam] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_text},
    ]

    try:
        completion = client.chat.completions.create(
            model=config.api.model,
            messages=messages,
            temperature=config.model.temperature,
            max_tokens=config.model.max_tokens,
            extra_body=_THINKING_DISABLED,
        )
    except AuthenticationError as exc:
        raise AuthError("密钥无效，请检查环境变量 DEEPSEEK_API_KEY") from exc
    except openai.RateLimitError as exc:
        raise RateLimitError("请求过于频繁，稍等片刻再试") from exc
    except (APITimeoutError, APIConnectionError) as exc:
        raise NetworkError("网络连接失败或超时，请检查网络后重试") from exc
    except BadRequestError as exc:
        raise ApiError("请求被拒绝（可能是上下文过长）") from exc
    except APIStatusError as exc:
        raise _status_error(exc) from exc
    except Exception as exc:
        raise ApiError("发生了未预期的错误") from exc

    if not completion.choices:
        raise ApiError("模型没有返回任何内容")
    content = completion.choices[0].message.content or ""
    if not content.strip():
        raise ApiError("模型返回了空回复，请重试")
    return content


def _build_client(config: Config) -> OpenAI:
    return OpenAI(
        api_key=load_api_key(),
        base_url=config.api.base_url,
        timeout=config.api.timeout_seconds,
        # 本版不重试；SDK 默认会静默重试两次，必须显式关掉。
        max_retries=0,
    )


def _status_error(exc: APIStatusError) -> ApiError:
    if exc.status_code == 402:
        return ApiError("账户余额不足，请前往开放平台充值")
    if exc.status_code >= 500:
        return ApiError("服务端暂时不可用，请稍后再试")
    return ApiError(f"请求失败（HTTP {exc.status_code}）")
