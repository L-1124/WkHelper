"""测试全局配置与 Fixtures。"""

from typing import Any, override

import niquests
import pytest
import pytest_asyncio

from wkhelper.core.models import Course, Homework, UserInfo
from wkhelper.platform.base import BasePlatform


class FakeUI:
    """通用的 UI 假实现。"""

    async def select_one(self, message: str, choices: list[str]) -> str | None:
        return "退出"

    async def select_many(
        self,
        message: str,
        choices: list[str],
        default_selected: set[str] | None = None,
        disabled_choices: set[str] | None = None,
    ) -> list[str] | None:
        return []

    async def confirm(self, message: str, default: bool = True) -> bool:
        return default

    def show_table(self, title: str, columns: list[str], rows: list[list[str]]) -> None:
        pass

    def update_video_progress(self, video_name: str, progress: float) -> None:
        pass

    def finish_video_progress(self, video_name: str) -> None:
        pass

    def update_video_status(self, video_name: str, status: str | None) -> None:
        pass

    def update_homework_progress(self, homework_name: str, done: int, total: int) -> None:
        pass

    def finish_homework_progress(self, homework_name: str) -> None:
        pass

    def update_homework_status(self, homework_name: str, status: str | None) -> None:
        pass

    def print_message(self, message: str) -> None:
        pass

    def stop_live_displays(self) -> None:
        pass

    def start_live_displays(self) -> None:
        pass

    def track_progress(self, title: str, total: int):
        class FakeTracker:
            def update(self, advance=1):
                pass

            def stop(self):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

        return FakeTracker()


class FakeAsyncPlatform(BasePlatform):
    """通用的异步平台假实现。"""

    def __init__(self, client: niquests.AsyncSession, ui: Any):
        super().__init__(client, ui)

    @override
    async def login(self, cookies: dict[str, str] | None = None) -> UserInfo:
        self.user = UserInfo(id=1, name="tester")
        return self.user

    @override
    async def get_courses(self) -> list[Course]:
        return [Course(id=1, name="课程1", platform_id="test")]

    @override
    async def _get_chapter_data(self, course: Course) -> list[dict[str, Any]]:
        return []

    @override
    async def _get_leaf_schedules(self, course: Course) -> dict[int, float]:
        return {}

    @override
    def _build_leaf_info_url(self, leaf_id: str | int, course: Course) -> str:
        return ""

    @override
    def _build_exercise_list_url(self, leaf_type_id: int | str) -> str:
        return ""

    @override
    def _build_submit_url(self) -> str:
        return ""

    @override
    def _build_submit_payload(self, problem_id: int, answer: list[str]) -> dict[str, Any]:
        return {}

    @override
    async def _prepare_video_context(self, video_id: str, course: Course) -> Any:
        from wkhelper.core.models import VideoContext

        return VideoContext(
            video_id=video_id,
            classroom_id="0",
            user_id=0,
            course_id=0,
            sku_id=0,
            progress_url="",
            heartbeat_url="",
            progress_params={},
        )

    @override
    async def _prepare_submit_context(self, homework: Homework, course: Course) -> None:
        self._submit_ctx = {}

    @override
    async def _login_with_cookies(self, cookies: dict[str, str]) -> UserInfo:
        return UserInfo(id=1, name="test")

    @override
    async def _login_with_qrcode(self) -> UserInfo:
        return UserInfo(id=1, name="test")


@pytest.fixture
def fake_ui():
    return FakeUI()


@pytest_asyncio.fixture
async def fake_platform(fake_ui):
    async with niquests.AsyncSession() as client:
        yield FakeAsyncPlatform(client, fake_ui)
