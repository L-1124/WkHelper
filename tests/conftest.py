"""测试全局配置与 Fixtures。"""

import asyncio
from collections.abc import Callable
from typing import Any, override

import pytest_asyncio

from wkhelper.domain.accounts import UserInfo
from wkhelper.domain.courses import Course
from wkhelper.domain.homework import Homework
from wkhelper.domain.video import VideoContext
from wkhelper.infra.http import AsyncSession
from wkhelper.main import _loop_factory
from wkhelper.platform.base import BasePlatform
from wkhelper.platform.protocol_types import HeartbeatProfile


def pytest_asyncio_loop_factories() -> dict[str, Callable[[], asyncio.AbstractEventLoop]]:
    """测试与 CLI 共用循环工厂，避免 Windows 下 curl_cffi 的线程兼容路径。"""
    return {"default": _loop_factory}


class FakeAsyncPlatform(BasePlatform):
    """通用的异步平台假实现。"""

    def __init__(self, client: AsyncSession, **kwargs: Any) -> None:
        super().__init__(client, **kwargs)

    @property
    @override
    def heartbeat_profile(self) -> HeartbeatProfile:
        return HeartbeatProfile(lob="fake")

    @override
    async def get_courses(self) -> list[Course]:
        return [Course(id="1", name="测试课程1", platform_id="test")]

    @override
    async def get_chapter_data(self, course: Course) -> list[dict[str, Any]]:
        return []

    @override
    async def get_leaf_schedules(self, course: Course) -> dict[int, float]:
        return {}

    @override
    def _build_leaf_info_url(self, leaf_id: str | int, course: Course) -> str:
        return ""

    @override
    def _build_exercise_list_url(self, leaf_type_id: int | str, sku_id: Any = None) -> str:
        return ""

    @override
    def _build_submit_url(self) -> str:
        return ""

    @override
    def _build_submit_payload(
        self, problem_id: int, answer: list[str], submit_ctx: dict[str, Any] | None = None, question: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return {"problem_id": problem_id, "answer": answer}

    @override
    async def prepare_video_context(self, video_id: str, course: Course) -> VideoContext:
        return VideoContext(
            video_id=video_id,
            classroom_id="0",
            user_id=0,
            course_id=0,
            sku_id=0,
            progress_url="http://fake.local/progress",
            heartbeat_url="http://fake.local/heartbeat",
            progress_params={},
        )

    @override
    async def prepare_submit_context(self, homework: Homework, course: Course) -> dict[str, Any]:
        return {"homework_id": homework.id}

    @override
    async def login_with_cookies(self, cookies: dict[str, str]) -> UserInfo:
        self.user = UserInfo(id="1", name="tester")
        return self.user


@pytest_asyncio.fixture
async def fake_platform():
    async with AsyncSession() as client:
        yield FakeAsyncPlatform(client)
