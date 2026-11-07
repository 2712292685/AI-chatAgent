"""测试公共设施。"""

import shutil
import tempfile
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture
def workdir() -> Iterator[Path]:
    """提供一个可读写的临时目录。

    没有使用 pytest 自带的 ``tmp_path``：它在 Windows 上用
    ``tempfile.mkdtemp`` 建目录，生成的是仅所有者可访问的权限，
    在受限令牌下连创建者自己都读写不了。这里用普通 ``mkdir``，
    权限继承父目录，在普通环境和受限环境里都能用。
    """
    path = Path(tempfile.gettempdir()) / f"chatagent-test-{uuid.uuid4().hex}"
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
