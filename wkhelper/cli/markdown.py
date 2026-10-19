"""CLI 终端 Markdown 展现层。

设计约束：

- 每个领域结果模型通过 `@renderer(...)` 显式登记渲染函数；漏登记由
  `tests/cli/test_format_selection.py` 的全模型覆盖用例直接失败，不在运行时静默降级。
- 所有表格单元格统一经 `_cell()` 转义，避免平台文案里的 `|` 或换行破坏 GFM 表格结构。
- 本模块只负责展现，不修改任何数据契约。
"""

from collections.abc import Callable, Sequence
from typing import cast

import msgspec

from wkhelper.cli.envelope import NextAction
from wkhelper.core.entities import StorageStatus
from wkhelper.core.json_types import JsonDict, json_int
from wkhelper.core.results import (
    AccountListData,
    ArticleListData,
    ArticleReadResult,
    AuthStatusResult,
    CourseDetail,
    CourseListData,
    DiscussionCommentResult,
    DiscussionListData,
    DiscussionTopicDetail,
    ExamAnswerResult,
    ExamBrowserCloseResult,
    ExamCoverDetail,
    ExamExportResult,
    ExamListData,
    ExamPaperData,
    ExamResultData,
    ExamStartPreview,
    ExamStartResult,
    ExamStatusData,
    ExamSubmitResult,
    ExportResult,
    HomeworkListData,
    HomeworkOutline,
    HomeworkSubmitPreview,
    HomeworkSubmitResult,
    LoginResult,
    NoticeDetail,
    NoticeListData,
    OutputFileResult,
    QuestionDetail,
    QuestionListData,
    QuestionSubmitPreview,
    QuestionSubmitResult,
    RemoveResult,
    StatusData,
    VideoLearnPreview,
    VideoListData,
    VideoPlanSummary,
    VideoProgress,
)
from wkhelper.core.time_types import EpochMillis

type Renderer = Callable[[object], list[str]]

_RENDERERS: dict[type, Renderer] = {}


def renderer[T](model: type[T]) -> Callable[[Callable[[T], list[str]]], Callable[[T], list[str]]]:
    """把结果模型的 Markdown 渲染函数登记到注册表。"""

    def register(func: Callable[[T], list[str]]) -> Callable[[T], list[str]]:
        _RENDERERS[model] = cast(Renderer, func)
        return func

    return register


def _cell(value: object) -> str:
    """转义 GFM 表格单元格：竖线转义为 `\\|`，换行折叠为空格，空值显示为 `-`。"""
    if value is None:
        return "-"
    text = str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ").strip()
    return text or "-"


def _table(headers: list[str], rows: Sequence[Sequence[object]]) -> list[str]:
    """构造 GFM 表格；空数据集给出显式提示而不是空表。"""
    lines = [
        "| " + " | ".join(_cell(header) for header in headers) + " |",
        "|" + "|".join(":---" for _ in headers) + "|",
    ]
    if not rows:
        return [*lines, "", "> 暂无数据"]
    lines.extend("| " + " | ".join(_cell(cell) for cell in row) + " |" for row in rows)
    return lines


def _format_next_actions(actions: list[NextAction] | None) -> list[str]:
    """渲染 Agent 下一步动作建议。"""
    if not actions:
        return []
    lines = ["", "### 下一步建议", ""]
    for action in actions:
        reason = _cell(action.reason)
        if action.argv:
            cmd = " ".join(action.argv)
            lines.append(f"- **{reason}**：`{cmd}`")
        elif action.description:
            lines.append(f"- **{reason}**：{_cell(action.description)}")
        else:
            lines.append(f"- **{reason}**")
    return lines


def _pagination_hint(meta: JsonDict) -> str:
    """生成分页提示行；首页且无更多数据时返回空串。"""
    offset = json_int(meta.get("offset"), 0)
    limit = json_int(meta.get("limit"), 10)
    total = json_int(meta.get("total"), 0)
    has_more = bool(meta.get("has_more", False))
    next_cursor = meta.get("next_cursor")
    if not has_more and offset <= 0:
        return ""

    start_idx = offset + 1
    end_idx = min(total, offset + limit) if total > 0 else offset + limit
    total_str = f"，共 {total} 条" if total > 0 else ""
    cursor_str = f" (下一页参数: `--cursor {next_cursor}`)" if next_cursor not in (None, "") else ""
    return f"*分页提示: 显示第 {start_idx}~{end_idx} 条{total_str}{cursor_str}*"


