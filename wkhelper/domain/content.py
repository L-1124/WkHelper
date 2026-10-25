"""课程内容数据。"""

from wkhelper.domain.model import Model
from wkhelper.domain.time_types import EpochMillis


class ArticleItem(Model):
    id: str
    name: str
    completed: bool


class DiscussionItem(Model):
    id: str
    name: str
    completed: bool


class NoticeItem(Model):
    """课程公告/通知节点。"""

    id: str
    name: str
    completed: bool
    published_at: EpochMillis | None = None


class NoticeListData(Model):
    notices: list[NoticeItem]


class NoticeDetail(Model):
    """公告正文与发布信息。"""

    course_id: str
    leaf_id: str
    notice_id: int | None
    title: str
    content: str
    published_at: EpochMillis | None = None
    publisher: str = ""


class ArticleListData(Model):
    articles: list[ArticleItem]


class ArticleReadResult(Model):
    course_id: str
    leaf_id: str
    name: str
    success: bool
    completed: bool


class DiscussionListData(Model):
    discussions: list[DiscussionItem]


class DiscussionTopicDetail(Model):
    course_id: str
    leaf_id: str
    topic_id: int
    title: str
    prompt: str
    comment_count: int
    user_comment_num: int
    completed: bool


class DiscussionCommentResult(Model):
    course_id: str
    leaf_id: str
    topic_id: int
    comment_id: int | None
    success: bool
    message: str


class DiscussionDeleteResult(Model):
    course_id: str
    comment_id: str
    success: bool
    message: str
