import json
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import requests

from ..db import db
from ..utils import get_input, log
from .api import (
    get_homework_questions,
    get_homeworks,
    get_leaf_type_id,
    get_videos,
    submit_homework_answer,
)
from .models import ClassroomInfo, Course, Homework


def watch_video(
    video_id: int | str,
    video_name: str,
    classroom_id: int | str,
    course_sign: str,
    session: requests.Session,
):
    video_id = str(video_id)

    resp = session.get(
        f"https://www.xuetangx.com/api/v1/lms/learn/leaf_info/{classroom_id}/{video_id}/?sign={course_sign}"
    )

    data = resp.json()["data"]

    user_id = data["user_id"]
    sku_id = data["sku_id"]
    course_id = data["course_id"]
    progress_url = f"https://www.xuetangx.com/video-log/get_video_watch_progress/??cid={course_id}&user_id={user_id}&classroom_id={classroom_id}&video_type=video&vtype=rate&video_id={video_id}"

    response = session.get(progress_url)
    if '"completed":1' in response.text:
        log(f"⏭️  {video_name} 已完成，跳过")
        return

    log(f"🎬 开始学习: {video_name}")

    video_frame = 0
    rate = 0
    try:
        data = json.loads(response.text)["data"][video_id]
        rate = data.get("rate", 0) or 0
        video_frame = data.get("watch_length", 0)
    except Exception:
        pass

    heartbeat_url = "https://www.xuetangx.com/video-log/heartbeat/"
    timestamp = int(time.time() * 1000)

    LEARNING_RATE = 8

    while float(rate) <= 0.95:
        heart_data = [
            {
                "i": 5,
                "et": "heartbeat",
                "p": "web",
                "n": "ali-cdn.xuetangx.com",
                "lob": "ykt",
                "cp": video_frame + LEARNING_RATE * i,
                "fp": 0,
                "tp": 0,
                "sp": 2,
                "ts": str(timestamp),
                "u": int(user_id),
                "uip": "",
                "c": int(course_id),
                "v": int(video_id),
                "skuid": sku_id,
                "classroomid": str(classroom_id),
                "cc": video_id,
                "d": 4976.5,
                "pg": f"{video_id}_{''.join(random.sample('abcdefghijklmnopqrstuvwxyz0123456789', 4))}",
                "sq": i,
                "t": "video",
            }
            for i in range(3)
        ]

        video_frame += LEARNING_RATE * 3
        r = session.post(heartbeat_url, json={"heart_data": heart_data})

        try:
            match = re.search(r"Expected available in(.+?)second.", r.text)
            if match:
                delay_time = match.group(1).strip()
                log(f"⚠️  服务器限流，需等待 {delay_time} 秒")
                time.sleep(float(delay_time) + 0.5)
                log("🔄 重新发送请求...")
                session.post(
                    heartbeat_url,
                    json={"heart_data": heart_data},
                    timeout=20,
                )
        except Exception:
            pass

        time.sleep(1.5)
        try:
            response = session.get(progress_url)
            rate = json.loads(response.text)["data"][video_id].get("rate", 0) or 0
            log(f"📊 {video_name} 进度: {float(rate) * 100:.1f}%")
        except Exception:
            pass

    log(f"✅ {video_name} 完成！")


