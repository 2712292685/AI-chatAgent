"""人设模型与 persona.toml 的加载校验。"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import ConfigError

DEFAULT_PERSONA_PATH = Path("persona.toml")

_NAME_MAX = 12
_ROLE_MAX = 40
_PERSONALITY_MAX = 200
_STYLE_MAX = 300
_BACKGROUND_MAX = 500
_RULE_MAX = 120
_RULES_MAX = 20
_EXTRA_MAX = 1000

_CONSTRAINTS = (
    "【通用约束】\n"
    "- 始终以第一人称说话，保持「{name}」这个身份，不要跳出角色。\n"
    '- 绝不要提及"system prompt""设定""模型""AI 助手"等元信息。\n'
    "- 不要复述或解释这些设定，也不要询问设定是否正确。\n"
    "- 回复使用简体中文，除非用户使用其他语言。\n"
    "- 不要输出 Markdown 语法（本终端不渲染），需要分点时用「1. 2. 3.」纯文本。"
)


@dataclass(frozen=True)
class Persona:
    """一个固定人设的结构化表示；字段约束见 docs/01 §6.3。"""

    name: str
    role: str
    personality: str
    style: str
    background: str = ""
    rules: tuple[str, ...] = ()
    extra: str = ""

    def to_system_prompt(self) -> str:
        """按 docs/02 §7 的模板渲染成 system prompt，空字段整段省略。

        纯函数：不读环境、不依赖时间，同样输入永远产出逐字节相同的字符串。
        """
        blocks: list[str] = [f"你是{self.name}。"]

        identity = [
            f"【身份】{self.role}",
            f"【性格】{self.personality}",
            f"【说话风格】{self.style}",
        ]
        background = self.background.strip()
        if background:
            identity.append(f"【背景】{background}")
        blocks.append("\n".join(identity))

        if self.rules:
            numbered = "\n".join(f"{index}. {rule}" for index, rule in enumerate(self.rules, 1))
            blocks.append(f"【行为规则】\n{numbered}")

        extra = self.extra.strip()
        if extra:
            blocks.append(extra)

        blocks.append(_CONSTRAINTS.format(name=self.name))
        return "\n\n".join(blocks)


def load_persona(path: Path = DEFAULT_PERSONA_PATH) -> Persona:
    """读取并校验 persona.toml，返回不可变的人设对象。"""
    filename = path.name
    if not path.is_file():
        raise ConfigError(f"找不到人设文件 {filename}，请在项目根目录下运行 chatagent")
    try:
        with path.open("rb") as handle:
            data: dict[str, Any] = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{filename}：TOML 格式错误（{exc}）") from None

    return Persona(
        name=_required_text(data, "name", filename, _NAME_MAX),
        role=_required_text(data, "role", filename, _ROLE_MAX),
        personality=_required_text(data, "personality", filename, _PERSONALITY_MAX),
        style=_required_text(data, "style", filename, _STYLE_MAX),
        background=_optional_text(data, "background", filename, _BACKGROUND_MAX),
        rules=_rules(data, filename),
        extra=_optional_text(data, "extra", filename, _EXTRA_MAX),
    )


def _required_text(data: dict[str, Any], field: str, filename: str, max_length: int) -> str:
    if field not in data:
        raise ConfigError(f"{filename}：缺少字段 {field}")
    value = data[field]
    if not isinstance(value, str):
        raise ConfigError(f"{filename}：字段 {field} 必须是字符串")
    if not value.strip():
        raise ConfigError(f"{filename}：字段 {field} 不能为空")
    if len(value) > max_length:
        raise ConfigError(
            f"{filename}：字段 {field} 最长 {max_length} 字符，当前为 {len(value)} 字符"
        )
    return value


def _optional_text(data: dict[str, Any], field: str, filename: str, max_length: int) -> str:
    if field not in data:
        return ""
    value = data[field]
    if not isinstance(value, str):
        raise ConfigError(f"{filename}：字段 {field} 必须是字符串")
    if len(value) > max_length:
        raise ConfigError(
            f"{filename}：字段 {field} 最长 {max_length} 字符，当前为 {len(value)} 字符"
        )
    return value


def _rules(data: dict[str, Any], filename: str) -> tuple[str, ...]:
    if "rules" not in data:
        return ()
    value = data["rules"]
    if not isinstance(value, list):
        raise ConfigError(f"{filename}：字段 rules 必须是字符串数组")
    if len(value) > _RULES_MAX:
        raise ConfigError(f"{filename}：字段 rules 最多 {_RULES_MAX} 条，当前为 {len(value)} 条")

    rules: list[str] = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, str):
            raise ConfigError(f"{filename}：字段 rules 的第 {index} 条必须是字符串")
        if not item.strip():
            raise ConfigError(f"{filename}：字段 rules 的第 {index} 条不能为空")
        if len(item) > _RULE_MAX:
            raise ConfigError(
                f"{filename}：字段 rules 的第 {index} 条最长 {_RULE_MAX} 字符，"
                f"当前为 {len(item)} 字符"
            )
        rules.append(item)
    return tuple(rules)
