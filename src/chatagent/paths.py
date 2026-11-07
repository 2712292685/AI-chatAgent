"""数据文件查找顺序：显式路径 > 当前工作目录 > 包内默认。

包内默认随 wheel 一起安装，因此 `uv tool install` 后在任意目录都能启动；
开发者把同名文件放在工作目录里即可临时改人设或参数，不必动安装目录。
"""

from __future__ import annotations

from pathlib import Path

PACKAGE_DATA_DIR = Path(__file__).with_name("data")


def resolve(name: str, explicit: Path | None = None) -> Path:
    """返回要读取的数据文件路径。

    ``explicit`` 非空时直接使用（找不到就按原样报错，便于测试与调试）；
    否则当前工作目录存在同名文件就用它，最后回落到包内默认文件。
    """
    if explicit is not None:
        return explicit
    local = Path(name)
    if local.is_file():
        return local
    return PACKAGE_DATA_DIR / name


def packaged(name: str) -> Path:
    """包内默认文件路径，供测试与文档引用。"""
    return PACKAGE_DATA_DIR / name
