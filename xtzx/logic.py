import json
import random
import re
import time

import requests

from .utils import log


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
        r = requests.post(
            heartbeat_url, headers=headers, json={"heart_data": heart_data}
        )

        try:
            match = re.search(r"Expected available in(.+?)second.", r.text)
            if match:
                delay_time = match.group(1).strip()
                log(f"⚠️  服务器限流，需等待 {delay_time} 秒")
                time.sleep(float(delay_time) + 0.5)
                log("🔄 重新发送请求...")
                requests.post(
                    heartbeat_url,
                    headers=headers,
                    json={"heart_data": heart_data},
                    timeout=20,
                )
        except Exception:
            pass

        time.sleep(0.5)
        try:
            response = requests.get(progress_url, headers=headers)
            rate = json.loads(response.text)["data"][video_id].get("rate", 0) or 0
            log(f"📊 {video_name} 进度: {float(rate) * 100:.1f}%")
        except Exception:
            pass

    log(f"✅ {video_name} 完成！")
