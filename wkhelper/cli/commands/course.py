"""课程查询命令。"""

from typing import Annotated, cast

from cyclopts import App, Parameter

from wkhelper.cli.context import PlatformCode
from wkhelper.cli.deps import _open_course, _open_platform, _paginate
from wkhelper.cli.envelope import CommandResult
from wkhelper.domain.actions import NextAction
from wkhelper.domain.courses import CourseDetail, CourseItem, CourseListData, course_reference, dedupe_courses
from wkhelper.infra.serialization import to_json_value

course_app = App(name="course", help="课程查询")


@course_app.command(name="list")
async def course_list_cmd(
    platform: Annotated[PlatformCode | None, Parameter(name=["--platform", "-p"], help="平台代码")] = None,
    account: Annotated[str | None, Parameter(name=["--account", "-a"], help="账号 ID")] = None,
    limit: Annotated[int, Parameter(name=["--limit"], help="每页条数")] = 10,
    cursor: Annotated[str | None, Parameter(name=["--cursor"], help="分页游标")] = None,
) -> CommandResult[CourseListData]:
    """查询当前账号的课程列表。"""
    async with _open_platform(platform, account) as plat:
        courses = dedupe_courses(await plat.get_courses())
        items = [
            CourseItem(
                id=course_reference(course),
                name=course.name,
                platform=course.platform_id,
                classroom_id=cast(str | int | None, to_json_value(course.metadata.get("classroom_id"))),
            )
            for course in courses
        ]
        page_items, meta = _paginate(items, limit, cursor)
        next_actions = (
            [NextAction(reason="查看首门课程视频", argv=["wkhelper", "video", "list", "--course", page_items[0].id])] if page_items else []
        )
        return CommandResult(data=CourseListData(courses=page_items), meta=meta.model_dump(), next_actions=next_actions)


@course_app.command(name="inspect")
async def course_inspect_cmd(
    course: Annotated[str, Parameter(name=["--course", "-c"], help="课程 ID")],
    platform: Annotated[PlatformCode | None, Parameter(name=["--platform", "-p"], help="平台代码")] = None,
    account: Annotated[str | None, Parameter(name=["--account", "-a"], help="账号 ID")] = None,
) -> CommandResult[CourseDetail]:
    """查看指定课程的详细信息。"""
    async with _open_course(course, platform, account) as (_, matched):
        clean_meta = {
            str(k): to_json_value(v) for k, v in matched.metadata.items() if not str(k).startswith("_") and not isinstance(v, set)
        }
        return CommandResult(
            data=CourseDetail(
                id=course_reference(matched),
                name=matched.name,
                platform=matched.platform_id,
                metadata=clean_meta,
            )
        )


course_list = course_list_cmd
course_inspect = course_inspect_cmd

__all__ = [
    "course_app",
    "course_inspect",
    "course_inspect_cmd",
    "course_list",
    "course_list_cmd",
]
