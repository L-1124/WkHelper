"""考场强监考规则评估与刚性安全门禁。

凡包含在线双机位、人脸核验、网页截屏、线下机房或严格切屏限制的考试，
为保护学生学籍与诚信安全，CLI 全面禁止自动化入场与作答，仅保留只读查询。
"""

from wkhelper.core.exceptions import APIError
from wkhelper.core.results import ExamCoverDetail


class ProctoringRestrictionError(APIError):
    """命中了严格监考保护规则的安全门禁异常。"""

    def __init__(self, exam_id: str | int, reasons: list[str]) -> None:
        self.exam_id = str(exam_id)
        self.reasons = reasons
        reasons_text = "、".join(reasons)
        message = (
            f"考试 {exam_id} 已开启强监考保护（{reasons_text}）。"
            "为了你的学籍与诚信安全，WkHelper 已全局锁定该考试的自动化功能。"
            "请务必在官方授权客户端或浏览器中由本人独立完成考试。"
        )
        super().__init__(message, retryable=False, status_code=403)


def assess_proctoring_risk(cover: ExamCoverDetail) -> tuple[bool, list[str]]:
    """评估考试监考风险，返回 (is_restricted, restriction_reasons)。"""
    reasons: list[str] = []
    if cover.online_proctor:
        reasons.append("在线双机位视频监考")
    if cover.face_auth or cover.identity_auth:
        reasons.append("人脸识别与实名核验")
    if cover.capture_screen:
        reasons.append("屏幕录制与截屏监控")
    if cover.is_offline:
        reasons.append("指定线下机房考场考试")
    if cover.screen_cuts and 0 < cover.max_screen_cuts <= 3:
        reasons.append(f"严格切屏限制（限切屏 {cover.max_screen_cuts} 次）")
    # app_capture_screen / app_front_random_snap 是 App 端从属配置，前端仅在 online_proctor 生效时启用，不能独立触发锁定。
    return bool(reasons), reasons


def ensure_exam_operation_allowed(cover: ExamCoverDetail) -> None:
    """门禁校验：若为强监考考试，严禁执行任何自动化入场或作答。"""
    is_restricted, reasons = assess_proctoring_risk(cover)
    if is_restricted:
        raise ProctoringRestrictionError(cover.exam_id, reasons)
