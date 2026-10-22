"""课程内容数据。"""

from dataclasses import dataclass

from wkhelper.domain.time_types import EpochMillis


@dataclass
class ArticleItem:
    id: str
    name: str
    completed: bool


@dataclass
class DiscussionItem:
    id: str
    name: str
    completed: bool


@dataclass
class NoticeItem:
    """课程公告/通知节点。"""

    id: str
    name: str
    completed: bool
    published_at: EpochMillis | None = None


@dataclass
class NoticeListData:
    notices: list[NoticeItem]


@dataclass
class NoticeDetail:
    """公告正文与发布信息。"""

    course_id: str
    leaf_id: str
    notice_id: int | None
    title: str
    content: str
    published_at: EpochMillis | None = None
    publisher: str = ""


@dataclass
class ArticleListData:
    articles: list[ArticleItem]


@dataclass
class ArticleReadResult:
    course_id: str
    leaf_id: str
    name: str
    success: bool
    completed: bool


@dataclass
class DiscussionListData:
    discussions: list[DiscussionItem]


@dataclass
class DiscussionTopicDetail:
    course_id: str
    leaf_id: str
    topic_id: int
    title: str
    prompt: str
    comment_count: int
    user_comment_num: int
    completed: bool


@dataclass
class DiscussionCommentResult:
    course_id: str
    leaf_id: str
    topic_id: int
    comment_id: int | None
    success: bool
    message: str
