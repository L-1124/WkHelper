import json
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import qrcode
import requests
import websocket

log_lock = threading.Lock()


def log(msg):
    with log_lock:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def get_ykt_cookie():
    """雨课堂扫码登录获取Cookie"""
    login_data = {}

    def on_message(ws, message):
        msg = json.loads(message)
        if "qrcode" in msg and msg["qrcode"]:
            qr = qrcode.QRCode()
            qr.add_data(msg["qrcode"])
            qr.print_ascii(invert=True)
            print("\n请使用雨课堂扫码登录...")

        if msg.get("op") == "loginsuccess":
            login_data.update(msg)
            ws.close()

    def on_open(ws):
        ws.send(
            json.dumps({
                "op": "requestlogin",
                "role": "web",
                "version": 1.4,
                "type": "qrcode",
            })
        )

    ws = websocket.WebSocketApp(
        "wss://www.yuketang.cn/wsapp/", on_message=on_message, on_open=on_open
    )
    ws.run_forever()

    response = requests.post(
        "https://www.yuketang.cn/pc/web_login",
        json={"Auth": login_data["Auth"], "UserID": str(login_data["UserID"])},
    )

    return {
        "csrftoken": response.cookies.get("csrftoken"),
        "sessionid": response.cookies.get("sessionid"),
    }


def init_session():
    log("🔐 正在获取雨课堂Cookie...")
    cookies = get_ykt_cookie()

    if not cookies["csrftoken"] or not cookies["sessionid"]:
        log("❌ Cookie获取失败！")
        exit(1)

    log("✅ Cookie获取成功！")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Content-Type": "application/json",
        "Cookie": f"csrftoken={cookies['csrftoken']}; sessionid={cookies['sessionid']}",
    }
    return headers


def get_basic_info(headers: dict) -> dict:
    response = requests.get(
        "https://www.yuketang.cn/api/v3/user/basic-info", headers=headers
    )
    resp = json.loads(response.text)
    if resp["code"] != 0:
        log("❌ 获取用户信息失败！")
        exit(1)

    return resp["data"]


def get_courses(headers: dict):
    url = "https://www.yuketang.cn/v2/api/web/courses/list?identity=2"
    response = requests.get(url, headers=headers)
    resp = json.loads(response.text)
    if resp["errcode"] != 0:
        log("❌ 获取课程列表失败！")
        exit(1)

    try:
        courses = []
        for course in resp["data"]["list"]:
            courses.append({
                "name": course["course"]["name"],
                "classroom_id": course["classroom_id"],
                "university_id": course["course"]["university_id"],
                "id": course["course"]["id"],
            })
        return courses
    except:
        log("❌ 获取课程列表失败！")
        exit(1)


def get_classroom_info(course: dict, headers: dict) -> tuple[dict, dict]:
    url = (
        f"https://www.yuketang.cn/v2/api/web/classrooms/{course['classroom_id']}?role=5"
    )
    headers = headers.copy()
    headers["Cookie"] += (
        f"; xtbz=ykt; platform_type=1; uv_id={course['university_id']}; university_id={course['university_id']}; platform_id=3; classroom_id={course['classroom_id']}; classroomID={course['classroom_id']}"
    )
    headers["Xtbz"] = "ykt"
    response = requests.get(url, headers=headers)
    data = json.loads(response.text)
    if data["errcode"] != 0:
        log("❌ 获取课程信息失败！")
        exit(1)
    return data["data"], headers


def get_videos(course: dict, headers: dict) -> tuple[dict, dict, dict]:
    course_info, headers = get_classroom_info(course, headers)
    url = f"https://www.yuketang.cn/mooc-api/v1/lms/learn/course/chapter?cid={course['classroom_id']}&sign={course_info['course_sign']}&term=latest&uv_id={course['university_id']}&classroom_id={course['classroom_id']}"
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
        return videos, headers, course_info
    except:
        log("❌ 获取视频列表失败！")
        exit(1)


def watch_video(video_id, video_name, classroom_info, user_id, headers):
    video_id = str(video_id)
    classroom_id = str(classroom_info["id"])
    progress_url = f"https://www.yuketang.cn/video-log/get_video_watch_progress/?cid={classroom_info['course_id']}&user_id={user_id}&classroom_id={classroom_id}&video_type=video&vtype=rate&video_id={video_id}&snapshot=1"

    response = requests.get(progress_url, headers=headers)
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
    except:
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
                "v": int(video_id),
                "skuid": classroom_info["free_sku_id"],
                "classroomid": classroom_id,
                "cc": video_id,
                "d": 4976.5,
                "pg": f"{video_id}_{''.join(random.sample('abcdefghijklmnopqrstuvwxyz0123456789', 4))}",
                "sq": i,
                "t": "video",
            }
            for i in range(3)
        ]

        video_frame += LEARNING_RATE * 3
        r = requests.post(
            heartbeat_url, headers=headers, json={"heart_data": heart_data}
        )

        try:
            delay_time = (
                re.search(r"Expected available in(.+?)second.", r.text).group(1).strip()
            )
            log(f"⚠️  服务器限流，需等待 {delay_time} 秒")
            time.sleep(float(delay_time) + 0.5)
            log("🔄 重新发送请求...")
            requests.post(
                heartbeat_url, headers=headers, json={"heart_data": heart_data}
            )
        except:
            pass

        time.sleep(0.5)
        try:
            response = requests.get(progress_url, headers=headers)
            rate = json.loads(response.text)["data"][video_id].get("rate", 0) or 0
            log(f"📊 {video_name} 进度: {float(rate) * 100:.1f}%")
        except:
            pass

    log(f"✅ {video_name} 完成！")


def ykt_main():
    headers = init_session()

    userinfo = get_basic_info(headers)
    log(f"👤 登录成功：{userinfo['name']}（{userinfo['school']}）")

    log("📚 正在获取课程列表...")
    courses = get_courses(headers)

    if not courses:
        log("⚠️  未找到任何课程")
        return

    log(f"✅ 获取到 {len(courses)} 门课程")
    for i, course in enumerate(courses, 1):
        log(f"  [{i}] {course['name']}")

    print("\n请选择要学习的课程:")
    choice = input("输入课程编号（输入0学习全部课程）: ")

    if not choice.isdigit() or int(choice) > len(courses):
        log("❌ 输入不合法！")
        return

    target_courses = courses if int(choice) == 0 else [courses[int(choice) - 1]]

    for idx, course in enumerate(target_courses, 1):
        log(f"\n🎯 [{idx}/{len(target_courses)}] 处理课程: {course['name']}")
        videos, headers, classroom_info = get_videos(course, headers)

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = []
            for video_id, video_name in videos.items():
                future = executor.submit(
                    watch_video,
                    video_id,
                    video_name,
                    classroom_info,
                    userinfo["id"],
                    headers,
                )
                futures.append(future)

            for future in futures:
                future.result()

    log("\n✅ 全部完成！")


if __name__ == "__main__":
    ykt_main()
