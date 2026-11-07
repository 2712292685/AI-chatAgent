"""config.toml 的加载与校验测试。"""

from pathlib import Path

import pytest

from chatagent.config import load_config
from chatagent.errors import ConfigError

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
    assert config.model.thinking is False
    assert config.model.temperature == 0.8
    assert config.model.max_tokens == 4096
    assert config.context.budget_tokens == 32000


def test_missing_file_names_the_file() -> None:
    with pytest.raises(ConfigError) as excinfo:
        load_config(Path("no-such-dir") / "config.toml")

    assert "config.toml" in excinfo.value.message


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


def test_temperature_out_of_range_is_rejected(workdir: Path) -> None:
    text = VALID_CONFIG.replace("temperature = 0.8", "temperature = 2.5")

    with pytest.raises(ConfigError) as excinfo:
        load_config(write_config(workdir, text))

    assert "model.temperature" in excinfo.value.message


def test_budget_below_minimum_is_rejected(workdir: Path) -> None:
    text = VALID_CONFIG.replace("budget_tokens = 32000", "budget_tokens = 1999")

    with pytest.raises(ConfigError) as excinfo:
        load_config(write_config(workdir, text))

    assert "context.budget_tokens" in excinfo.value.message
