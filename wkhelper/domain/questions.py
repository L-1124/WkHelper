"""题型与答案归一规则。"""

from typing import Any

type QuestionPayload = dict[str, Any]


_FILL_IN_TYPE = 4


_JUDGEMENT_TYPE = 6


_TRUE_ANSWERS = {"A", "T", "TRUE", "1", "对", "正确", "√"}


_FALSE_ANSWERS = {"B", "F", "FALSE", "0", "错", "错误", "×"}


def normalize_judgement_answer(question: QuestionPayload | None, answer: list[str]) -> list[str]:
    """把判断题答案归一为平台约定的 `true` / `false`。"""
    if question_type(question) != _JUDGEMENT_TYPE:
        return answer
    normalized: list[str] = []
    for item in answer:
        token = item.strip().upper()
        if token in _TRUE_ANSWERS:
            normalized.append("true")
        elif token in _FALSE_ANSWERS:
            normalized.append("false")
        else:
            normalized.append(item)
    return normalized


def question_type(question: QuestionPayload | None) -> int | None:
    """题目题型：优先取 `content.ProblemType`（两平台实际字段），退回 `type`。

    题型取值：1/6 单选判断、2 多选、3 投票、4 填空、5 主观题。
    """
    if not isinstance(question, dict):
        return None
    content = question.get("content")
    raw: Any = None
    if isinstance(content, dict):
        raw = content.get("ProblemType")
        if raw is None:
            raw = content.get("Type")
    if raw is None:
        raw = question.get("type")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def is_fill_in_question(question: QuestionPayload | None) -> bool:
    """是否为填空题型：必须走 `answers` 映射而不是 `answer` 列表。"""
    return question_type(question) == _FILL_IN_TYPE
