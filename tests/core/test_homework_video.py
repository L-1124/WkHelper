"""Core 域综合测试：作业处理与视频流程。"""

import niquests
import pytest

from wkhelper.core.homework import generic_random_answer, process_question


@pytest.mark.asyncio
async def test_process_question_submits_list_answer(monkeypatch):
    """验证题目处理时提交的答案格式为列表。"""
    captured = {"answer": None}

    def fake_get_answer(library_id, version):
        return ["A"]

    async def fake_submit(problem_id, answer, course_info, client, kwargs):
        captured["answer"] = answer
        return {"success": True, "is_correct": True, "correct_answer": []}

    monkeypatch.setattr("wkhelper.core.homework.db.get_answer", fake_get_answer)

    q = {
        "id": 1,
        "problem_id": 1,
        "content": {"LibraryID": "lib-1", "Version": "v1"},
        "user": {"my_count": 0},
        "max_retry": 3,
    }

    async with niquests.AsyncSession() as client:
        ok, correct = await process_question(1, q, 0, 0, {}, client, fake_submit)

    assert ok is True and correct is True
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