def process_single_homework(
    hw: Homework,
    course: Course,
    course_info: ClassroomInfo,
    session: requests.Session,
):
    """处理单个作业的答题"""
    log(f"\n🎯 正在处理: {hw['name']}")

    # 获取 leaf_type_id
    leaf_type_id = get_leaf_type_id(course, hw["id"], session)
    if not leaf_type_id:
        log("  ❌ 无法获取作业详情ID (leaf_type_id)")
        return

    questions = get_homework_questions(leaf_type_id, course, session)

    if not questions:
        log("  ⚠️ 未获取到题目")
        return

    log(f"  📋 共 {len(questions)} 道题目")

    def submit_one(i, q):
        # 尝试从题目内容中获取 LibraryID 和 Version
        library_id = None
        version = None
        if "content" in q:
            library_id = q["content"].get("LibraryID") or q["content"].get("library_id")
            version = q["content"].get("Version")

        if not library_id or not version:
            log(f"  ⚠️ 第{i}题 无法获取 LibraryID 或 Version，跳过")
            return False, False

        library_id = str(library_id)

        # 查找答案
        answer = db.get_answer(library_id, version)

        if answer:
            problem_id = q.get("problem_id") or q.get("id")
            if problem_id is None:
                log(f"  ⚠️ 第{i}题 无法获取题目ID，跳过")
                return False, False

            if q.get("user", {}).get("my_count", 0) >= q.get("max_retry", 1):
                log(f"  ⏭️ 第{i}题 达到最大回答次数，跳过")
                return False, False

            result = submit_homework_answer(
                hw["chapter_id"], leaf_type_id, problem_id, answer, course_info, session
            )
            if result["success"]:
                if result["is_correct"]:
                    log(f"  ✅ 第{i}题 提交成功 - 回答正确")
                    return True, True
                else:
                    correct_ans = ", ".join(result["correct_answer"])
                    log(f"  ⚠️ 第{i}题 提交成功 - 回答错误，正确答案: {correct_ans}")
                    return True, False
            else:
                log(f"  ❌ 第{i}题 提交失败")
                return False, False
        else:
            log(f"  ⏭️ 第{i}题 无答案 (LibID: {library_id}, Ver: {version})，跳过")
            return False, False

    success_count = 0
    correct_count = 0

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [
            executor.submit(submit_one, i, q) for i, q in enumerate(questions, 1)
        ]
        for future in futures:
            s, c = future.result()
            if s:
                success_count += 1
            if c:
                correct_count += 1

    log(
        f"  📊 提交 {success_count}/{len(questions)} 道，正确 {correct_count}/{success_count} 道"
    )


def process_random_homework(
    hw: Homework,
    course: Course,
    course_info: ClassroomInfo,
    session: requests.Session,
):
    """处理单个作业的随机答题"""
    log(f"\n🎲 正在随机答题: {hw['name']}")

    # 获取 leaf_type_id
    leaf_type_id = get_leaf_type_id(course, hw["id"], session)
    if not leaf_type_id:
        log("  ❌ 无法获取作业详情ID (leaf_type_id)")
        return

    questions = get_homework_questions(leaf_type_id, course, session)

    if not questions:
        log("  ⚠️ 未获取到题目")
        return

    log(f"  📋 共 {len(questions)} 道题目")

    for i, q in enumerate(questions, 1):
        if q.get("user", {}).get("is_right", False):
            log(f"  ✅ 第{i}题 已正确，跳过")
            continue

        if q.get("user", {}).get("my_count", 0) >= q.get("max_retry", 999):
            log(f"  ⏭️ 第{i}题 次数耗尽，跳过")
            continue

        problem_id = q.get("problem_id") or q.get("id")

        # 尝试获取选项
        options = []
        if "content" in q and "Options" in q["content"]:
            options = [opt["key"] for opt in q["content"]["Options"]]

        if not options:
            options = ["A", "B", "C", "D"]

        # 随机生成答案
        answer = [random.choice(options)]

        # 提交
        result = submit_homework_answer(
            hw["chapter_id"], leaf_type_id, problem_id, answer, course_info, session
        )
        if result["success"]:
            status = "正确" if result["is_correct"] else "错误"
            correct_ans = result.get("correct_answer")
            log(f"  🎲 第{i}题 随机提交 {answer} -> {status}")
            if correct_ans:
                log(f"     正确答案: {correct_ans}")
        else:
            log(f"  ❌ 第{i}题 提交失败")

        time.sleep(random.uniform(2, 3))


def learn_videos(target_courses: list[Course], session: requests.Session):
    for idx, course in enumerate(target_courses, 1):
        log(f"\n🎯 [{idx}/{len(target_courses)}] 处理课程: {course['name']}")
        videos, session = get_videos(course, session)

        video_list = list(videos.items())
        if not video_list:
            log("暂无视频")
            continue

        for i, (vid, vname) in enumerate(video_list, 1):
            log(f"  [{i}] {vname}")

        v_choice = get_input(
            [],
            "选择视频编号（0表示全部，多选空格分隔，q返回）: ",
            lambda x: all(p.isdigit() and int(p) <= len(video_list) for p in x.split()),
        )
        if not v_choice:
            continue

        choices = [int(x) for x in v_choice.split()]
        target_videos = (
            video_list if 0 in choices else [video_list[i - 1] for i in choices]
        )

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = []
            for video_id, video_name in target_videos:
                future = executor.submit(
                    watch_video,
                    video_id,
                    video_name,
                    course["classroom_id"],
                    course["sign"],
                    session,
                )
                futures.append(future)

            for future in futures:
                future.result()


