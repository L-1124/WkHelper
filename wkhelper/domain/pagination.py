"""分页数据。"""

from dataclasses import dataclass

from wkhelper.domain.json_types import JsonDict


@dataclass
class PaginationMeta:
    """列表分页元数据。"""

    limit: int
    offset: int
    total: int
    has_more: bool
    next_cursor: str | None
    pagination_mode: str = "local"

    def to_dict(self) -> JsonDict:
        return {
            "limit": self.limit,
            "offset": self.offset,
            "total": self.total,
            "has_more": self.has_more,
            "next_cursor": self.next_cursor,
            "pagination_mode": self.pagination_mode,
        }