def _render_fallback(data: object) -> list[str]:
    """未登记模型的兜底渲染：优先结构化 JSON，不可序列化时降级为原始文本。"""
    try:
        payload = msgspec.to_builtins(data)
        return ["```json", msgspec.json.format(msgspec.json.encode(payload), indent=2).decode("utf-8"), "```"]
    except (TypeError, ValueError):
        return [f"> 无法结构化渲染：`{_cell(data)}`"]


@renderer(CourseListData)
def _render_course_list(data: CourseListData) -> list[str]:
    rows = [[i, c.id, c.name, c.platform, c.classroom_id] for i, c in enumerate(data.courses, 1)]
    return [f"## 课程列表 (共 {len(data.courses)} 门)", "", *_table(["序号", "课程引用 (ID)", "课程名称", "平台", "课堂 ID"], rows)]


@renderer(CourseDetail)
def _render_course_detail(data: CourseDetail) -> list[str]:
    lines = [
        f"## 课程详情：{_cell(data.name)}",
        "",
        f"- **课程引用 ID**：`{data.id}`",
        f"- **所属平台**：`{data.platform}`",
    ]
    if data.metadata:
        lines.extend(["", "### 元数据"])
        lines.extend(f"- **{_cell(key)}**：`{_cell(value)}`" for key, value in sorted(data.metadata.items()))
    return lines


@renderer(VideoListData)
def _render_video_list(data: VideoListData) -> list[str]:
    rows = [[i, video.id, video.name, "已完成" if video.completed else "未完成"] for i, video in enumerate(data.videos, 1)]
    return [f"## 视频列表 (共 {len(data.videos)} 个)", "", *_table(["序号", "视频 ID", "视频节点名称", "状态"], rows)]


@renderer(VideoProgress)
def _render_video_progress(data: VideoProgress) -> list[str]:
    tag = "已完成" if data.completed else "进行中"
    return [
        f"## 视频进度：`{data.video_id}`",
        "",
        f"- **课程 ID**：`{data.course_id}`",
        f"- **观看进度**：**{data.current_rate * 100:.1f}%** ({tag})",
    ]


@renderer(VideoPlanSummary)
def _render_video_plan_summary(data: VideoPlanSummary) -> list[str]:
    rows = [
        [
            result.video_id,
            f"{result.initial_rate * 100:.1f}%",
            f"{result.final_rate * 100:.1f}%",
            result.status,
            f"{result.duration_seconds:.1f}s",
            result.message,
        ]
        for result in data.results
    ]
    return [
        "## 视频计划学习汇总",
        "",
        f"- **目标总数**：{data.total} | **已完成**：{data.completed} | **失败/中断**：{data.failed}",
        "",
        *_table(["视频 ID", "初始进度", "最终进度", "状态", "耗时", "详细说明"], rows),
    ]


@renderer(VideoLearnPreview)
def _render_video_learn_preview(data: VideoLearnPreview) -> list[str]:
    rows = [[i, target.course_id, target.video_id] for i, target in enumerate(data.targets, 1)]
    return [
        "## 视频学习计划预览",
        "",
        f"> {_cell(data.note)}",
        "",
        f"- **目标视频数**：{data.targets_count}",
        f"- **执行策略**：并发 `{data.effective_config.concurrency}` | 出错策略 `{data.effective_config.on_error}`",
        "",
        *_table(["序号", "课程 ID", "视频 ID"], rows),
    ]


@renderer(HomeworkListData)
def _render_homework_list(data: HomeworkListData) -> list[str]:
    rows = [
        [i, homework.id, homework.name, homework.deadline or "无限制", "已完成" if homework.completed else "未完成"]
        for i, homework in enumerate(data.homeworks, 1)
    ]
    return [f"## 作业列表 (共 {len(data.homeworks)} 项)", "", *_table(["序号", "作业 ID", "作业名称", "截止时间", "完成状态"], rows)]


