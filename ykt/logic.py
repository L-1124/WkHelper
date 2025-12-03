import json
import os
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import requests
from api import (
    check_text_finish_status,
    get_homework_questions,
    get_homeworks,
    get_leaf_info,
    get_texts,
    get_videos,
    submit_homework_answer,
)
from models import ClassroomInfo, Course, Homework, UserInfo
from utils import get_input, log


def load_answer_file(course_name: str) -> dict[str, dict[str, list[str]]]:
    """加载答案文件，格式为 JSON: {"LibraryID": {"Version": ["答案"], ...}, ...}"""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    file_path = os.path.join(base_dir, "answer", f"{course_name}_lib.json")
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            log(f"📖 加载答案文件: {file_path}")
            return json.load(f)
    except FileNotFoundError:
        # log(f"❌ 答案文件不存在: {file_path}")
        return {}
    except json.JSONDecodeError:
        log("❌ 答案文件格式错误，请使用 JSON 格式")
        return {}


def watch_video(
    video_id: int,
    video_name: str,
    classroom_info: ClassroomInfo,
    user_id: int,
    session: requests.Session,
    kwargs: dict,
):
    video_id_str = str(video_id)
    classroom_id = str(classroom_info["id"])
    progress_url = f"https://www.yuketang.cn/video-log/get_video_watch_progress/?cid={classroom_info['course_id']}&user_id={user_id}&classroom_id={classroom_id}&video_type=video&vtype=rate&video_id={video_id_str}&snapshot=1"

    response = session.get(progress_url, **kwargs)
    if '"completed":1' in response.text:
        log(f"⏭️  {video_name} 已完成，跳过")
        return

    log(f"🎬 开始学习: {video_name}")

    video_frame = 0
    rate = 0
    try:
        data = json.loads(response.text)["data"][video_id_str]
        rate = data.get("rate", 0) or 0
        video_frame = data.get("watch_length", 0)
    except Exception:
        pass

    heartbeat_url = "https://www.yuketang.cn/video-log/heartbeat/"
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
                "c": classroom_info["course_id"],
                "v": video_id,
                "skuid": classroom_info["free_sku_id"],
                "classroomid": classroom_id,
                "cc": video_id_str,
                "d": 4976.5,
                "pg": f"{video_id_str}_{''.join(random.sample('abcdefghijklmnopqrstuvwxyz0123456789', 4))}",
                "sq": i,
                "t": "video",
            }
            for i in range(3)
        ]

        video_frame += LEARNING_RATE * 3
        r = session.post(heartbeat_url, json={"heart_data": heart_data}, **kwargs)

        try:
            match = re.search(r"Expected available in(.+?)second.", r.text)
            if match:
                delay_time = match.group(1).strip()
                log(f"⚠️  服务器限流，需等待 {delay_time} 秒")
                time.sleep(float(delay_time) + 0.5)
                log("🔄 重新发送请求...")
                session.post(heartbeat_url, json={"heart_data": heart_data}, **kwargs)
        except Exception:
            pass

        time.sleep(0.5)
        try:
            response = session.get(progress_url, **kwargs)
            rate = json.loads(response.text)["data"][video_id_str].get("rate", 0) or 0
            log(f"📊 {video_name} 进度: {float(rate) * 100:.1f}%")
        except Exception:
            pass

    log(f"✅ {video_name} 完成！")


def read_text(
    text_id: int,
    text_name: str,
    course: Course,
    session: requests.Session,
):
    log(f"📖 正在阅读: {text_name}")
    get_leaf_info(course, text_id, session)
    resp = check_text_finish_status(text_id, course, session)
    if not resp.get("success") and not resp.get("data", {}).get("finish"):
        log(f"❌ 阅读 {text_name} 失败")
        return
    log(f"✅ {text_name} 阅读完成")
    time.sleep(1)


