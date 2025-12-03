from ..utils import get_input, log
from .api import get_basic_info, get_courses
from .auth import init_session
from .logic import learn_videos


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

    while True:
        choice = get_input(
            prompt_lines=["\n请选择要学习的课程:"],
            input_msg="输入课程编号（输入0学习全部课程，输入q退出）: ",
            validator=lambda x: x.isdigit() and 0 <= int(x) <= len(courses),
        )

        if choice is None:
            break

        target_courses = courses if int(choice) == 0 else [courses[int(choice) - 1]]

        learn_videos(target_courses, headers)

        log("👋 退出程序")