@renderer(HomeworkOutline)
def _render_homework_outline(data: HomeworkOutline) -> list[str]:
    rows = [[q.index, q.id, q.type or "客观题", q.library_id] for q in data.questions_summary]
    return [
        f"## 作业题目大纲：{_cell(data.homework_id)}",
        "",
        f"- **课程 ID**：`{data.course_id}` | **作业 ID**：`{data.homework_id}`",
        f"- **题目总数**：{data.total_questions}",
        "",
        *_table(["题号", "题目 ID", "题型", "题库 ID"], rows),
    ]


@renderer(HomeworkSubmitResult)
def _render_homework_submit_result(data: HomeworkSubmitResult) -> list[str]:
    rows = [
        [
            item.index,
            item.question_id,
            "成功" if item.submitted else "失败",
            "正确" if item.is_correct else ("错误" if item.submitted else "-"),
            ", ".join(item.correct_answer) if item.correct_answer else "-",
            item.error or "-",
        ]
        for item in data.items
    ]
    return [
        "## 作业提交结果",
        "",
        f"- **总题数**：{data.total} | **成功提交**：{data.submitted_count} | **正确题数**：{data.correct_count}",
        "",
        *_table(["序号", "题目 ID", "提交状态", "判题结果", "正确答案", "提示信息"], rows),
    ]


@renderer(HomeworkSubmitPreview)
def _render_homework_submit_preview(data: HomeworkSubmitPreview) -> list[str]:
    return [
        "## 作业答案提交预检",
        "",
        f"- **课程 ID**：`{data.course_id}` | **作业 ID**：`{data.homework_id}`",
        f"- **拟提交题目数**：{data.answers_count} | **合法性校验**：{'通过' if data.validated else '未通过'}",
    ]


@renderer(QuestionListData)
def _render_question_list(data: QuestionListData) -> list[str]:
    rows = []
    for question in data.questions:
        submitted = "已作答" if question.submitted else "未作答"
        correct = "正确" if question.is_correct is True else ("错误" if question.is_correct is False else "-")
        rows.append([question.index, question.id, question.prompt, submitted, correct])
    return [
        f"## 作业题目列表 (共 {len(data.questions)} 题)",
        "",
        *_table(["题号", "题目 ID", "题干摘要", "作答状态", "正确状态"], rows),
    ]


@renderer(QuestionDetail)
def _render_question_detail(data: QuestionDetail) -> list[str]:
    lines = [f"## 题目详情 [ID: {data.id}]", "", "### 题干", data.prompt.strip(), ""]
    if data.options:
        lines.append("### 选项")
        lines.extend(f"- **{_cell(option.key)}**: {option.text}" for option in data.options)
        lines.append("")

    submitted = "已作答" if data.submitted else "未作答"
    correct = "正确" if data.is_correct is True else ("错误" if data.is_correct is False else "待判")
    answer = str(data.user_answer) if data.user_answer is not None else "无"
    score = str(data.score) if data.score is not None else "-"
    lines.append(f"- **状态**：{submitted} (我的作答: `{answer}`, 判定: {correct}, 得分: {score})")
    lines.append(f"- **尝试次数**：{data.attempt_count}")
    if data.resources:
        lines.append(f"- **本地资源**：{len(data.resources)} 个文件已下载到本地")
    return lines


@renderer(QuestionSubmitResult)
def _render_question_submit_result(data: QuestionSubmitResult) -> list[str]:
    lines = [
        f"## 单题提交结果：`{data.question_id}`",
        "",
        f"- **提交选项**：`{', '.join(data.submitted_answer)}`",
        f"- **提交成功**：{'是' if data.success else '否'}",
        f"- **判题结果**：{'正确' if data.is_correct else '错误'}",
    ]
    if data.correct_answer:
        lines.append(f"- **正确答案**：`{', '.join(data.correct_answer)}`")
    if data.error:
        lines.append(f"- **提示信息**：{_cell(data.error)}")
    return lines


