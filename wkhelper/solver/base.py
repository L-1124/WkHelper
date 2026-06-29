import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from wkhelper.ui.interface import UserInterface

logger = logging.getLogger(__name__)

_OPTION_LETTER_RE = re.compile(r"^[A-Z]$")


@dataclass
class Answer:
    """题目的答案模型。"""

    needs_course_context: bool
    selected_options: list[str] | None


class BaseSolver(ABC):
    """解析器基类。

    解析器负责接收一批题目，并尝试给出答案。
    可以是本地题库解析器，也可以是 AI 解析器。
    """

    @abstractmethod
    async def batch_solve(self, questions: list[dict[str, Any]], ui: UserInterface) -> list[tuple[dict[str, Any], Answer]]:
        """批量解析题目。

        Args:
            questions: 需要解析的题目列表。
            ui: 用于可能的交互。

        Returns:
            list[tuple[dict, Answer]]: 成功解析的题目及其答案对。
        """
        pass
