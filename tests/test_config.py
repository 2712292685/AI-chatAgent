"""config.toml 的加载与校验测试。"""

from pathlib import Path

import pytest

from chatagent.config import load_api_key, load_config
from chatagent.errors import AuthError, ConfigError

VALID_CONFIG = """\
[api]
base_url = "https://api.deepseek.com"
model = "deepseek-flash"
timeout_seconds = 60
max_retries = 2

[model]
thinking = false
temperature = 0.8
max_tokens = 4096

[context]
budget_tokens = 32000
"""


def write_config(directory: Path, text: str) -> Path:
    path = directory / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_loads_valid_config(workdir: Path) -> None:
    config = load_config(write_config(workdir, VALID_CONFIG))

    assert config.api.base_url == "https://api.deepseek.com"
    assert config.api.model == "deepseek-flash"
    assert config.api.timeout_seconds == 60
    assert config.api.max_retries == 2
    assert config.model.thinking is False
    assert config.model.temperature == 0.8
    assert config.model.max_tokens == 4096
    assert config.context.budget_tokens == 32000


def test_missing_file_names_the_file() -> None:
    with pytest.raises(ConfigError) as excinfo:
        load_config(Path("no-such-dir") / "config.toml")

    assert "config.toml" in excinfo.value.message


def test_toml_syntax_error_is_reported(workdir: Path) -> None:
    with pytest.raises(ConfigError) as excinfo:
        load_config(write_config(workdir, "this is not = toml =\n"))

    assert "TOML 格式错误" in excinfo.value.message


def test_missing_section_is_reported(workdir: Path) -> None:
    text = VALID_CONFIG.split("[context]")[0]

    with pytest.raises(ConfigError) as excinfo:
        load_config(write_config(workdir, text))

    assert "缺少 [context] 配置段" in excinfo.value.message


def test_section_that_is_not_a_table_is_reported(workdir: Path) -> None:
    # 顶层放一个名叫 context 的非表值，触发「必须是一个表」而不是「缺少配置段」。
    text = "context = 1\n" + VALID_CONFIG.split("[context]")[0]

    with pytest.raises(ConfigError) as excinfo:
        load_config(write_config(workdir, text))

    assert "配置段 [context] 必须是一个表" in excinfo.value.message


def test_missing_field_names_file_and_field(workdir: Path) -> None:
    text = VALID_CONFIG.replace('model = "deepseek-flash"\n', "")

    with pytest.raises(ConfigError) as excinfo:
        load_config(write_config(workdir, text))

    assert "config.toml" in excinfo.value.message
    assert "api.model" in excinfo.value.message


def test_wrong_type_names_file_and_field(workdir: Path) -> None:
    text = VALID_CONFIG.replace("temperature = 0.8", 'temperature = "hot"')

    with pytest.raises(ConfigError) as excinfo:
        load_config(write_config(workdir, text))

    assert "config.toml" in excinfo.value.message
    assert "model.temperature" in excinfo.value.message


def test_thinking_true_is_rejected(workdir: Path) -> None:
    text = VALID_CONFIG.replace("thinking = false", "thinking = true")

    with pytest.raises(ConfigError) as excinfo:
        load_config(write_config(workdir, text))

    assert "config.toml" in excinfo.value.message
    assert "model.thinking" in excinfo.value.message


def test_thinking_must_be_a_boolean(workdir: Path) -> None:
    text = VALID_CONFIG.replace("thinking = false", 'thinking = "false"')

    with pytest.raises(ConfigError) as excinfo:
        load_config(write_config(workdir, text))

    assert "model.thinking" in excinfo.value.message


def test_temperature_out_of_range_is_rejected(workdir: Path) -> None:
    text = VALID_CONFIG.replace("temperature = 0.8", "temperature = 2.5")

    with pytest.raises(ConfigError) as excinfo:
        load_config(write_config(workdir, text))

    assert "model.temperature" in excinfo.value.message


def test_temperature_accepts_the_boundaries(workdir: Path) -> None:
    for value in ("0.0", "2.0"):
        text = VALID_CONFIG.replace("temperature = 0.8", f"temperature = {value}")
        config = load_config(write_config(workdir, text))
        assert config.model.temperature == float(value)


def test_budget_below_minimum_is_rejected(workdir: Path) -> None:
    text = VALID_CONFIG.replace("budget_tokens = 32000", "budget_tokens = 1999")

    with pytest.raises(ConfigError) as excinfo:
        load_config(write_config(workdir, text))

    assert "context.budget_tokens" in excinfo.value.message


def test_timeout_must_be_positive(workdir: Path) -> None:
    text = VALID_CONFIG.replace("timeout_seconds = 60", "timeout_seconds = 0")

    with pytest.raises(ConfigError) as excinfo:
        load_config(write_config(workdir, text))

    assert "api.timeout_seconds" in excinfo.value.message


def test_max_retries_must_not_be_negative(workdir: Path) -> None:
    text = VALID_CONFIG.replace("max_retries = 2", "max_retries = -1")

    with pytest.raises(ConfigError) as excinfo:
        load_config(write_config(workdir, text))

    assert "api.max_retries" in excinfo.value.message


def test_max_tokens_must_be_positive(workdir: Path) -> None:
    text = VALID_CONFIG.replace("max_tokens = 4096", "max_tokens = 0")

    with pytest.raises(ConfigError) as excinfo:
        load_config(write_config(workdir, text))

    assert "model.max_tokens" in excinfo.value.message


def test_empty_base_url_is_rejected(workdir: Path) -> None:
    text = VALID_CONFIG.replace('base_url = "https://api.deepseek.com"', 'base_url = "  "')

    with pytest.raises(ConfigError) as excinfo:
        load_config(write_config(workdir, text))

    assert "api.base_url" in excinfo.value.message


def test_api_key_is_read_from_the_environment() -> None:
    assert load_api_key({"DEEPSEEK_API_KEY": "  sk-test  "}) == "sk-test"


def test_missing_api_key_explains_both_shells() -> None:
    with pytest.raises(AuthError) as excinfo:
        load_api_key({})

    message = excinfo.value.message
    assert "DEEPSEEK_API_KEY" in message
    assert "PowerShell" in message
    assert "bash" in message


def test_blank_api_key_is_treated_as_missing() -> None:
    with pytest.raises(AuthError):
        load_api_key({"DEEPSEEK_API_KEY": "   "})