@renderer(QuestionSubmitPreview)
def _render_question_submit_preview(data: QuestionSubmitPreview) -> list[str]:
    return [
        f"## 单题提交预检：`{data.question_id}`",
        "",
        f"- **课程 ID**：`{data.course_id}` | **作业 ID**：`{data.homework_id}`",
        f"- **拟提交选项**：`{', '.join(data.answer)}`",
    ]


@renderer(ArticleListData)
def _render_article_list(data: ArticleListData) -> list[str]:
    rows = [[i, article.id, article.name, "已读" if article.completed else "未读"] for i, article in enumerate(data.articles, 1)]
    return [f"## 资料/图文列表 (共 {len(data.articles)} 项)", "", *_table(["序号", "资料 ID", "节点名称", "阅读状态"], rows)]


@renderer(ArticleReadResult)
def _render_article_read_result(data: ArticleReadResult) -> list[str]:
    return [
        "## 资料阅读结果",
        "",
        f"- **资料节点**：`{data.leaf_id}` ({_cell(data.name)})",
        f"- **完成状态**：{'已完成' if data.completed else '未完成'}",
    ]


@renderer(DiscussionListData)
def _render_discussion_list(data: DiscussionListData) -> list[str]:
    rows = [[i, topic.id, topic.name, "已发言" if topic.completed else "未发言"] for i, topic in enumerate(data.discussions, 1)]
    return [f"## 讨论题列表 (共 {len(data.discussions)} 题)", "", *_table(["序号", "讨论 ID", "节点名称", "发言状态"], rows)]


@renderer(DiscussionTopicDetail)
def _render_discussion_topic_detail(data: DiscussionTopicDetail) -> list[str]:
    tag = "已发言" if data.completed else "未发言"
    return [
        f"## 讨论题详情：{_cell(data.title)}",
        "",
        f"- **节点 ID**：`{data.leaf_id}` | **Topic ID**：`{data.topic_id}`",
        f"- **发言状态**：{tag} (我的发言数: {data.user_comment_num})",
        f"- **全班参与人数**：{data.comment_count} 人",
        "",
        "### 教师要求与引导题干",
        data.prompt.strip(),
    ]


@renderer(DiscussionCommentResult)
def _render_discussion_comment_result(data: DiscussionCommentResult) -> list[str]:
    lines = [
        f"## 讨论回复提交：{'成功' if data.success else '失败'}",
        "",
        f"- **节点 ID**：`{data.leaf_id}` | **Topic ID**：`{data.topic_id}`",
    ]
    if data.comment_id:
        lines.append(f"- **回复记录 ID**：`{data.comment_id}`")
    lines.append(f"- **回执信息**：{_cell(data.message)}")
    return lines


@renderer(StatusData)
def _render_status(data: StatusData) -> list[str]:
    storage = data.credential_storage
    lines = [
        "## 本地运行环境与账号概况",
        "",
        f"- **凭据保管模式**：`{storage.mode}` ({storage.backend})",
        f"- **数据存储目录**：`{storage.data_dir}`",
        f"- **已保存账号数**：{data.accounts_count}",
        "",
    ]
    rows = [[account.platform, account.user_id, account.name, account.school] for account in data.accounts]
    lines.append("### 已绑定账号")
    lines.extend(_table(["平台", "账号 ID", "用户姓名", "院校归属"], rows))
    return lines


@renderer(AccountListData)
def _render_account_list(data: AccountListData) -> list[str]:
    rows = [[account.platform, account.user_id, account.name, account.school] for account in data.accounts]
    return [
        f"## 已保存账号列表 (共 {len(data.accounts)} 个)",
        "",
        *_table(["平台", "账号 ID", "姓名", "院校归属"], rows),
    ]


@renderer(AuthStatusResult)
def _render_auth_status(data: AuthStatusResult) -> list[str]:
    return [
        f"## 账号登录态验证：{'有效' if data.valid else '无效/已过期'}",
        "",
        f"- **平台**：`{data.platform}` | **账号 ID**：`{data.user_id}`",
        f"- **用户姓名**：{_cell(data.name)}",
    ]


