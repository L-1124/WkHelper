"""课程成绩大纲统计测试。"""

from typing import Any

import pytest

from tests.conftest import FakeAsyncPlatform
from wkhelper.course.service import get_course_score_summary
from wkhelper.domain.courses import Course
from wkhelper.infra.http import AsyncSession


class _YktScorePlatform(FakeAsyncPlatform):
    def __init__(self, client: AsyncSession) -> None:
        super().__init__(client)
        self.platform_code = "ykt"

    async def get_course_progress(self, course: Course) -> list[dict[str, Any]]:
        return [
            {
                "name": "第一章",
                "section_list": [
                    {
                        "name": "第一节",
                        "leaf_list": [
                            {"id": 101, "name": "视频1", "leaf_type": 0, "progress": 1.0},
                            {"id": 102, "name": "视频2", "leaf_type": 0, "progress": 1.0},
                            {"id": 201, "name": "作业1", "leaf_type": 6, "progress": 1.0},
                            {
                                "id": 501,
                                "name": "期末考",
                                "leaf_type": 5,
                                "progress": {
                                    "status": "已完成",
                                    "user_score": 90,
                                    "total_score": 100,
                                    "total": 10,
                                    "done": 10,
                                },
                            },
                        ],
                    }
                ],
            }
        ]

    async def get_leaf_info(self, leaf_id: str | int, course: Course) -> dict[str, Any]:
        proportions = {
            101: 0.4,
            201: 0.2,
            501: 0.4,
        }
        prop = proportions.get(int(leaf_id))
        if prop is not None:
            return {
                "content_info": {
                    "score_evaluation": {
                        "score_proportion": {"proportion": prop},
                    }
                }
            }
        return {}


@pytest.mark.asyncio
async def test_yuketang_score_summary_preserves_weights_and_exam_scores() -> None:
    """节点权重与考试原始分共同决定课程预估分。"""
    async with AsyncSession() as client:
        platform = _YktScorePlatform(client)
        course = Course(
            id="1001",
            name="测试课程",
            platform_id="ykt",
            metadata={"classroom_id": 2001},
        )
        summary = await get_course_score_summary(platform, course)

    assert summary is not None
    assert summary.total_predicted_score == 96.0
    mod_map = {m.name: m for m in summary.modules}
    assert mod_map["视频"].rate == 1.0
    assert mod_map["视频"].weight == 0.4
    assert mod_map["视频"].score == 40.0
    assert mod_map["作业"].rate == 1.0
    assert mod_map["作业"].weight == 0.2
    assert mod_map["作业"].score == 20.0
    assert mod_map["考试"].rate == 0.9
    assert mod_map["考试"].weight == 0.4
    assert mod_map["考试"].score == 36.0
    assert summary.exams[0].exam_id == "501"
