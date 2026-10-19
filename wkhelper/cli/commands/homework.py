"""作业查询与提交命令。"""

from typing import Annotated, Any, Literal

from cyclopts import App, Parameter

from wkhelper.cli.context import PlatformCode
from wkhelper.cli.deps import (
    _open_course,
    _paginate,
    _parse_answers_payload,
    _read_json_file_or_stdin,
)
from wkhelper.cli.envelope import CommandResult
from wkhelper.core.entities import Homework
from wkhelper.core.exceptions import ConfirmationRequiredError, PartialFailureError
from wkhelper.core.homework_runner import run_homework_submit
from wkhelper.core.json_types import parse_json_text
from wkhelper.core.results import (
    HomeworkItem,
    HomeworkListData,
    HomeworkOutline,
    HomeworkSubmitPreview,
    HomeworkSubmitResult,
    QuestionOutline,
)

homework_app = App(name="homework", help="作业查询与提交")


@homework_app.command(name="list")
async def homework_list_cmd(
    course: Annotated[str, Parameter(name=["--course", "-c"], help="课程 ID")],
    status: Annotated[Literal["pending", "completed", "all"], Parameter(name=["--status"], help="状态过滤")] = "all",
    platform: Annotated[PlatformCode | None, Parameter(name=["--platform", "-p"], help="平台代码")] = None,
    account: Annotated[str | None, Parameter(name=["--account", "-a"], help="账号 ID")] = None,
    limit: Annotated[int, Parameter(name=["--limit"], help="每页条数")] = 10,
    cursor: Annotated[str | None, Parameter(name=["--cursor"], help="分页游标")] = None,
) -> CommandResult[HomeworkListData]:
    """查询课程作业列表。"""
    async with _open_course(course, platform, account) as (plat, matched):
        homeworks = await plat.get_homeworks(matched)

        items: list[HomeworkItem] = []
        for homework in homeworks:
            if status == "pending" and homework.completed:
                continue
            if status == "completed" and not homework.completed:
                continue
            items.append(
                HomeworkItem(
                    id=str(homework.id),
                    name=homework.name,
                    deadline=homework.deadline,
                    completed=homework.completed,
                )
            )

        page_items, meta = _paginate(items, limit, cursor)
        return CommandResult(data=HomeworkListData(homeworks=page_items), meta=meta.to_dict())


@homework_app.command(name="inspect")
async def homework_inspect_cmd(
    course: Annotated[str, Parameter(name=["--course", "-c"], help="课程 ID")],
    homework: Annotated[str, Parameter(name=["--homework", "-w"], help="作业 ID")],
    platform: Annotated[PlatformCode | None, Parameter(name=["--platform", "-p"], help="平台代码")] = None,
    account: Annotated[str | None, Parameter(name=["--account", "-a"], help="账号 ID")] = None,
) -> CommandResult[HomeworkOutline]:
    """查看作业元数据及题目大纲。"""
    async with _open_course(course, platform, account) as (plat, matched):
        questions = await plat.get_leaf_questions(homework, matched)

        outlines: list[QuestionOutline] = []
        for index, question in enumerate(questions, 1):
            content = question.get("content", {})
            outlines.append(
                QuestionOutline(
                    index=index,
                    id=str(question.get("problem_id") or question.get("id")),
                    type=question.get("type"),
                    library_id=content.get("LibraryID"),
                )
            )

        return CommandResult(
            data=HomeworkOutline(
                course_id=str(matched.id),
                homework_id=str(homework),
                total_questions=len(questions),
                questions_summary=outlines,
            )
        )


