"""资料题 (article) 与讨论题 (discussion) 单元与流程测试。"""

import pytest

from wkhelper.article.service import get_articles, read_article
from wkhelper.discussion.service import get_discussion_detail, get_discussions, submit_discussion
from wkhelper.domain.courses import Course
from wkhelper.infra.http import AsyncSession
from wkhelper.platform.base import BasePlatform
from wkhelper.platform.protocol_types import HeartbeatProfile


def _sample_course() -> Course:
    return Course(
        id="1001",
        name="积极心理学",
        platform_id="ykt",
        metadata={"classroom_id": 2001, "university_id": 2930},
    )


class MockPlatform(BasePlatform):
    @property
    def heartbeat_profile(self) -> HeartbeatProfile:
        return HeartbeatProfile(lob="test")

    def _build_submit_url(self) -> str:
        return ""

    def _build_submit_payload(self, problem_id: int, answer: list[str], submit_ctx=None, question=None):
        return {}

    async def prepare_submit_context(self, homework, course):
        return {}

    async def prepare_video_context(self, video_id: str, course):
        raise NotImplementedError

    async def login_with_cookies(self, cookies):
        raise NotImplementedError

    async def qr_exchange_cookies(self, login_data):

        return {}

    async def get_courses(self):
        return [_sample_course()]

    async def get_chapter_data(self, course):
        return [
            {
                "name": "第一章",
                "section_leaf_list": [
                    {
                        "id": 101,
                        "name": "课程导学",
                        "leaf_type": 3,
                        "is_show": True,
                    },
                    {
                        "id": 102,
                        "name": "三件好事讨论",
                        "leaf_type": 4,
                        "is_show": True,
                    },
                ],
            }
        ]

    async def get_leaf_schedules(self, course):
        return {101: 1.0, 102: 0.0}

    def _build_leaf_info_url(self, leaf_id, course):
        return f"https://api.test/leaf_info/{leaf_id}"

    def _get_leaf_info_params(self, leaf_id, course):
        return {}

    def _build_exercise_list_url(self, leaf_type_id, sku_id=None):
        return ""

    async def mark_article_finish(self, leaf_id: str | int, course: Course) -> bool:
        return True

    async def fetch_discussion_unit(self, leaf_id: str | int, course: Course):
        return {"id": 999, "user_comment_num": 0, "commented": 10, "content": {"text": "写下三件好事..."}}

    async def post_discussion_comment(self, topic_info, content, course, leaf_id):
        return {"success": True, "data": {"data": {"id": 8888}}, "msg": "操作成功"}


@pytest.mark.asyncio
async def test_get_articles_and_read_article():
    """验证资料/图文节点列表提取与标为已读。"""
    async with AsyncSession() as client:
        plat = MockPlatform(client)
        course = _sample_course()

        articles = await get_articles(plat, course)
        assert len(articles) == 1
        assert articles[0].id == "101"
        assert articles[0].completed is True

        res = await read_article(plat, "101", course)
        assert res.success is True
        assert res.leaf_id == "101"
        assert res.completed is True


@pytest.mark.asyncio
async def test_get_discussions_and_submit():
    """验证讨论题列表提取、题目详情与回复打卡。"""
    async with AsyncSession() as client:
        plat = MockPlatform(client)
        course = _sample_course()

        discussions = await get_discussions(plat, course)
        assert len(discussions) == 1
        assert discussions[0].id == "102"
        assert discussions[0].completed is False

        detail = await get_discussion_detail(plat, "102", course)
        assert detail.topic_id == 999
        assert detail.comment_count == 10
        assert "三件好事" in detail.prompt

        res = await submit_discussion(plat, "102", course, "今天心情很好")
        assert res.success is True
        assert res.comment_id == 8888
