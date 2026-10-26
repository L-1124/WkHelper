"""课程成绩与大纲统计服务。"""

import logging
from typing import Any

from wkhelper.domain.courses import Course, CourseScoreSummary, ExamScoreItem, ModuleEvaluation
from wkhelper.exam.service import get_exams
from wkhelper.platform.base import BasePlatform
from wkhelper.platform.parsing import iter_leaves_with_context

logger = logging.getLogger(__name__)


async def get_course_score_summary(platform: BasePlatform, course: Course) -> CourseScoreSummary | None:
    """提取整门课程考核大纲、各模块完成率与得分看板。"""
    try:
        if platform.platform_code == "ykt":
            return await _get_yuketang_summary(platform, course)
        return await _get_generic_summary(platform, course)
    except Exception as exc:
        logger.debug(f"获取课程成绩大纲失败: {exc}")
        return None


async def _get_yuketang_summary(platform: BasePlatform, course: Course) -> CourseScoreSummary:
    cid = course.metadata.get("classroom_id")
    if not cid:
        return await _get_generic_summary(platform, course)

    try:
        cp = await platform.get_course_progress(course)
    except NotImplementedError:
        return await _get_generic_summary(platform, course)

    module_counts: dict[str, dict[str, int]] = {
        "视频": {"total": 0, "done": 0},
        "作业": {"total": 0, "done": 0},
        "讨论": {"total": 0, "done": 0},
        "资料": {"total": 0, "done": 0},
    }
    sample_nodes: dict[str, int] = {}
    exam_items: list[ExamScoreItem] = []

    def walk(nodes: list[dict[str, Any]]) -> None:
        for n in nodes:
            if not isinstance(n, dict):
                continue
            ltype = n.get("leaf_type")
            prog = n.get("progress")
            name = str(n.get("name") or "")
            lid = n.get("id")

            if ltype == 0:
                module_counts["视频"]["total"] += 1
                if isinstance(prog, (int, float)) and prog >= 1.0:
                    module_counts["视频"]["done"] += 1
                if "视频" not in sample_nodes and lid is not None:
                    sample_nodes["视频"] = int(lid)
            elif ltype == 6:
                module_counts["作业"]["total"] += 1
                if isinstance(prog, (int, float)) and prog >= 1.0:
                    module_counts["作业"]["done"] += 1
                if "作业" not in sample_nodes and lid is not None:
                    sample_nodes["作业"] = int(lid)
            elif ltype in (4, 10):
                module_counts["讨论"]["total"] += 1
                if isinstance(prog, (int, float)) and prog >= 1.0:
                    module_counts["讨论"]["done"] += 1
                if "讨论" not in sample_nodes and lid is not None:
                    sample_nodes["讨论"] = int(lid)
            elif ltype == 3:
                module_counts["资料"]["total"] += 1
                if isinstance(prog, (int, float)) and prog >= 1.0:
                    module_counts["资料"]["done"] += 1
                if "资料" not in sample_nodes and lid is not None:
                    sample_nodes["资料"] = int(lid)
            elif ltype == 5:
                p_info = prog if isinstance(prog, dict) else {}
                exam_items.append(
                    ExamScoreItem(
                        exam_id=str(lid or ""),
                        title=name or f"考试 {lid}",
                        status=str(p_info.get("status") or "未知"),
                        user_score=float(p_info["user_score"]) if isinstance(p_info.get("user_score"), (int, float)) else None,
                        total_score=float(p_info["total_score"]) if isinstance(p_info.get("total_score"), (int, float)) else None,
                        problem_count=int(p_info.get("total") or 0),
                        done_count=int(p_info.get("done") or 0),
                    )
                )
                if "考试" not in sample_nodes and lid is not None:
                    sample_nodes["考试"] = int(lid)

            for k in ("section_leaf_list", "leaf_list", "section_list"):
                children = n.get(k)
                if isinstance(children, list):
                    walk(children)

    walk(cp)

    weights: dict[str, float] = {}
    for mod_name, sample_id in sample_nodes.items():
        try:
            data = await platform.get_leaf_info(sample_id, course)
            ci = data.get("content_info", {}) if isinstance(data, dict) else {}
            se = ci.get("score_evaluation") if isinstance(ci, dict) else None
            if isinstance(se, dict):
                prop = se.get("score_proportion", {}).get("proportion")
                if isinstance(prop, (int, float)):
                    weights[mod_name] = float(prop)
        except Exception:
            pass

    if not weights:
        weights = {"视频": 0.3, "作业": 0.2, "讨论": 0.1, "考试": 0.4}

    modules: list[ModuleEvaluation] = []
    total_score = 0.0
    has_score_calc = False

    for mod_name in ("视频", "作业", "讨论", "资料"):
        counts = module_counts[mod_name]
        total = counts["total"]
        done = counts["done"]
        if total == 0:
            continue
        rate = round(done / total, 4) if total > 0 else 0.0
        w = weights.get(mod_name)
        mod_score = round(rate * w * 100, 2) if w is not None else None
        if mod_score is not None:
            total_score += mod_score
            has_score_calc = True
        modules.append(
            ModuleEvaluation(
                name=mod_name,
                weight=w,
                total_count=total,
                completed_count=done,
                rate=rate,
                score=mod_score,
            )
        )

    if exam_items:
        w_exam = weights.get("考试", 0.4)
        sum_user = sum(item.user_score for item in exam_items if item.user_score is not None)
        sum_total = sum(item.total_score for item in exam_items if item.total_score is not None)
        exam_rate = round(sum_user / sum_total, 4) if sum_total > 0 else 0.0
        exam_mod_score = round(exam_rate * w_exam * 100, 2) if w_exam is not None and sum_total > 0 else None
        if exam_mod_score is not None:
            total_score += exam_mod_score
            has_score_calc = True
        modules.append(
            ModuleEvaluation(
                name="考试",
                weight=w_exam,
                total_count=len(exam_items),
                completed_count=sum(1 for item in exam_items if item.status == "已完成"),
                rate=exam_rate,
                score=exam_mod_score,
            )
        )

    predicted = round(total_score, 1) if has_score_calc else None
    return CourseScoreSummary(
        modules=modules,
        exams=exam_items,
        total_predicted_score=predicted,
    )


