from api import get_basic_info, get_courses
from auth import init_session
from logic import fetch_homeworks, learn_videos, save_answers
from utils import get_input, log


def ykt_main():
    session = init_session()

    userinfo = get_basic_info(session)
    log(f"👤 登录成功：{userinfo['name']}（{userinfo['school']}）")

    log("📚 正在获取课程列表...")
    courses = get_courses(session)

    if not courses:
        log("⚠️  未找到任何课程")
        return

    while True:
        log(f"✅ 获取到 {len(courses)} 门课程")
        for i, course in enumerate(courses, 1):
            log(f"  [{i}] {course['name']}")

        mode = get_input(
            [
                "\n请选择功能:",
                "  [1] 学习课程视频",
                "  [2] 完成课程作业",
                "  [3] 下载课程答案",
                "  [q] 退出",
            ],
            "输入功能编号: ",
            lambda x: x
            in (
                "1",
                "2",
                "3",
            ),
        )
        if not mode:
            break

        choice = get_input(
            ["\n请选择课程:"],
            "输入课程编号（输入0表示全部课程，q返回）: ",
            lambda x: x.isdigit() and int(x) <= len(courses),
        )
        if not choice:
            continue

        target_courses = courses if int(choice) == 0 else [courses[int(choice) - 1]]

        if mode == "1":
            learn_videos(target_courses, userinfo, session)
        elif mode == "2":
            fetch_homeworks(target_courses, session)
        elif mode == "3":
            for course in target_courses:
                save_answers(course, session)
        log("✅ 任务完成！\n")

    log("👋 再见！")


if __name__ == "__main__":
    ykt_main()
