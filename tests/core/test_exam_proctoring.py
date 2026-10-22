"""监考总开关与从属配置的门禁回归。"""

import pytest

from wkhelper.domain.exam import ExamCoverDetail
from wkhelper.exam.proctoring import ProctoringRestrictionError, ensure_exam_operation_allowed


def _cover() -> ExamCoverDetail:
    return ExamCoverDetail(
        course_id="5812766",
        exam_id="84705331",
        title="期末考试",
        problem_count=28,
        total_score=51,
        limit_minutes=0,
        start_time=None,
        deadline=None,
        status="未开始",
        description="",
        face_auth=False,
        capture_screen=False,
        app_capture_screen=True,
        front_random_snap=True,
    )


def test_app_subsettings_do_not_enable_proctoring() -> None:
    """App 抓拍与录屏子配置不能独立锁定普通考试。"""
    ensure_exam_operation_allowed(_cover())


def _with_flag(flag: str) -> ExamCoverDetail:
    c = _cover()
    setattr(c, flag, True)
    return c


@pytest.mark.parametrize("flag", ["online_proctor", "face_auth", "identity_auth", "capture_screen", "is_offline"])
def test_active_proctoring_still_blocks_automation(flag: str) -> None:
    """修复 App 误判不能解除真正启用的监考门禁。"""
    with pytest.raises(ProctoringRestrictionError) as error:
        ensure_exam_operation_allowed(_with_flag(flag))
    assert error.value.exam_id == "84705331"
    assert error.value.reasons