@renderer(LoginResult)
def _render_login_result(data: LoginResult) -> list[str]:
    return [
        "## 登录成功",
        "",
        f"- **平台**：`{data.platform}` | **账号 ID**：`{data.user_id}`",
        f"- **用户姓名**：{_cell(data.name)} ({_cell(data.school) if data.school else '无院校'})",
        f"- **凭据持久化**：{'已存入系统安全库' if data.persisted else '仅当前会话有效'}",
    ]


@renderer(RemoveResult)
def _render_remove_result(data: RemoveResult) -> list[str]:
    return [
        f"## 账号凭据删除：{'成功' if data.deleted else '未找到'}",
        "",
        f"- **平台**：`{data.platform}` | **账号 ID**：`{data.account}`",
    ]


@renderer(StorageStatus)
def _render_storage_status(data: StorageStatus) -> list[str]:
    return [
        "## 系统凭据后端状态",
        "",
        f"- **模式**：`{data.mode}` ({data.backend})",
        f"- **可用性**：{'正常' if data.available else '不可用'} | **优先级**：{data.priority}",
        f"- **数据目录**：`{data.data_dir}`",
        f"- **元数据数据库**：`{data.metadata_db}`",
    ]


@renderer(ExportResult)
def _render_export_result(data: ExportResult) -> list[str]:
    resources = f" | **本地资源**：{data.resources_count} 个文件" if data.resources_count > 0 else ""
    return [
        "## 题目导出完成",
        "",
        f"- **导出文件路径**：`{_cell(data.file_path)}`",
        f"- **包含作业数**：{data.homeworks_count} 项 | **总题目数**：{data.questions_count} 题{resources}",
    ]


@renderer(OutputFileResult)
def _render_output_file_result(data: OutputFileResult) -> list[str]:
    return ["## 文件写入完成", "", f"- **输出路径**：`{data.output_file}`"]


def format_error_markdown(
    code: str,
    message: str,
    details: JsonDict | None = None,
    next_actions: list[NextAction] | None = None,
) -> str:
    """格式化错误响应为 Markdown。"""
    quoted = str(message).replace("\r", "").replace("\n", "\n> ")
    lines = [f"## 操作失败 [{_cell(code)}]", "", f"> **错误信息**：{quoted}", ""]
    if details:
        lines.append("### 详细诊断")
        if "exception_type" in details:
            lines.append(f"- **异常类型**：`{details['exception_type']}`")
        traceback_items = details.get("traceback")
        if isinstance(traceback_items, list):
            lines.extend(["```text", *(str(item) for item in traceback_items), "```"])
        others = {key: value for key, value in details.items() if key not in ("exception_type", "traceback")}
        if others:
            payload = msgspec.json.encode(others).decode("utf-8")
            lines.append(f"- **附加详情**：`{payload}`")
        lines.append("")

    return "\n".join([*lines, *_format_next_actions(next_actions)]).strip()


def format_result_markdown(
    data: object,
    meta: JsonDict | None = None,
    next_actions: list[NextAction] | None = None,
) -> str:
    """将强类型领域结果数据格式化为适合终端阅读的 GFM Markdown。"""
    render = _RENDERERS.get(type(data))
    lines = render(data) if render is not None else _render_fallback(data)
    if meta:
        hint = _pagination_hint(meta)
        if hint:
            lines.extend(["", hint])
    lines.extend(_format_next_actions(next_actions))
    return "\n".join(lines).strip()


def _format_time_str(ts: EpochMillis | None) -> str:
    if not ts:
        return "无限制"
    import datetime

    return datetime.datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d %H:%M")


@renderer(ExamListData)
def _render_exam_list(data: ExamListData) -> list[str]:
    rows = [
        [
            i,
            exam.id,
            exam.name,
            _format_time_str(exam.start_time),
            _format_time_str(exam.deadline),
            exam.status or ("已完成" if exam.completed else ("已缺考" if exam.overdue else "未完成")),
        ]
        for i, exam in enumerate(data.exams, 1)
    ]
    return [f"## 考试列表 (共 {len(data.exams)} 场)", "", *_table(["序号", "考试 ID", "考试名称", "开考时间", "截止时间", "状态"], rows)]


