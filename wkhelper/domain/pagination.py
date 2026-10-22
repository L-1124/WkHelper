"""分页数据。"""

from wkhelper.domain.model import Model


class PaginationMeta(Model):
    """列表分页元数据。"""

    limit: int
    offset: int
    total: int
    has_more: bool
    next_cursor: str | None
    pagination_mode: str = "local"