@homework_app.command(name="submit")
async def homework_submit_cmd(
    course: Annotated[str, Parameter(name=["--course", "-c"], help="课程 ID")],
    homework: Annotated[str, Parameter(name=["--homework", "-w"], help="作业 ID")],
    answers_file: Annotated[str | None, Parameter(name=["--answers-file"], help="答案 JSON 文件")] = None,
    answers: Annotated[str | None, Parameter(name=["--answers"], help="直接传入 JSON 格式答案字典")] = None,
    dry_run: Annotated[bool, Parameter(name=["--dry-run"], help="仅校验，不提交")] = False,
    confirm: Annotated[bool, Parameter(name=["--confirm"], help="确认提交答案")] = False,
    concurrency: Annotated[int, Parameter(name=["--concurrency"], help="并发提交数（默认 1，强制单线程顺序作答）")] = 1,
    delay: Annotated[
        float,
        Parameter(
            name=["--delay"],
            help="单题基础间隔秒数（默认 8.0s；建议依据难易度动态调整：简单题 5~10s，多选 15~35s，主观题 45~90s，模拟真人思考）",
        ),
    ] = 8.0,
    delay_jitter: Annotated[
        float,
        Parameter(
            name=["--delay-jitter"],
            help="提交间隔随机浮动秒数（默认 3.0s；实际间隔为 delay ± jitter，消除固定机器周期）",
        ),
    ] = 3.0,
    random_order: Annotated[
        bool,
        Parameter(
            name=["--random-order"],
            help="是否打乱题目提交次序（默认 False；建议批量作业开启，打破从第 1 题顺序作答的机械规律）",
        ),
    ] = False,
    platform: Annotated[PlatformCode | None, Parameter(name=["--platform", "-p"], help="平台代码")] = None,
    account: Annotated[str | None, Parameter(name=["--account", "-a"], help="账号 ID")] = None,
) -> CommandResult[HomeworkSubmitPreview] | CommandResult[HomeworkSubmitResult]:
    """提交作业答案集合。建议依据题目难易程度动态设置 `--delay`、`--delay-jitter` 与 `--random-order` 模拟真人思考作答。"""
    if answers is not None and answers_file is not None:
        raise ValueError("`--answers` 与 `--answers-file` 只能提供其一")
    if answers is not None:
        raw_answers = parse_json_text(answers)
    elif answers_file is not None:
        raw_answers = _read_json_file_or_stdin(answers_file)
    else:
        raise ValueError("提交作业必须通过 `--answers` 或 `--answers-file` 至少提供一种答案输入")

    answers_map = _parse_answers_payload(raw_answers)
    if not answers_map:
        raise ValueError("答案内容为空或格式无效，未提取到任何题目的有效答案")

    async with _open_course(course, platform, account) as (plat, matched):
        # 校验题目归属与选项合法性
        questions = await plat.get_leaf_questions(homework, matched)
        questions_by_id: dict[str, dict[str, Any]] = {
            str(question.get("problem_id") or question.get("id")): question for question in questions
        }
        for question_id, answer_choices in answers_map.items():
            question = questions_by_id.get(question_id)
            if question is None:
                raise ValueError(f"题目 ID '{question_id}' 不属于作业 '{homework}'，可用题目 ID: {sorted(questions_by_id)}")
            options = question.get("content", {}).get("Options") or []
            valid_keys = {str(option.get("key")) for option in options if option.get("key")}
            if valid_keys:
                for option_value in answer_choices:
                    if option_value not in valid_keys:
                        raise ValueError(f"题目 '{question_id}' 的答案选项 '{option_value}' 无效，合法选项为: {sorted(valid_keys)}")

        if dry_run:
            return CommandResult[HomeworkSubmitPreview](
                data=HomeworkSubmitPreview(
                    dry_run=True,
                    course_id=str(matched.id),
                    homework_id=str(homework),
                    answers_count=len(answers_map),
                    validated=True,
                )
            )

        if not confirm:
            raise ConfirmationRequiredError("作业提交必须显式附加 `--confirm` 参数")

        result = await run_homework_submit(
            plat,
            matched,
            Homework(id=str(homework), name=str(homework)),
            answers=answers_map,
            concurrency=concurrency,
            delay=delay,
            delay_jitter=delay_jitter,
            random_order=random_order,
        )
        if result.submitted_count < result.total:
            raise PartialFailureError("部分题目提交失败", data=result)
        return CommandResult[HomeworkSubmitResult](data=result)


homework_list = homework_list_cmd
homework_inspect = homework_inspect_cmd
homework_submit = homework_submit_cmd

__all__ = [
    "homework_app",
    "homework_inspect",
    "homework_inspect_cmd",
    "homework_list",
    "homework_list_cmd",
    "homework_submit",
    "homework_submit_cmd",
]