def fetch_homeworks(target_courses: list[Course], session: requests.Session):
    """获取课程作业"""
    for idx, course in enumerate(target_courses, 1):
        log(f"\n📝 [{idx}/{len(target_courses)}] 获取课程作业: {course['name']}")
        homeworks, session, course_info = get_homeworks(course, session)

        if not homeworks:
            log("暂无作业")
            continue

        for i, hw in enumerate(homeworks, 1):
            deadline_str = "无截止时间"
            if hw["score_deadline"]:
                deadline_str = datetime.fromtimestamp(
                    hw["score_deadline"] / 1000
                ).strftime("%Y-%m-%d %H:%M")
            log(f"  [{i}] {hw['name']}  截止: {deadline_str}")

        hw_choice = get_input(
            [],
            "选择作业编号（0表示全部，多选空格分隔，q返回）: ",
            lambda x: all(p.isdigit() and int(p) <= len(homeworks) for p in x.split()),
        )
        if not hw_choice:
            continue

        choices = [int(x) for x in hw_choice.split()]
        target_hws = homeworks if 0 in choices else [homeworks[i - 1] for i in choices]

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = []
            for hw in target_hws:
                future = executor.submit(
                    process_single_homework, hw, course, course_info, session
                )
                futures.append(future)

            for future in futures:
                future.result()


def random_answer(target_courses: list[Course], session: requests.Session):
    """随机答题（用于获取答案）"""
    for idx, course in enumerate(target_courses, 1):
        log(f"\n🎲 [{idx}/{len(target_courses)}] 随机答题: {course['name']}")
        homeworks, session, course_info = get_homeworks(course, session)

        if not homeworks:
            log("暂无作业")
            continue

        for i, hw in enumerate(homeworks, 1):
            deadline_str = "无截止时间"
            if hw["score_deadline"]:
                deadline_str = datetime.fromtimestamp(
                    hw["score_deadline"] / 1000
                ).strftime("%Y-%m-%d %H:%M")
            log(f"  [{i}] {hw['name']}  截止: {deadline_str}")

        hw_choice = get_input(
            [],
            "选择作业编号（0表示全部，多选空格分隔，q返回）: ",
            lambda x: all(p.isdigit() and int(p) <= len(homeworks) for p in x.split()),
        )
        if not hw_choice:
            continue

        choices = [int(x) for x in hw_choice.split()]
        target_hws = homeworks if 0 in choices else [homeworks[i - 1] for i in choices]

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = []
            for hw in target_hws:
                future = executor.submit(
                    process_random_homework, hw, course, course_info, session
                )
                futures.append(future)

            for future in futures:
                future.result()


def _fetch_single_homework_answers(
    course: Course, hw: Homework, session: requests.Session
) -> dict:
    """获取单个作业的答案"""
    leaf_type_id = get_leaf_type_id(course, hw["id"], session)
    if not leaf_type_id:
        return {}

    questions = get_homework_questions(leaf_type_id, course, session)
    hw_answers = {}
    for q in questions:
        # 提取 LibraryID
        library_id = None
        if "content" in q:
            library_id = q["content"].get("LibraryID") or q["content"].get("library_id")

        version = q["content"].get("Version")

        if not library_id or not version:
            continue

        ans = None
        if "user" in q and q["user"].get("answer"):
            ans = q["user"]["answer"]

        if library_id and ans:
            if str(library_id) not in hw_answers:
                hw_answers[str(library_id)] = {}
            hw_answers[str(library_id)][version] = ans

    return hw_answers


def save_answers(course: Course, session: requests.Session):
    """生成并保存课程答案"""
    log(f"🔍 正在扫描课程答案: {course['name']}")
    homeworks, _, _ = get_homeworks(course, session)

    count = 0
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [
            executor.submit(_fetch_single_homework_answers, course, hw, session)
            for hw in homeworks
        ]
        for future in futures:
            hw_answers = future.result()
            for lib_id, versions in hw_answers.items():
                for version, answer in versions.items():
                    db.save_answer(lib_id, version, answer)
                    count += 1

    if count == 0:
        log("⚠️ 未找到任何答案")
        return

    log(f"✅ 已保存 {count} 个答案到数据库")
