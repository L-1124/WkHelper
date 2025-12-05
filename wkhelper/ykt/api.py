import json
import random
import re
import time

import requests

from ..utils import log
from .models import ClassroomInfo, Course, Homework, Question, SubmitResult, UserInfo


def _get_course_kwargs(course: Course) -> dict:
    """生成课程相关的请求参数 (headers 和 cookies)"""
    cookies = {
        "xtbz": "ykt",
        "platform_type": "1",
        "uv_id": str(course["university_id"]),
        "university_id": str(course["university_id"]),
        "platform_id": "3",
        "classroom_id": str(course["classroom_id"]),
        "classroomID": str(course["classroom_id"]),
    }
    headers = {
        "classroom-id": str(course["classroom_id"]),
        "Xtbz": "ykt",
    }
    return {"headers": headers, "cookies": cookies}


def get_basic_info(session: requests.Session) -> UserInfo:
    response = session.get("https://www.yuketang.cn/api/v3/user/basic-info")
    resp = json.loads(response.text)
    if resp["code"] != 0:
        log("❌ 获取用户信息失败！")
        exit(1)

    return resp["data"]


def get_courses(session: requests.Session) -> list[Course]:
    url = "https://www.yuketang.cn/v2/api/web/courses/list?identity=2"
    response = session.get(url)
    resp = json.loads(response.text)
    if resp["errcode"] != 0:
        log("❌ 获取课程列表失败！")
        exit(1)

    try:
        courses: list[Course] = []
        for course in resp["data"]["list"]:
            courses.append({
                "name": course["course"]["name"],
                "classroom_id": course["classroom_id"],
                "university_id": course["course"]["university_id"],
                "id": course["course"]["id"],
            })
        return courses
    except Exception as e:
        log(f"❌ 获取课程列表失败！错误: {e}")
        exit(1)


def get_classroom_info(
    course: Course, session: requests.Session
) -> tuple[ClassroomInfo, dict]:
    url = (
        f"https://www.yuketang.cn/v2/api/web/classrooms/{course['classroom_id']}?role=5"
    )
    kwargs = _get_course_kwargs(course)
    response = session.get(url, **kwargs)
    data = json.loads(response.text)
    if data["errcode"] != 0:
        log("❌ 获取课程信息失败！")
        exit(1)
    return data["data"], kwargs


def get_chapter_info(
    course: Course, session: requests.Session
) -> tuple[list[dict], dict, ClassroomInfo]:
    """获取课程章节信息"""
    course_info, kwargs = get_classroom_info(course, session)
    url = f"https://www.yuketang.cn/mooc-api/v1/lms/learn/course/chapter?cid={course['classroom_id']}&sign={course_info['course_sign']}&term=latest&uv_id={course['university_id']}&classroom_id={course['classroom_id']}"
    try:
        response = session.get(url, **kwargs)
        data = json.loads(response.text)["data"]["course_chapter"]
        return data, kwargs, course_info
    except Exception as e:
        log(f"❌ 获取章节信息失败！错误: {e}")
        exit(1)


def _iter_leaves(chapter_data: list[dict]):
    for chapter in chapter_data:
        if "section_leaf_list" in chapter:
            for section in chapter["section_leaf_list"]:
                yield from section.get("leaf_list", [section])


def get_videos(
    course: Course, session: requests.Session
) -> tuple[dict[int, str], dict, ClassroomInfo]:
    """获取课程视频（leaf_type == 0）"""
    chapter_data, kwargs, course_info = get_chapter_info(course, session)

    videos = {
        leaf["id"]: leaf["name"]
        for leaf in _iter_leaves(chapter_data)
        if leaf.get("leaf_type") == 0
    }

    log(f"📋 找到 {len(videos)} 个视频")
    return videos, kwargs, course_info


def get_texts(
    course: Course, session: requests.Session
) -> tuple[dict[int, str], dict, ClassroomInfo]:
    """获取课程图文（leaf_type == 3）"""
    chapter_data, kwargs, course_info = get_chapter_info(course, session)

    texts = {
        leaf["id"]: leaf["name"]
        for leaf in _iter_leaves(chapter_data)
        if leaf.get("leaf_type") == 3
    }

    log(f"📋 找到 {len(texts)} 个图文")
    return texts, kwargs, course_info


def get_homeworks(
    course: Course, session: requests.Session
) -> tuple[list[Homework], dict, ClassroomInfo]:
    """获取课程中的课堂作业（leaf_type == 6）"""
    chapter_data, kwargs, course_info = get_chapter_info(course, session)

    homeworks: list[Homework] = [
        {
            "id": leaf["id"],
            "name": leaf["name"],
            "start_time": leaf.get("start_time"),
            "score_deadline": leaf.get("score_deadline"),
            "is_score": leaf.get("is_score"),
            "chapter_id": leaf.get("chapter_id"),
        }
        for leaf in _iter_leaves(chapter_data)
        if leaf.get("leaf_type") == 6
    ]

    log(f"📋 找到 {len(homeworks)} 个课堂作业")
    return homeworks, kwargs, course_info


def get_leaf_info(
    course: Course, leaf_id: int, session: requests.Session
) -> int | None:
    """获取 leaf 信息，提取 leaf_type_id"""
    kwargs = _get_course_kwargs(course)
    url = f"https://www.yuketang.cn/mooc-api/v1/lms/learn/leaf_info/{course['classroom_id']}/{leaf_id}/"
    try:
        response = session.get(url, **kwargs)
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
    kwargs = _get_course_kwargs(course)
    url = f"https://www.yuketang.cn/mooc-api/v1/lms/exercise/get_exercise_list/{homework_id}/"
    try:
        response = session.get(url, **kwargs)
        data = json.loads(response.text)
        if data.get("success", False):
            return data.get("data", {}).get("problems", [])
        return []
    except Exception as e:
        log(f"❌ 获取作业题目失败！错误: {e}")
        return []


def check_text_finish_status(
    text_id: int, course: Course, session: requests.Session
) -> dict:
    """检查图文阅读状态"""
    kwargs = _get_course_kwargs(course)
    url = f"https://www.yuketang.cn/mooc-api/v1/lms/learn/user_article_finish_status/{text_id}/"
    try:
        response = session.get(url, **kwargs)
        return json.loads(response.text)
    except Exception as e:
        log(f"❌ 获取图文阅读状态失败！错误: {e}")
        return {}


def submit_homework_answer(
    problem_id: int,
    answer: str | list,
    course_info: ClassroomInfo,
    session: requests.Session,
    kwargs: dict,
) -> SubmitResult:
    """提交单个题目答案，返回提交结果详情"""
    url = "https://www.yuketang.cn/mooc-api/v1/lms/exercise/problem_apply/"

    # 确保 answer 是列表格式
    if isinstance(answer, str):
        answer = [answer]

    payload = {
        "classroom_id": course_info["id"],
        "problem_id": problem_id,
        "answer": answer,
    }

    try:
        response = session.post(url, json=payload, **kwargs)

        # 处理限流
        match = re.search(r"Expected available in(.+?)second.", response.text)
        if match:
            delay_time = match.group(1).strip()
            log(f"⚠️  服务器限流，需等待 {delay_time} 秒")
            time.sleep(float(delay_time) + 0.5)
            log("🔄 重新发送请求...")
            return submit_homework_answer(
                problem_id, answer, course_info, session, kwargs
            )

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
