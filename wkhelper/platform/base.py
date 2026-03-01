"""平台基础接口。"""

from abc import ABC, abstractmethod
from typing import Any

import httpx

from wkhelper.core.models import Course, Homework, UserInfo
from wkhelper.ui.interface import UserInterface


class BasePlatform(ABC):
    """所有平台的抽象基类。"""

    def __init__(self, client: httpx.AsyncClient, ui: UserInterface):
        self.client = client
        self.ui = ui
        self.user: UserInfo | None = None

    @abstractmethod
    async def login(self) -> UserInfo:
        """执行登录并返回用户信息。"""
        ...

    @abstractmethod
    async def get_courses(self) -> list[Course]:
        """获取课程列表。"""
        ...

    @abstractmethod
    async def get_videos(self, course: Course) -> dict[int, str]:
        """获取课程视频。返回 {id: name}。"""
        ...

    @abstractmethod
    async def get_homeworks(self, course: Course) -> list[Homework]:
        """获取课程作业。"""
        ...

    @abstractmethod
    async def get_leaf_questions(self, leaf_id: str | int, course: Course) -> list[dict[str, Any]]:
        """获取特定叶子节点（作业/视频问题）的题目。"""
        ...

    @abstractmethod
    async def do_video(self, video_id: str, video_name: str, course: Course) -> None:
        """观看单个视频。"""
        ...

    @abstractmethod
    async def do_homework(self, homework: Homework, course: Course, is_random: bool = False) -> None:
        """完成单个作业。"""
        ...
