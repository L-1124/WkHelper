"""Core 域综合测试：作业处理与视频流程。"""

import niquests
import pytest

from wkhelper.core.homework import generic_random_answer, generic_submit_homework


@pytest.mark.asyncio
async def test_generic_submit_homework_submits_list_answer():
    """验证题目处理时提交的答案格式为列表。"""
    captured = {"answer": None}

    async def fake_submit(problem_id, answer, course_info, client, kwargs):
        captured["answer"] = answer
        return {"success": True, "is_correct": True, "correct_answer": []}

    q = {
        "id": 1,
        "problem_id": 1,
        "content": {"LibraryID": "lib-1", "Version": "v1"},
        "user": {"my_count": 0},
        "max_retry": 3,
    }

    resolved_pairs = [(q, ["A"])]

    async with niquests.AsyncSession() as client:
        await generic_submit_homework(resolved_pairs, fake_submit, {}, client)

    assert captured["answer"] == ["A"]


@pytest.mark.asyncio
async def test_random_answer_persists_correct_result(monkeypatch):
    """验证随机模式下若服务器返回了正确答案，则持久化到数据库。"""
    saved = []

    def fake_save_answer(lib_id, version, answer):
        saved.append((lib_id, version, answer))

    monkeypatch.setattr("wkhelper.core.homework.db.save_answer", fake_save_answer)

    async def fake_submit(problem_id, answer, course_info, client, kwargs):
        return {"success": True, "is_correct": False, "correct_answer": ["B"]}

    questions = [
        {
            "id": 1,
            "problem_id": 1,
            "content": {
                "LibraryID": "lib-1",
                "Version": "v1",
                "Options": [{"key": "A"}, {"key": "B"}],
            },
            "user": {"is_right": False, "my_count": 0},
            "max_retry": 3,
        }
    ]

    async with niquests.AsyncSession() as client:
        await generic_random_answer(questions, fake_submit, None, client)

    assert saved == [("lib-1", "v1", ["B"])]
