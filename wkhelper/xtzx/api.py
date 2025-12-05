import json
import random
import re
import time

import requests

from ..utils import log
from .models import ClassroomInfo, Course, Homework, Question, SubmitResult, UserInfo


def get_basic_info(session: requests.Session) -> UserInfo:
    response = session.get("https://www.xuetangx.com/api/v1/u/user/basic_profile/")
    resp = json.loads(response.text)
    if not resp["success"]:
        log("❌ 获取用户信息失败！")
        exit(1)

    return resp["data"]


def get_courses(session: requests.Session) -> list[Course]:
    url = "https://www.xuetangx.com/api/v1/lms/user/user-courses/?status=1&page=1"
    response = session.get(url)
    resp = json.loads(response.text)
    if not resp["success"]:
        log("❌ 获取课程列表失败！")
        exit(1)

    try:
        courses: list[Course] = []
        for course in resp["data"]["product_list"]:
            courses.append({
                "name": course["name"],
                "classroom_id": course["classroom_id"],
                "sign": course["sign"],
                "product_id": course["product_id"],
                "sku_id": course["sku_id"],
            })
        return courses
    except Exception:
        log("❌ 获取课程列表失败！")
        exit(1)


def _iter_leaves(chapter_data: list[dict]):
    for chapter in chapter_data:
        if "section_leaf_list" in chapter:
            for section in chapter["section_leaf_list"]:
                yield from section.get("leaf_list", [section])


def get_chapter_data(course: Course, session: requests.Session) -> list[dict]:
    url = f"https://www.xuetangx.com/api/v1/lms/learn/course/chapter?cid={course['classroom_id']}&sign={course['sign']}"
    try:
        response = session.get(url)
        return json.loads(response.text)["data"]["course_chapter"]
    except Exception:
        log("❌ 获取章节信息失败！")
        exit(1)


def get_videos(
    course: Course, session: requests.Session
) -> tuple[dict[int, str], requests.Session]:
    data = get_chapter_data(course, session)
    videos = {
        leaf["id"]: leaf["name"]
        for leaf in _iter_leaves(data)
        if leaf.get("leaf_type") == 0
    }

    log(f"📋 找到 {len(videos)} 个视频")
    return videos, session


def get_homeworks(
    course: Course, session: requests.Session
) -> tuple[list[Homework], requests.Session, ClassroomInfo]:
    """获取课程中的课堂作业（leaf_type == 6）"""
    data = get_chapter_data(course, session)

    homeworks: list[Homework] = [
        {
            "id": leaf["id"],
            "name": leaf["name"],
            "start_time": leaf.get("start_time"),
            "score_deadline": leaf.get("score_deadline"),
            "is_score": leaf.get("is_score"),
            "chapter_id": leaf.get("chapter_id"),
        }
        for leaf in _iter_leaves(data)
        if leaf.get("leaf_type") == 6
    ]

    log(f"📋 找到 {len(homeworks)} 个课堂作业")

    classroom_info: ClassroomInfo = {
        "id": course["classroom_id"],
        "course_id": course["product_id"],  # 猜测
        "course_sign": course["sign"],
        "free_sku_id": course["sku_id"],
    }
    return homeworks, session, classroom_info


def get_leaf_type_id(
    course: Course, leaf_id: int, session: requests.Session
) -> int | None:
    """获取 leaf 信息，提取 leaf_type_id"""
    url = f"https://www.xuetangx.com/api/v1/lms/learn/leaf_info/{course['classroom_id']}/{leaf_id}/?sign={course['sign']}"
    try:
        response = session.get(url)
        data = json.loads(response.text)
        if data.get("success") or data.get("data"):
            return data.get("data", {}).get("content_info", {}).get("leaf_type_id")
        return None
    except Exception as e:
        log(f"❌ 获取 leaf_info 失败！错误: {e}")
        return None


def get_homework_questions(
    homework_id: int, course: Course, session: requests.Session
) -> list[Question]:
    """获取作业题目列表"""
    url = (
        f"https://www.xuetangx.com/api/v1/lms/exercise/get_exercise_list/{homework_id}/"
    )
    try:
        response = session.get(url)
        data = json.loads(response.text)
        if data.get("success", False):
            return data.get("data", {}).get("problems", [])
        return []
    except Exception as e:
        log(f"❌ 获取作业题目失败！错误: {e}")
        return []


def submit_homework_answer(
    leaf_id: int,
    exercise_id: int,
    problem_id: int,
    answer: str | list,
    course_info: ClassroomInfo,
    session: requests.Session,
) -> SubmitResult:
    """提交单个题目答案，返回提交结果详情"""
    url = "https://www.xuetangx.com/api/v1/lms/exercise/problem_apply/"

    # 确保 answer 是列表格式
    if isinstance(answer, str):
        answer = [answer]

    payload = {
        "classroom_id": course_info["id"],
        "problem_id": problem_id,
        "leaf_id": leaf_id,
        "exercise_id": exercise_id,
        "sign": course_info["course_sign"],
        "answer": answer,
    }

    try:
        while True:
            response = session.post(url, json=payload)

            # 检查是否被限流
            match = re.search(r"Expected available in(.+?)second.", response.text)
            if match:
                delay_time = match.group(1).strip()
                log(f"⚠️  服务器限流，需等待 {delay_time} 秒")
                time.sleep(float(delay_time) + 0.5)
                log("🔄 重新提交答案...")
                continue

            data = json.loads(response.text)
            if data.get("success") is True:
                result_data = data.get("data", {})
                # 添加3-4秒随机延迟，模拟人工操作
                time.sleep(random.uniform(3, 4))
                return {
                    "success": True,
                    "is_correct": result_data.get(
                        "is_right", result_data.get("is_correct", False)
                    ),
                    "correct_answer": result_data.get("answer", []),
                }
            return {"success": False, "is_correct": False, "correct_answer": []}
    except Exception as e:
        log(f"❌ 提交答案失败！错误: {e}")
        return {"success": False, "is_correct": False, "correct_answer": []}
