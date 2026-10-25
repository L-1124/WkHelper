"""单题检索与课程题目导出命令。"""

from pathlib import Path
from typing import Annotated, Literal

from cyclopts import App, Parameter

from wkhelper.cli.context import PlatformCode
from wkhelper.cli.deps import _open_course, _paginate
from wkhelper.cli.envelope import CommandResult
from wkhelper.cli.inputs import Limit, validate_positive_limit
from wkhelper.domain.errors import APIError, ConfirmationRequiredError
from wkhelper.domain.exporting import ExportResult, OutputFileResult
from wkhelper.domain.homework import (
    Homework,
    OptionItem,
    QuestionDetail,
    QuestionListData,
    QuestionSubmitPreview,
    QuestionSubmitResult,
    QuestionSummary,
)
from wkhelper.domain.questions import normalize_judgement_answer, question_type
from wkhelper.exporting.markdown import html_to_md
from wkhelper.exporting.questions import export_questions
from wkhelper.exporting.resources import localize_question_detail
from wkhelper.homework.service import submit_question
from wkhelper.infra.serialization import encode_json
from wkhelper.platform.parsing import PlatformCounters

question_app = App(name="question", help="题目查询与单题提交")
questions_app = App(name="questions", help="课程作业题目批量导出")


def _type_text(question: dict[str, object]) -> str | None:
    """题型对外展示：平台只在 `content.ProblemType` 给出题型。"""
    raw = question_type(question)  # type: ignore[arg-type]
    return str(raw) if raw is not None else None


@question_app.command(name="list")
async def question_list_cmd(
    course: Annotated[str, Parameter(name=["--course", "-c"], help="课程 ID")],
    homework: Annotated[str, Parameter(name=["--homework", "-w"], help="作业 ID")],
    limit: Limit = 10,
    cursor: Annotated[str | None, Parameter(name=["--cursor"], help="分页游标")] = None,
    platform: Annotated[PlatformCode | None, Parameter(name=["--platform", "-p"], help="平台代码")] = None,
    account: Annotated[str | None, Parameter(name=["--account", "-a"], help="账号 ID")] = None,
) -> CommandResult[QuestionListData]:
    """列出作业题目列表与作答状态。"""
    validate_positive_limit(int, limit)
    async with _open_course(course, platform, account) as (plat, matched):
        questions = await plat.get_leaf_questions(homework, matched)

        items: list[QuestionSummary] = []
        for index, question in enumerate(questions, 1):
            content = question.get("content", {})
            body = html_to_md(content.get("Body") or content.get("body") or "")
            options = content.get("Options") or []
            user_info = question.get("user", {})
            user_answer = user_info.get("answer")
            attempt_count = PlatformCounters.model_validate(user_info).my_count
            items.append(
                QuestionSummary(
                    index=index,
                    id=str(question.get("problem_id") or question.get("id")),
                    type=_type_text(question),
                    prompt=body[:200] + ("..." if len(body) > 200 else ""),
                    content_truncated=len(body) > 200,
                    options=[OptionItem(key=option.get("key"), text=html_to_md(option.get("value", ""))) for option in options],
                    submitted=bool(user_answer or attempt_count > 0),
                    is_correct=user_info.get("is_right") if user_info.get("is_right") is not None else user_info.get("is_correct"),
                )
            )

        page_items, meta = _paginate(items, limit, cursor)
        return CommandResult(data=QuestionListData(questions=page_items), meta=meta.model_dump())


@question_app.command(name="get")
async def question_get_cmd(
    course: Annotated[str, Parameter(name=["--course", "-c"], help="课程 ID")],
    homework: Annotated[str, Parameter(name=["--homework", "-w"], help="作业 ID")],
    question: Annotated[str, Parameter(name=["--question", "-q"], help="题目 ID")],
    output: Annotated[str | None, Parameter(name=["--output", "-o"], help="输出文件路径")] = None,
    download_resources: Annotated[
        bool, Parameter(name=["--download-resources", "--download-images"], help="是否下载题干配图与附件")
    ] = False,
    assets_dir: Annotated[str | None, Parameter(name=["--assets-dir"], help="资源保存目录路径")] = None,
    platform: Annotated[PlatformCode | None, Parameter(name=["--platform", "-p"], help="平台代码")] = None,
    account: Annotated[str | None, Parameter(name=["--account", "-a"], help="账号 ID")] = None,
) -> CommandResult[QuestionDetail] | CommandResult[OutputFileResult]:
    """查看单道题目的完整内容与作答结果。"""
    async with _open_course(course, platform, account) as (plat, matched):
        questions = await plat.get_leaf_questions(homework, matched)
        target_question = next((item for item in questions if str(item.get("problem_id") or item.get("id")) == str(question)), None)
        if target_question is None:
            raise KeyError(f"未找到题目 ID: {question}")

        content = target_question.get("content", {})
        body = html_to_md(content.get("Body") or content.get("body") or "")
        options = content.get("Options") or []
        user_info = target_question.get("user", {})
        attempt_count = PlatformCounters.model_validate(user_info).my_count
        detail = QuestionDetail(
            id=str(question),
            type=_type_text(target_question),
            prompt=body,
            options=[OptionItem(key=option.get("key"), text=html_to_md(option.get("value", ""))) for option in options],
            submitted=bool(user_info.get("answer") or attempt_count > 0),
            user_answer=user_info.get("answer"),
            is_correct=user_info.get("is_right") if user_info.get("is_right") is not None else user_info.get("is_correct"),
            score=user_info.get("score") if user_info.get("score") is not None else user_info.get("my_score"),
            attempt_count=attempt_count,
            library_id=content.get("LibraryID") or content.get("library_id"),
            version=content.get("Version"),
        )
        if download_resources:
            target_assets_dir = Path(assets_dir) if assets_dir else (Path(output).parent / "assets" if output else Path("./assets"))
            detail = await localize_question_detail(plat.fetch_bytes, detail, target_assets_dir)

        if output is not None:
            Path(output).write_bytes(encode_json(detail))
            return CommandResult[OutputFileResult](data=OutputFileResult(output_file=output, written=True))
        return CommandResult[QuestionDetail](data=detail)


