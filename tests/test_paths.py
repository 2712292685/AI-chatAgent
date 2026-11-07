"""数据文件查找顺序与包内默认文件的测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from chatagent import paths

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_explicit_path_wins_even_if_missing(workdir: Path) -> None:
    explicit = workdir / "custom.toml"

    assert paths.resolve("config.toml", explicit) == explicit


def test_working_directory_file_wins(workdir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    local = workdir / "config.toml"
    local.write_text("[api]\n", encoding="utf-8")
    monkeypatch.chdir(workdir)

    assert paths.resolve("config.toml") == Path("config.toml")


def test_packaged_default_is_used_elsewhere(workdir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(workdir)

    resolved = paths.resolve("persona.toml")

    assert resolved == paths.PACKAGE_DATA_DIR / "persona.toml"
    assert resolved.is_file()


@pytest.mark.parametrize("name", ["config.toml", "persona.toml"])
def test_packaged_defaults_exist(name: str) -> None:
    assert paths.packaged(name).is_file()


@pytest.mark.parametrize("name", ["config.toml", "persona.toml"])
def test_repository_copies_match_the_packaged_defaults(name: str) -> None:
    """根目录副本供开发者改，包内副本随 wheel 发布；两者必须一致，避免漂移。"""
    root_copy = REPO_ROOT / name
    if not root_copy.is_file():
        pytest.skip("不是从仓库运行，跳过副本一致性检查")

    assert root_copy.read_bytes() == paths.packaged(name).read_bytes(), (
        f"{name} 的根目录副本与 src/chatagent/data 副本不一致，请同步"
    )


def test_packaged_config_still_loads_from_an_unrelated_directory(
    workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chatagent.config import load_config
    from chatagent.persona import load_persona

    monkeypatch.chdir(workdir)

    config = load_config()
    persona = load_persona()

    assert config.api.model == "deepseek-flash"
    assert persona.name