async def _get_generic_summary(platform: BasePlatform, course: Course) -> CourseScoreSummary:
    """通用平台（如学堂在线）统计各模块完成情况。"""
    data = await platform.get_chapter_data(course)
    schedules = await platform.get_leaf_schedules(course)
    exams_raw = await get_exams(platform, course, chapter_data=data, schedules=schedules)

    counts: dict[str, dict[str, int]] = {
        "视频": {"total": 0, "done": 0},
        "作业": {"total": 0, "done": 0},
        "讨论": {"total": 0, "done": 0},
        "资料": {"total": 0, "done": 0},
    }
    for leaf, _, _ in iter_leaves_with_context(data):
        ltype = leaf.get("leaf_type")
        lid = leaf.get("id")
        if lid is None:
            continue
        done = schedules.get(int(lid), 0.0) >= 1.0
        if ltype == 0:
            counts["视频"]["total"] += 1
            if done:
                counts["视频"]["done"] += 1
        elif ltype == 6:
            counts["作业"]["total"] += 1
            if done:
                counts["作业"]["done"] += 1
        elif ltype in (4, 10):
            counts["讨论"]["total"] += 1
            if done:
                counts["讨论"]["done"] += 1
        elif ltype in (3, 7):
            counts["资料"]["total"] += 1
            if done:
                counts["资料"]["done"] += 1

    modules: list[ModuleEvaluation] = []
    weights = {"视频": 0.4, "作业": 0.2, "讨论": 0.1, "考试": 0.3}
    total_score = 0.0
    has_score_calc = False

    for mod_name in ("视频", "作业", "讨论", "资料"):
        c = counts[mod_name]
        total = c["total"]
        done = c["done"]
        if total == 0:
            continue
        rate = round(done / total, 4) if total > 0 else 0.0
        w = weights.get(mod_name)
        score = round(rate * w * 100, 2) if w is not None else None
        if score is not None:
            total_score += score
            has_score_calc = True
        modules.append(
            ModuleEvaluation(
                name=mod_name,
                weight=w,
                total_count=total,
                completed_count=done,
                rate=rate,
                score=score,
            )
        )

    exam_items: list[ExamScoreItem] = [
        ExamScoreItem(
            exam_id=ex.id,
            title=ex.name,
            status="已完成" if ex.completed else ("已缺考" if ex.overdue else "未开始"),
        )
        for ex in exams_raw
    ]

    predicted = round(total_score, 1) if has_score_calc else None
    return CourseScoreSummary(
        modules=modules,
        exams=exam_items,
        total_predicted_score=predicted,
    )
