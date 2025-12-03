from concurrent.futures import ThreadPoolExecutor

from .api import get_basic_info, get_courses, get_videos
from .auth import init_session
from .logic import watch_video
from .utils import log


def main():
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

    log("👋 任务完成！")
