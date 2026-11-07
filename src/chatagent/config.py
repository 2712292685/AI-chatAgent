"""config.toml 的加载与校验。

本模块只负责把文件读成冻结的 dataclass，不打印任何内容：错误以 :class:`ConfigError`
抛出，由 cli 决定怎么呈现给用户。
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import AuthError, ConfigError

DEFAULT_CONFIG_PATH = Path("config.toml")
API_KEY_ENV = "DEEPSEEK_API_KEY"


@dataclass(frozen=True)
class ApiConfig:
    """接口与传输层参数。"""

    base_url: str
    model: str
    timeout_seconds: int
    max_retries: int


@dataclass(frozen=True)
class ModelConfig:
    """模型行为参数。"""

    thinking: bool
    temperature: float
    max_tokens: int


@dataclass(frozen=True)
class ContextConfig:
    """上下文预算参数。"""

    budget_tokens: int


@dataclass(frozen=True)
class Config:
    """config.toml 的完整内容。"""

    api: ApiConfig
    model: ModelConfig
    context: ContextConfig


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> Config:
    """读取并校验配置文件，返回不可变配置对象。"""
    data = _read_toml(path)
    filename = path.name

    api = _section(data, "api", filename)
    model = _section(data, "model", filename)
    context = _section(data, "context", filename)

    api_config = ApiConfig(
        base_url=_text(api, "api.base_url", filename),
        model=_text(api, "api.model", filename),
        timeout_seconds=_positive_int(api, "api.timeout_seconds", filename),
        max_retries=_non_negative_int(api, "api.max_retries", filename),
    )

    thinking = _bool(model, "model.thinking", filename)
    if thinking:
        raise ConfigError(f"{filename}：字段 model.thinking 不能为 true，MVP 仅支持关闭思考模式")
    temperature = _number(model, "model.temperature", filename)
    if not 0.0 <= temperature <= 2.0:
        raise ConfigError(
            f"{filename}：字段 model.temperature 必须位于 [0.0, 2.0] 之间，当前为 {temperature}"
        )
    model_config = ModelConfig(
        thinking=thinking,
        temperature=temperature,
        max_tokens=_positive_int(model, "model.max_tokens", filename),
    )

    budget_tokens = _positive_int(context, "context.budget_tokens", filename)
    if budget_tokens < 2000:
        raise ConfigError(
            f"{filename}：字段 context.budget_tokens 必须大于等于 2000，当前为 {budget_tokens}"
        )

    return Config(
        api=api_config,
        model=model_config,
        context=ContextConfig(budget_tokens=budget_tokens),
    )


def load_api_key(environ: Mapping[str, str] | None = None) -> str:
    """读取环境变量里的密钥；缺失时连同两种平台的设置方法一起报错。"""
    source = os.environ if environ is None else environ
    value = source.get(API_KEY_ENV, "").strip()
    if not value:
        raise AuthError(
            f"未找到环境变量 {API_KEY_ENV}。请先设置密钥再启动：\n"
            '  PowerShell: $env:DEEPSEEK_API_KEY = "sk-..."\n'
            '  bash:       export DEEPSEEK_API_KEY="sk-..."'
        )
    return value


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"找不到配置文件 {path.name}，请在项目根目录下运行 chatagent")
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path.name}：TOML 格式错误（{exc}）") from None


def _section(data: dict[str, Any], name: str, filename: str) -> dict[str, Any]:
    if name not in data:
        raise ConfigError(f"{filename}：缺少 [{name}] 配置段")
    value = data[name]
    if not isinstance(value, dict):
        raise ConfigError(f"{filename}：配置段 [{name}] 必须是一个表")
    return value


def _require(table: dict[str, Any], field: str, filename: str) -> Any:
    key = field.rsplit(".", 1)[-1]
    if key not in table:
        raise ConfigError(f"{filename}：缺少字段 {field}")
    return table[key]


def _text(table: dict[str, Any], field: str, filename: str) -> str:
    value = _require(table, field, filename)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{filename}：字段 {field} 必须是非空字符串")
    return value


def _bool(table: dict[str, Any], field: str, filename: str) -> bool:
    value = _require(table, field, filename)
    if not isinstance(value, bool):
        raise ConfigError(f"{filename}：字段 {field} 必须是布尔值（true 或 false）")
    return value


def _positive_int(table: dict[str, Any], field: str, filename: str) -> int:
    value = _integer(table, field, filename)
    if value <= 0:
        raise ConfigError(f"{filename}：字段 {field} 必须大于 0，当前为 {value}")
    return value


def _non_negative_int(table: dict[str, Any], field: str, filename: str) -> int:
    value = _integer(table, field, filename)
    if value < 0:
        raise ConfigError(f"{filename}：字段 {field} 不能为负数，当前为 {value}")
    return value


def _integer(table: dict[str, Any], field: str, filename: str) -> int:
    value = _require(table, field, filename)
    # bool 是 int 的子类，JSON 式的 true 不该被当成整数接受。
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{filename}：字段 {field} 必须是整数")
    return value


def _number(table: dict[str, Any], field: str, filename: str) -> float:
    value = _require(table, field, filename)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{filename}：字段 {field} 必须是数字")
    return float(value)
