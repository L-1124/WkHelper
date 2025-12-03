import json
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from io import BytesIO

import qrcode
import requests
import websocket
from PIL import Image

log_lock = threading.Lock()


def log(msg):
    with log_lock:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def get_cookie():
    """扫码登录获取Cookie"""
    login_data = {}

    def on_message(ws, message):
        msg = json.loads(message)
        if "ticket" in msg and msg["ticket"]:
            resp = requests.get(msg["ticket"])
            img = Image.open(BytesIO(resp.content))
            from pyzbar.pyzbar import decode

            url = decode(img)[0].data.decode("utf-8")
            qr = qrcode.QRCode()
            qr.add_data(url)
            qr.print_ascii(invert=True)
            print("\n请使用微信扫码登录...")

        if msg.get("op") == "loginsuccess":
            login_data.update(msg)
            ws.close()

    def on_open(ws):
        ws.send(
            json.dumps({
                "op": "requestlogin",
                "role": "web",
                "version": "1.4",
                "purpose": "login",
                "xtbz": "xt",
                "x-client": "web",
            })
        )

    ws = websocket.WebSocketApp(
        "wss://www.xuetangx.com/wsapp/", on_message=on_message, on_open=on_open
    )
    ws.run_forever()

    response = requests.post(
        "https://www.xuetangx.com/api/v1/u/login/wx/",
        json={
            "s_s": login_data["token"],
            "preset_properties": {
                "$timezone_offset": -480,
                "$screen_height": 1067,
                "$screen_width": 1707,
                "$lib": "js",
                "$lib_version": "1.19.14",
                "$latest_traffic_source_type": "直接流量",
                "$latest_search_keyword": "未取到值_直接打开",
                "$latest_referrer": "",
                "$is_first_day": False,
                "$referrer": "https://www.xuetangx.com/",
                "$referrer_host": "www.xuetangx.com",
                "$url": "https://www.xuetangx.com/",
                "$url_path": "/",
                "$title": "学堂在线 - 精品在线课程学习平台",
                "_distinct_id": "19a16647ffb7cf-0590d22341cefa4-4c657b58-1821369-19a16647ffc129c",
            },
            "page_name": "首页",
        },
    )

    return {
        "csrftoken": response.cookies.get("csrftoken"),
        "sessionid": response.cookies.get("sessionid"),
    }


def init_session():
    log("🔐 正在获取学堂在线Cookie...")
    # cookies = get_cookie()
    cookies = {
        "csrftoken": "yDDymSsXbZZwDUW6PD7H6DIKx0wcovbf",
        "sessionid": "8blpu8j4ora3nxz8qh9tsxqd43mm8rss",
    }

    if not cookies["csrftoken"] or not cookies["sessionid"]:
        log("❌ Cookie获取失败！")
        exit(1)

    log("✅ Cookie获取成功！")

    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Content-Type": "application/json",
        "Cookie": f"csrftoken={cookies['csrftoken']}; sessionid={cookies['sessionid']}",
        "X-CSRFToken": cookies["csrftoken"],
        "Xtbz": "xt",
    }


def get_basic_info(headers: dict) -> dict:
    response = requests.get(
        "https://www.xuetangx.com/api/v1/u/user/basic_profile/", headers=headers
    )
    resp = json.loads(response.text)
    if not resp["success"]:
        log("❌ 获取用户信息失败！")
        exit(1)

    return resp["data"]


def get_courses(headers: dict):
    url = "https://www.xuetangx.com/api/v1/lms/user/user-courses/?status=1&page=1"
    response = requests.get(url, headers=headers)
    resp = json.loads(response.text)
    if not resp["success"]:
        log("❌ 获取课程列表失败！")
        exit(1)

    try:
        courses = []
        for course in resp["data"]["product_list"]:
            courses.append({
                "name": course["name"],
                "classroom_id": course["classroom_id"],
                "sign": course["sign"],
                "product_id": course["product_id"],
                "sku_id": course["sku_id"],
            })
        return courses
    except:
        log("❌ 获取课程列表失败！")
        exit(1)


def get_videos(course: dict, headers: dict) -> tuple[dict, dict]:
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
    except:
        log("❌ 获取视频列表失败！")
        exit(1)


def watch_video(video_id, video_name, classroom_id, course_sign, headers):
    video_id = str(video_id)

    resp = requests.get(
        f"https://www.xuetangx.com/api/v1/lms/learn/leaf_info/{classroom_id}/{video_id}/?sign={course_sign}",
        headers=headers,
    )

    data = resp.json()["data"]

    user_id = data["user_id"]
    sku_id = data["sku_id"]
    course_id = data["course_id"]
    progress_url = f"https://www.xuetangx.com/video-log/get_video_watch_progress/??cid={course_id}&user_id={user_id}&classroom_id={classroom_id}&video_type=video&vtype=rate&video_id={video_id}"

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
                heartbeat_url,
                headers=headers,
                json={"heart_data": heart_data},
                timeout=20,
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
        videos, headers = get_videos(course, headers)

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = []
            for video_id, video_name in videos.items():
                future = executor.submit(
                    watch_video,
                    video_id,
                    video_name,
                    course["classroom_id"],
                    course["sign"],
                    headers,
                )
                futures.append(future)

            for future in futures:
                future.result()

    log("\n✅ 全部完成！")


if __name__ == "__main__":
    ykt_main()
