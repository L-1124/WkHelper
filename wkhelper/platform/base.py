"""平台基础接口。

为保持向后兼容，BasePlatform 与 BasePlatformParser 实现位于 `wkhelper.core.base_platform`。
"""

from wkhelper.core.base_platform import (
    BasePlatform,
    BasePlatformParser,
    LeafContext,
)
from wkhelper.platform.tree_utils import (
    format_leaf_label,
    iter_leaves_with_context,
)

__all__ = [
    "BasePlatform",
    "BasePlatformParser",
    "LeafContext",
    "format_leaf_label",
    "iter_leaves_with_context",
]