@renderer(ExamCoverDetail)
def _render_exam_cover(data: ExamCoverDetail) -> list[str]:
    lines = [
        f"## 考试详情：{_cell(data.title)}",
        "",
        f"- **考试节点 ID**：`{data.exam_id}`",
        f"- **考场状态**：`{data.status}`",
        f"- **题目数量**：{data.problem_count} 题",
        f"- **试卷满分**：{data.total_score} 分",
        f"- **考试限时**：{f'{data.limit_minutes} 分钟' if data.has_limit else '不限时（无倒计时，仅受截止时间约束）'}",
        f"- **开考时间**：{_format_time_str(data.start_time)}",
        f"- **截止时间**：{_format_time_str(data.deadline)}",
        f"- **在线监考**：{'是' if data.online_proctor else '否'}",
        f"- **身份核验（人脸/证件）**：{'是' if data.identity_auth else '否'}",
        f"- **Web 桌面截屏**：{'是（需共享整个屏幕）' if data.capture_screen else '否'}",
        f"- **App 端截屏**：{'是' if data.app_capture_screen else '否'}",
        f"- **App 前置随机抓拍**：{'是' if data.front_random_snap else '否'}",
        f"- **切屏检测**：{f'开启（上限 {data.max_screen_cuts} 次）' if data.screen_cuts else '关闭'}",
        f"- **作答次数**：{'不限次数' if data.max_retry < 0 else f'最多 {data.max_retry} 次'}",
        f"- **线下考试**：{'是' if data.is_offline else '否'}",
    ]
    if data.description:
        lines.extend(["", "### 考试须知", data.description])
    return lines


@renderer(ExamStartPreview)
def _render_exam_start_preview(data: ExamStartPreview) -> list[str]:
    return [
        f"## 考场进入预检：{_cell(data.title)}",
        "",
        f"- **考试节点 ID**：`{data.exam_id}`",
        f"- **考试限时**：{f'{data.limit_minutes} 分钟' if data.limit_minutes > 0 else '不限时'}",
        f"- **注意事项**：{data.note}",
        *([f"> **注意**：{data.notice}"] if data.notice else []),
    ]


@renderer(ExamStartResult)
def _render_exam_start_result(data: ExamStartResult) -> list[str]:
    return [
        f"## 考场会话已开启：{_cell(data.title)}",
        "",
        f"- **考试节点 ID**：`{data.exam_id}`",
        f"- **进入状态**：{'成功' if data.started else '失败'}",
        f"- **考试限时**：{f'{data.limit_minutes} 分钟' if data.limit_minutes > 0 else '不限时'}",
        f"- **考场直达地址**：<{data.web_url}>",
        f"- **提示信息**：{data.message}",
        *([f"> **注意**：{data.notice}"] if data.notice else []),
    ]


@renderer(ExamPaperData)
def _render_exam_paper(data: ExamPaperData) -> list[str]:
    rows = [
        [
            problem.index,
            problem.id,
            problem.type or "-",
            problem.score,
            problem.prompt,
            ",".join(problem.answer) if problem.answer else "-",
        ]
        for problem in data.problems
    ]
    return [
        f"## 考场试卷：{_cell(data.title)} (共 {len(data.problems)} 题)",
        "",
        *_table(["序号", "题目 ID", "题型", "分值", "题干摘要", "平台答案"], rows),
    ]


@renderer(ExamExportResult)
def _render_exam_export(data: ExamExportResult) -> list[str]:
    return [
        "## 考场试卷导出完成",
        "",
        f"- **文件路径**：`{data.file_path}`",
        f"- **考试 ID**：`{data.exam_id}`",
        f"- **题目数量**：{data.problems_count} 题",
        f"- **下载配图**：{data.resources_count} 个",
    ]


