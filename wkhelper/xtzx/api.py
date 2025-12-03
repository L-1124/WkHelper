import json

import requests

from ..utils import log
from .models import Course, UserInfo


def get_basic_info(headers: dict) -> UserInfo:
    response = requests.get(
        "https://www.xuetangx.com/api/v1/u/user/basic_profile/", headers=headers
    )
    resp = json.loads(response.text)
    if not resp["success"]:
        log("❌ 获取用户信息失败！")
        exit(1)

    return resp["data"]


def get_courses(headers: dict) -> list[Course]:
    url = "https://www.xuetangx.com/api/v1/lms/user/user-courses/?status=1&page=1"
    response = requests.get(url, headers=headers)
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


def get_videos(course: Course, headers: dict) -> tuple[dict[int, str], dict]:
    url = f"https://www.xuetangx.com/api/v1/lms/learn/course/chapter?cid={course['classroom_id']}&sign={course['sign']}"
    try:
        response = requests.get(url, headers=headers)
        data = json.loads(response.text)["data"]["course_chapter"]

        videos = {}
        for chapter in data:
            for section in chapter["section_leaf_list"]:
                for leaf in section.get("leaf_list", [section]):
                    if leaf.get("leaf_type") == 0:
                        videos[leaf["id"]] = leaf["name"]

        log(f"📋 找到 {len(videos)} 个视频")
        return videos, headers
    except Exception:
        log("❌ 获取视频列表失败！")
        exit(1)