@question_app.command(name="submit")
async def question_submit_cmd(
    course: Annotated[str, Parameter(name=["--course", "-c"], help="课程 ID")],
    homework: Annotated[str, Parameter(name=["--homework", "-w"], help="作业 ID")],
    question: Annotated[str, Parameter(name=["--question", "-q"], help="题目 ID")],
    answer: Annotated[str, Parameter(name=["--answer", "-a"], help="答案选项 (例如 'A' 或 'A,B')")],
    delay: Annotated[
        float,
        Parameter(
            name=["--delay"],
            help="提交前等待秒数",
        ),
    ] = 0.0,
    delay_jitter: Annotated[
        float,
        Parameter(
            name=["--delay-jitter"],
            help="等待时间随机抖动秒数 (实际等待 delay ± jitter)",
        ),
    ] = 0.0,
    dry_run: Annotated[bool, Parameter(name=["--dry-run"], help="校验参数，不提交")] = False,
    confirm: Annotated[bool, Parameter(name=["--confirm"], help="确认提交答案")] = False,
    platform: Annotated[PlatformCode | None, Parameter(name=["--platform", "-p"], help="平台代码")] = None,
    account: Annotated[str | None, Parameter(name=["--account"], help="账号 ID")] = None,
) -> CommandResult[QuestionSubmitPreview] | CommandResult[QuestionSubmitResult]:
    """提交单道题目答案。"""
    answer_list = [item.strip() for item in answer.split(",") if item.strip()]
    if not answer_list:
        raise ValueError(f"题目答案参数无效: {answer}")

    async with _open_course(course, platform, account) as (plat, matched):
        # 校验题目存在性与选项合法性
        questions = await plat.get_leaf_questions(homework, matched)
        target_question = next((item for item in questions if str(item.get("problem_id") or item.get("id")) == str(question)), None)
        if target_question is None:
            raise KeyError(f"题目 ID '{question}' 不属于作业 '{homework}'")
        answer_list = normalize_judgement_answer(target_question, answer_list)
        options = target_question.get("content", {}).get("Options") or []
        valid_keys = {str(option.get("key")) for option in options if option.get("key")}
        if valid_keys:
            for option_value in answer_list:
                if option_value not in valid_keys:
                    raise ValueError(f"题目 '{question}' 的答案选项 '{option_value}' 无效，合法选项为: {sorted(valid_keys)}")

        if dry_run:
            return CommandResult[QuestionSubmitPreview](
                data=QuestionSubmitPreview(
                    dry_run=True,
                    course_id=str(matched.id),
                    homework_id=str(homework),
                    question_id=str(question),
                    answer=answer_list,
                )
            )

        if not confirm:
            raise ConfirmationRequiredError("题目提交必须显式附加 `--confirm` 参数")

        if delay > 0:
            import asyncio
            import random

            actual_delay = max(0.05, delay + random.uniform(-delay_jitter, delay_jitter) if delay_jitter > 0 else delay)
            await asyncio.sleep(actual_delay)
        result = await submit_question(
            plat,
            Homework(id=str(homework), name=str(homework)),
            matched,
            question,
            answer_list,
        )
        if not result.success:
            err_msg = result.error or "题目提交失败"
            raise APIError(f"题目提交失败: {err_msg}")
        return CommandResult[QuestionSubmitResult](data=result)


@questions_app.command(name="export")
async def questions_export_cmd(
    course: Annotated[str, Parameter(name=["--course", "-c"], help="课程 ID")],
    output: Annotated[str, Parameter(name=["--output", "-o"], help="输出文件路径")],
    format: Annotated[
        Literal["md", "markdown", "json"] | None, Parameter(name=["--format"], help="导出格式 (缺省根据 output 后缀推断，默认 md)")
    ] = None,
    download_resources: Annotated[
        bool, Parameter(name=["--download-resources", "--download-images"], help="是否下载题干配图与附件并转为相对路径")
    ] = False,
    only_correct_answers: Annotated[bool, Parameter(name=["--only-correct-answers"], help="仅导出标准答案 (排除用户作答记录)")] = False,
    platform: Annotated[PlatformCode | None, Parameter(name=["--platform", "-p"], help="平台代码")] = None,
    account: Annotated[str | None, Parameter(name=["--account", "-a"], help="账号 ID")] = None,
) -> CommandResult[ExportResult]:
    """将课程所有作业题目导出为 Markdown 或 JSON 文件。"""
    async with _open_course(course, platform, account) as (plat, matched):
        return CommandResult(
            data=await export_questions(
                plat,
                matched,
                output_path=output,
                download_resources=download_resources,
                format=format,
                only_correct_answers=only_correct_answers,
            )
        )


question_list = question_list_cmd
question_get = question_get_cmd
question_submit = question_submit_cmd
questions_export = questions_export_cmd

__all__ = [
    "question_app",
    "question_get",
    "question_get_cmd",
    "question_list",
    "question_list_cmd",
    "question_submit",
    "question_submit_cmd",
    "questions_app",
    "questions_export",
    "questions_export_cmd",
]
