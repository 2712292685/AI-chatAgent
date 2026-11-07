"""人设结构与 system prompt 渲染测试。"""

from pathlib import Path

import pytest

from chatagent.errors import ConfigError
from chatagent.persona import Persona, load_persona


def make_persona(**overrides: object) -> Persona:
    fields: dict[str, object] = {
        "name": "阿岩",
        "role": "你的深夜自习室同桌",
        "personality": "慢热，但听得进话。",
        "style": "短句为主。",
        "background": "认识你两年多，知道你在准备一场考试。",
        "rules": ("不主动追问隐私", "不确定就说不确定"),
        "extra": "你偶尔会提起之前聊过的事。",
    }
    fields.update(overrides)
    return Persona(**fields)  # type: ignore[arg-type]


def test_full_persona_renders_every_section() -> None:
    prompt = make_persona().to_system_prompt()

    assert prompt.startswith("你是阿岩。\n\n【身份】你的深夜自习室同桌")
    assert "【性格】慢热，但听得进话。" in prompt
    assert "【说话风格】短句为主。" in prompt
    assert "【背景】认识你两年多，知道你在准备一场考试。" in prompt
    assert "【行为规则】\n1. 不主动追问隐私\n2. 不确定就说不确定" in prompt
    assert "你偶尔会提起之前聊过的事。" in prompt
    assert "【通用约束】" in prompt
    assert "- 始终以第一人称说话，保持「阿岩」这个身份，不要跳出角色。" in prompt


def test_empty_background_leaves_no_empty_heading() -> None:
    prompt = make_persona(background="").to_system_prompt()

    assert "【背景】" not in prompt
    assert "\n\n\n" not in prompt


def test_empty_rules_omit_the_whole_section() -> None:
    prompt = make_persona(rules=()).to_system_prompt()

    assert "【行为规则】" not in prompt
    assert "\n\n\n" not in prompt


def test_empty_extra_is_omitted() -> None:
    prompt = make_persona(extra="").to_system_prompt()

    assert "你偶尔会提起之前聊过的事。" not in prompt
    assert prompt.endswith("需要分点时用「1. 2. 3.」纯文本。")


def test_rendering_is_deterministic() -> None:
    persona = make_persona()

    first = persona.to_system_prompt()
    second = persona.to_system_prompt()

    assert first == second
    assert first.encode("utf-8") == make_persona().to_system_prompt().encode("utf-8")


def test_name_is_used_in_prompt_constraints() -> None:
    prompt = make_persona(name="小夏").to_system_prompt()

    assert prompt.startswith("你是小夏。")
    assert "保持「小夏」这个身份" in prompt


def test_empty_name_is_rejected_with_file_and_field(workdir: Path) -> None:
    persona_path = workdir / "persona.toml"
    persona_path.write_text(
        'name = ""\nrole = "你的私人学习搭子"\npersonality = "温和"\nstyle = "短句"\n',
        encoding="utf-8",
    )

    with pytest.raises(ConfigError) as excinfo:
        load_persona(persona_path)

    assert "persona.toml" in excinfo.value.message
    assert "name" in excinfo.value.message