def process_single_homework(
    hw: Homework,
    course_answers: dict[str, dict[str, list[str]]],
    course: Course,
    course_info: ClassroomInfo,
    session: requests.Session,
    kwargs: dict,
):
    """处理单个作业的答题"""
    log(f"\n🎯 正在处理: {hw['name']}")

    # 获取 leaf_type_id
    leaf_type_id = get_leaf_info(course, hw["id"], session)
    if not leaf_type_id:
        log("  ❌ 无法获取作业详情ID (leaf_type_id)")
        return

    questions = get_homework_questions(leaf_type_id, course, session)

    if not questions:
        log("  ⚠️ 未获取到题目")
        return

    log(f"  📋 共 {len(questions)} 道题目")

    success_count = 0
    correct_count = 0
    for i, q in enumerate(questions, 1):
        # 尝试从题目内容中获取 LibraryID 和 Version
        library_id = None
        version = None
        if "content" in q:
            library_id = q["content"].get("LibraryID") or q["content"].get("library_id")
            version = q["content"].get("Version")

        if not library_id or not version:
            log(f"  ⚠️ 第{i}题 无法获取 LibraryID 或 Version，跳过")
            continue

        library_id = str(library_id)

        # 查找答案
        answer = None
        if library_id in course_answers and version in course_answers[library_id]:
            answer = course_answers[library_id][version]

        if answer:
            problem_id = q.get("problem_id") or q.get("id")
            if problem_id is None:
                log(f"  ⚠️ 第{i}题 无法获取题目ID，跳过")
                continue

            if q.get("user", {}).get("my_count", 0) >= q.get("max_retry", 1):
                log(f"  ⏭️ 第{i}题 达到最大回答次数，跳过")
                continue

            # 多选题答案处理：如果是 "ABC" 这种形式，转换为 ["A", "B", "C"]
            if (
                isinstance(answer, str)
                and len(answer) > 1
                and answer.isupper()
                and answer.isalpha()
            ):
                answer = list(answer)

            result = submit_homework_answer(
                problem_id, answer, course_info, session, kwargs
            )
            if result["success"]:
                success_count += 1
                if result["is_correct"]:
                    correct_count += 1
                    log(f"  ✅ 第{i}题 提交成功 - 回答正确")
                else:
                    correct_ans = ", ".join(result["correct_answer"])
                    log(f"  ⚠️ 第{i}题 提交成功 - 回答错误，正确答案: {correct_ans}")
            else:
                print(result)
                log(f"  ❌ 第{i}题 提交失败")
            time.sleep(random.uniform(3, 4))
        else:
            log(f"  ⏭️ 第{i}题 无答案 (LibID: {library_id}, Ver: {version})，跳过")

    log(
        f"  📊 提交 {success_count}/{len(questions)} 道，正确 {correct_count}/{success_count} 道"
    )


def learn_videos(
    target_courses: list[Course], userinfo: UserInfo, session: requests.Session
):
    """学习课程视频"""
    for idx, course in enumerate(target_courses, 1):
        log(f"\n🎯 [{idx}/{len(target_courses)}] 处理课程: {course['name']}")
        videos, kwargs, classroom_info = get_videos(course, session)

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = []
            for video_id, video_name in videos.items():
                future = executor.submit(
                    watch_video,
                    video_id,
                    video_name,
                    classroom_info,
                    userinfo["id"],
                    session,
                    kwargs,
                )
                futures.append(future)

            for future in futures:
                future.result()


def learn_texts(target_courses: list[Course], session: requests.Session):
    """学习课程图文"""
    for idx, course in enumerate(target_courses, 1):
        log(f"\n🎯 [{idx}/{len(target_courses)}] 处理课程图文: {course['name']}")
        texts, kwargs, _ = get_texts(course, session)

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = []
            for text_id, text_name in texts.items():
                future = executor.submit(
                    read_text,
                    text_id,
                    text_name,
                    course,
                    session,
                )
                futures.append(future)

            for future in futures:
                future.result()


def fetch_homeworks(target_courses: list[Course], session: requests.Session):
    """获取课程作业"""
    for idx, course in enumerate(target_courses, 1):
        log(f"\n📝 [{idx}/{len(target_courses)}] 获取课程作业: {course['name']}")
        homeworks, kwargs, course_info = get_homeworks(course, session)

        course_answers = load_answer_file(course["name"])

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
            "选择作业编号（0表示全部，q返回）: ",
            lambda x: x.isdigit() and int(x) <= len(homeworks),
        )
        if not hw_choice:
            continue

        target_hws = (
            homeworks if int(hw_choice) == 0 else [homeworks[int(hw_choice) - 1]]
        )

        for hw in target_hws:
            process_single_homework(
                hw, course_answers, course, course_info, session, kwargs
            )


def _fetch_single_homework_answers(
    course: Course, hw: Homework, session: requests.Session
) -> dict:
    """获取单个作业的答案"""
    leaf_type_id = get_leaf_info(course, hw["id"], session)
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

    answers_data = {}

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [
            executor.submit(_fetch_single_homework_answers, course, hw, session)
            for hw in homeworks
        ]
        for future in futures:
            hw_answers = future.result()
            for lib_id, versions in hw_answers.items():
                if lib_id not in answers_data:
                    answers_data[lib_id] = {}
                answers_data[lib_id].update(versions)

    if not answers_data:
        log("⚠️ 未找到任何答案")
        return

    base_dir = os.path.dirname(os.path.abspath(__file__))
    file_path = os.path.join(base_dir, "answer", f"{course['name']}_lib.json")
    os.makedirs(os.path.dirname(file_path), exist_ok=True)

    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(answers_data, f, ensure_ascii=False, indent=2)

    log(f"✅ 答案已保存至: {file_path}")
