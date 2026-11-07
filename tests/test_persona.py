"""人设结构与 system prompt 渲染测试。"""

from pathlib import Path

import pytest

from chatagent.errors import ConfigError
from chatagent.persona import DEFAULT_FAREWELL, Persona, load_persona

VALID_PERSONA = """\
name = "阿岩"
role = "你的深夜自习室同桌"
personality = "慢热，但听得进话。"
style = "短句为主。"
background = "认识你两年多。"
rules = ["不主动追问隐私", "不确定就说不确定"]
extra = "你偶尔会提起之前聊过的事。"
farewell = "去睡吧，明天再战。"
"""


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


def write_persona(directory: Path, text: str) -> Path:
    path = directory / "persona.toml"
    path.write_text(text, encoding="utf-8")
    return path


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


def test_load_valid_persona_reads_every_field(workdir: Path) -> None:
    persona = load_persona(write_persona(workdir, VALID_PERSONA))

    assert persona.name == "阿岩"
    assert persona.role == "你的深夜自习室同桌"
    assert persona.rules == ("不主动追问隐私", "不确定就说不确定")
    assert persona.farewell == "去睡吧，明天再战。"
    assert persona.farewell_line() == "去睡吧，明天再战。"


def test_missing_persona_file_names_the_file() -> None:
    with pytest.raises(ConfigError) as excinfo:
        load_persona(Path("no-such-dir") / "persona.toml")

    assert "persona.toml" in excinfo.value.message


def test_persona_toml_syntax_error_is_reported(workdir: Path) -> None:
    with pytest.raises(ConfigError) as excinfo:
        load_persona(write_persona(workdir, "name = \n"))

    assert "TOML 格式错误" in excinfo.value.message


@pytest.mark.parametrize("field", ["name", "role", "personality", "style"])
def test_empty_required_field_is_rejected(workdir: Path, field: str) -> None:
    lines = [line for line in VALID_PERSONA.splitlines() if not line.startswith(f"{field} =")]
    lines.append(f'{field} = ""')

    with pytest.raises(ConfigError) as excinfo:
        load_persona(write_persona(workdir, "\n".join(lines) + "\n"))

    assert field in excinfo.value.message
    assert "不能为空" in excinfo.value.message


def test_name_longer_than_limit_is_rejected(workdir: Path) -> None:
    text = VALID_PERSONA.replace('name = "阿岩"', f'name = "{"长" * 13}"')

    with pytest.raises(ConfigError) as excinfo:
        load_persona(write_persona(workdir, text))

    assert "name" in excinfo.value.message
    assert "最长 12 字符" in excinfo.value.message


def test_optional_field_with_wrong_type_is_rejected(workdir: Path) -> None:
    text = VALID_PERSONA.replace('background = "认识你两年多。"', "background = 1")

    with pytest.raises(ConfigError) as excinfo:
        load_persona(write_persona(workdir, text))

    assert "background" in excinfo.value.message
    assert "必须是字符串" in excinfo.value.message


def test_single_string_rules_is_rejected(workdir: Path) -> None:
    text = VALID_PERSONA.replace(
        'rules = ["不主动追问隐私", "不确定就说不确定"]', 'rules = "不主动追问隐私"'
    )

    with pytest.raises(ConfigError) as excinfo:
        load_persona(write_persona(workdir, text))

    assert "rules 必须是字符串数组" in excinfo.value.message


def test_non_string_rule_is_rejected(workdir: Path) -> None:
    text = VALID_PERSONA.replace('rules = ["不主动追问隐私", "不确定就说不确定"]', "rules = [1]")

    with pytest.raises(ConfigError) as excinfo:
        load_persona(write_persona(workdir, text))

    assert "第 1 条必须是字符串" in excinfo.value.message


def test_empty_rule_is_rejected(workdir: Path) -> None:
    text = VALID_PERSONA.replace('rules = ["不主动追问隐私", "不确定就说不确定"]', 'rules = [""]')

    with pytest.raises(ConfigError) as excinfo:
        load_persona(write_persona(workdir, text))

    assert "第 1 条不能为空" in excinfo.value.message


def test_more_than_twenty_rules_are_rejected(workdir: Path) -> None:
    rules = ", ".join(f'"规则{i}"' for i in range(21))
    text = VALID_PERSONA.replace(
        'rules = ["不主动追问隐私", "不确定就说不确定"]', f"rules = [{rules}]"
    )

    with pytest.raises(ConfigError) as excinfo:
        load_persona(write_persona(workdir, text))

    assert "最多 20 条" in excinfo.value.message


def test_too_long_rule_is_rejected(workdir: Path) -> None:
    text = VALID_PERSONA.replace(
        'rules = ["不主动追问隐私", "不确定就说不确定"]', f'rules = ["{"规" * 121}"]'
    )

    with pytest.raises(ConfigError) as excinfo:
        load_persona(write_persona(workdir, text))

    assert "最长 120 字符" in excinfo.value.message


def test_too_long_extra_is_rejected(workdir: Path) -> None:
    text = VALID_PERSONA.replace('extra = "你偶尔会提起之前聊过的事。"', f'extra = "{"补" * 1001}"')

    with pytest.raises(ConfigError) as excinfo:
        load_persona(write_persona(workdir, text))

    assert "extra" in excinfo.value.message


def test_too_long_farewell_is_rejected(workdir: Path) -> None:
    text = VALID_PERSONA.replace('farewell = "去睡吧，明天再战。"', f'farewell = "{"晚" * 41}"')

    with pytest.raises(ConfigError) as excinfo:
        load_persona(write_persona(workdir, text))

    assert "farewell" in excinfo.value.message


def test_farewell_falls_back_to_the_default() -> None:
    assert make_persona().farewell_line() == DEFAULT_FAREWELL
    assert make_persona(farewell="   ").farewell_line() == DEFAULT_FAREWELL
    assert make_persona(farewell="  回见。 ").farewell_line() == "回见。"
