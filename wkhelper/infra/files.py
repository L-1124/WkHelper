"""文件与文件系统通用基础设施。"""

import os
from pathlib import Path

from wkhelper.domain.errors import OutputExistsError

__all__ = ["publish_file_no_clobber"]


def publish_file_no_clobber(temp_file_path: str, target_path: Path) -> None:
    """原子发布临时文件到目标路径，绝不覆盖已有文件。

    使用 os.link 实现原子 no-clobber 写入：
    - 若目标路径已存在（或发布时竞态新建），os.link 抛出 FileExistsError，捕获并转换为 OutputExistsError。
    - 若系统或文件系统不支持硬链接等其他错误，真实抛出，严禁降级为会覆盖现有文件的 copy/replace。
    """
    try:
        os.link(temp_file_path, target_path)
    except FileExistsError as exc:
        raise OutputExistsError(f"输出文件已存在，拒绝覆盖: {target_path}") from exc