@renderer(ExamAnswerResult)
def _render_exam_answer(data: ExamAnswerResult) -> list[str]:
    return [
        f"## 考试作答：题目 `{data.problem_id}`",
        "",
        f"- **考试 ID**：`{data.exam_id}`",
        f"- **作答内容**：{'、'.join(data.answer) if data.answer else '-'}",
        f"- **写入状态**：{'已保存' if data.saved else ('预览未写入' if data.dry_run else '保存失败')}",
        f"- **提示信息**：{data.message}",
        *([f"> **注意**：{data.notice}"] if data.notice else []),
    ]


@renderer(ExamSubmitResult)
def _render_exam_submit(data: ExamSubmitResult) -> list[str]:
    lines = [
        f"## 考试交卷：`{data.exam_id}`",
        "",
        f"- **已作答题数**：{data.answered_count}",
        f"- **未作答题数**：{len(data.unanswered)}",
        f"- **交卷状态**：{'已交卷' if data.submitted else ('预览未交卷' if data.dry_run else '交卷失败')}",
        f"- **提示信息**：{data.message}",
    ]
    if data.unanswered:
        lines.append(f"- **未作答题目**：{'、'.join(data.unanswered)}")
    if data.notice:
        lines.append(f"> **注意**：{data.notice}")
    return lines


@renderer(ExamBrowserCloseResult)
def _render_exam_browser_close(data: ExamBrowserCloseResult) -> list[str]:
    return [
        "## 考场浏览器会话",
        "",
        f"- **关联考试**：{data.exam_id or '-'}",
        f"- **关闭状态**：{'已关闭' if data.closed else '未关闭'}",
        f"- **提示信息**：{data.message}",
    ]


@renderer(ExamResultData)
def _render_exam_result(data: ExamResultData) -> list[str]:
    return [
        f"## 考试结果：`{data.exam_id}`",
        "",
        f"- **成绩**：{data.grade if data.grade is not None else '未公布'}",
        f"- **用时（秒）**：{data.duration if data.duration is not None else '-'}",
        f"- **作答题目数**：{data.problem_count}",
        *([f"- **平台回执**：{data.message}"] if data.message else []),
    ]


@renderer(ExamStatusData)
def _render_exam_status(data: ExamStatusData) -> list[str]:
    return [
        f"## 考场状态快照：`{data.exam_id}`",
        "",
        f"- **cover 状态**：{data.room_status or '-'}（raw: {data.room_status_raw}）",
        f"- **LMS 封面状态 raw**：{data.lms_status if data.lms_status is not None else '-'}",
        f"- **refresh_time**：{data.refresh_code} {data.refresh_message}".rstrip(),
        f"- **暂存作答数**：{data.cached_count}",
        *([f"- **暂存样例**：`{data.cached_sample}`"] if data.cached_sample else []),
        f"- **problem_results**：{data.result_code} {data.result_message}".rstrip(),
        *(["> **说明**：raw 2 表示已交卷待批阅，含主观题的试卷在批阅前不会返回结果"] if data.room_status_raw == 2 else []),
    ]


@renderer(NoticeListData)
def _render_notice_list(data: NoticeListData) -> list[str]:
    rows = [[i, notice.id, notice.name, "已读" if notice.completed else "未读"] for i, notice in enumerate(data.notices, 1)]
    return [f"## 公告/通知列表 (共 {len(data.notices)} 条)", "", *_table(["序号", "公告 ID", "节点名称", "阅读状态"], rows)]


@renderer(NoticeDetail)
def _render_notice_detail(data: NoticeDetail) -> list[str]:
    lines = [
        f"## 公告：{_cell(data.title)}",
        "",
        f"- **节点 ID**：`{data.leaf_id}` | **公告 ID**：`{data.notice_id or '-'}`",
    ]
    if data.publisher:
        lines.append(f"- **发布者**：{_cell(data.publisher)}")
    if data.published_at:
        lines.append(f"- **发布时间**：{_format_time_str(data.published_at)}")
    lines.extend(["", "### 正文", data.content.strip() or "（平台未返回正文）"])
    return lines
