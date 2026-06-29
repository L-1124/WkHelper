import logging
from typing import Any

from wkhelper.core.db import db
from wkhelper.solver.base import Answer, BaseSolver
from wkhelper.ui.interface import UserInterface

logger = logging.getLogger(__name__)


class LocalDbSolver(BaseSolver):
    """本地题库解析器。

    快速查询本地 SQLite 题库，将已有的答案返回。
    """

    async def batch_solve(self, questions: list[dict[str, Any]], ui: UserInterface) -> list[tuple[dict[str, Any], Answer]]:
        results = []
        for q in questions:
            library_id = None
            version = None
            if "content" in q:
                library_id = q["content"].get("LibraryID") or q["content"].get("library_id")
                version = q["content"].get("Version")

            if not library_id or not version:
                continue

            answer_list = db.get_answer(str(library_id), version)
            if answer_list:
                ans = Answer(needs_course_context=False, selected_options=answer_list)
                results.append((q, ans))

        return results
