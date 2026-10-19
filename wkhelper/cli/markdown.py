"""CLI 终端 Markdown 展现层格式化器。"""

from typing import Any

import msgspec

from wkhelper.cli.envelope import NextAction
from wkhelper.core.models import (
    AccountListData,
    ArticleListData,
    ArticleReadResult,
    AuthStatusResult,
    CourseDetail,
    CourseListData,
    DiscussionCommentResult,
    DiscussionListData,
    DiscussionTopicDetail,
    ExportResult,
    HomeworkListData,
    HomeworkOutline,
    HomeworkSubmitPreview,
    HomeworkSubmitResult,
    JsonDict,
    LoginResult,
    OutputFileResult,
    QuestionDetail,
    QuestionListData,
    QuestionSubmitPreview,
    QuestionSubmitResult,
    RemoveResult,
    StatusData,
    StorageStatus,
    VideoLearnPreview,
    VideoListData,
    VideoPlanSummary,
    VideoProgress,
    json_int,
)


def _format_next_actions(actions: list[NextAction] | None) -> str:
    if not actions:
        return ""
    lines = ["\n### 下一步建议", ""]
    for act in actions:
        cmd_str = " ".join(act.argv)
        lines.append(f"- **{act.reason}**：`{cmd_str}`")
    return "\n".join(lines)


def format_error_markdown(
    code: str,
    message: str,
    details: JsonDict | None = None,
    next_actions: list[NextAction] | None = None,
) -> str:
    """格式化错误响应为 Markdown。"""
    lines = [
        f"## 操作失败 [{code}]",
        "",
        f"> **错误信息**：{message}",
        "",
    ]
    if details:
        lines.append("### 详细诊断")
        if "exception_type" in details:
            lines.append(f"- **异常类型**：`{details['exception_type']}`")
        if "traceback" in details and isinstance(details["traceback"], list):
            lines.append("\n```text")
            lines.extend(str(item) for item in details["traceback"])
            lines.append("```")
        other_details = {k: v for k, v in details.items() if k not in ("exception_type", "traceback")}
        if other_details:
            lines.append(f"- **附加详情**：`{other_details}`")
        lines.append("")

    actions_md = _format_next_actions(next_actions)
    if actions_md:
        lines.append(actions_md)

    return "\n".join(lines).strip()


def format_result_markdown(
    data: Any,
    meta: JsonDict | None = None,
    next_actions: list[NextAction] | None = None,
) -> str:
    """将强类型领域结果数据格式化为适合终端阅读的 GFM Markdown。"""
    lines: list[str] = []

    if isinstance(data, CourseListData):
        lines.append(f"## 课程列表 (共 {len(data.courses)} 门)\n")
        lines.append("| 序号 | 课程引用 (ID) | 课程名称 | 平台 | 课堂 ID |")
        lines.append("|:---|:---|:---|:---|:---|")
        for i, c in enumerate(data.courses, 1):
            lines.append(f"| {i} | {c.id} | {c.name} | {c.platform} | {c.classroom_id} |")

    elif isinstance(data, CourseDetail):
        lines.append(f"## 课程详情：{data.name}\n")
        lines.append(f"- **课程引用 ID**：`{data.id}`")
        lines.append(f"- **所属平台**：`{data.platform}`")
        if data.metadata:
            lines.append("\n### 元数据")
            for k, v in sorted(data.metadata.items()):
                lines.append(f"- **{k}**：`{v}`")

    elif isinstance(data, VideoListData):
        lines.append(f"## 视频列表 (共 {len(data.videos)} 个)\n")
        lines.append("| 序号 | 视频 ID | 视频节点名称 | 状态 |")
        lines.append("|:---|:---|:---|:---|")
        for i, v in enumerate(data.videos, 1):
            status_tag = "已完成" if v.completed else "未完成"
            lines.append(f"| {i} | {v.id} | {v.name} | {status_tag} |")

    elif isinstance(data, VideoProgress):
        tag = "已完成" if data.completed else "进行中"
        pct = f"{data.current_rate * 100:.1f}%"
        lines.append(f"## 视频进度：`{data.video_id}`\n")
        lines.append(f"- **课程 ID**：`{data.course_id}`")
        lines.append(f"- **观看进度**：**{pct}** ({tag})")

    elif isinstance(data, VideoPlanSummary):
        lines.append("## 视频计划学习汇总\n")
        lines.append(f"- **目标总数**：{data.total} | **已完成**：{data.completed} | **失败/中断**：{data.failed}\n")
        lines.append("| 视频 ID | 初始进度 | 最终进度 | 状态 | 耗时 | 详细说明 |")
        lines.append("|:---|:---|:---|:---|:---|:---|")
        for r in data.results:
            init_pct = f"{r.initial_rate * 100:.1f}%"
            final_pct = f"{r.final_rate * 100:.1f}%"
            lines.append(f"| {r.video_id} | {init_pct} | {final_pct} | {r.status} | {r.duration_seconds:.1f}s | {r.message} |")

    elif isinstance(data, VideoLearnPreview):
        lines.append("## 视频学习计划预览\n")
        lines.append(f"> {data.note}\n")
        lines.append(f"- **目标视频数**：{data.targets_count}")
        lines.append(f"- **执行策略**：并发 `{data.effective_config.concurrency}` | 出错策略 `{data.effective_config.on_error}`\n")
        lines.append("| 序号 | 课程 ID | 视频 ID |")
        lines.append("|:---|:---|:---|")
        for i, t in enumerate(data.targets, 1):
            lines.append(f"| {i} | {t.course_id} | {t.video_id} |")

    elif isinstance(data, HomeworkListData):
        lines.append(f"## 作业列表 (共 {len(data.homeworks)} 项)\n")
        lines.append("| 序号 | 作业 ID | 作业名称 | 截止时间 | 完成状态 |")
        lines.append("|:---|:---|:---|:---|:---|")
        for i, hw in enumerate(data.homeworks, 1):
            status_tag = "已完成" if hw.completed else "未完成"
            dl_str = str(hw.deadline) if hw.deadline else "无限制"
            lines.append(f"| {i} | {hw.id} | {hw.name} | {dl_str} | {status_tag} |")

    elif isinstance(data, HomeworkOutline):
        lines.append(f"## 作业题目大纲：{data.title}\n")
        lines.append(f"- **课程 ID**：`{data.course_id}` | **作业 ID**：`{data.homework_id}`")
        lines.append(f"- **题目总数**：{data.total_questions}\n")
        lines.append("| 题号 | 题目 ID | 题型 | 题库 ID |")
        lines.append("|:---|:---|:---|:---|")
        lines.extend(f"| {q.index} | {q.id} | {q.type or '客观题'} | {q.library_id or '-'} |" for q in data.questions_summary)

    elif isinstance(data, HomeworkSubmitResult):
        lines.append("## 作业提交结果\n")
        lines.append(f"- **总题数**：{data.total} | **成功提交**：{data.submitted_count} | **正确题数**：{data.correct_count}\n")
        lines.append("| 序号 | 题目 ID | 提交状态 | 判题结果 | 正确答案 | 提示信息 |")
        lines.append("|:---|:---|:---|:---|:---|:---|")
        for item in data.items:
            sub_tag = "成功" if item.submitted else "失败"
            judge_tag = "正确" if item.is_correct else ("错误" if item.submitted else "-")
            ans_str = ", ".join(item.correct_answer) if item.correct_answer else "-"
            err_str = item.error or "-"
            lines.append(f"| {item.index} | {item.question_id} | {sub_tag} | {judge_tag} | {ans_str} | {err_str} |")
    elif isinstance(data, HomeworkSubmitPreview):
        lines.append("## 作业答案提交预检\n")
        lines.append(f"- **课程 ID**：`{data.course_id}` | **作业 ID**：`{data.homework_id}`")
        lines.append(f"- **拟提交题目数**：{data.answers_count} | **合法性校验**：{'通过' if data.validated else '未通过'}")

    elif isinstance(data, QuestionListData):
        lines.append(f"## 作业题目列表 (共 {len(data.questions)} 题)\n")
        lines.append("| 题号 | 题目 ID | 题干摘要 | 作答状态 | 正确状态 |")
        lines.append("|:---|:---|:---|:---|:---|")
        for q in data.questions:
            sub_tag = "已作答" if q.submitted else "未作答"
            right_tag = "正确" if q.is_correct is True else ("错误" if q.is_correct is False else "-")
            clean_prompt = q.prompt.replace("\n", " ")[:40]
            lines.append(f"| {q.index} | {q.id} | {clean_prompt} | {sub_tag} | {right_tag} |")
    elif isinstance(data, QuestionDetail):
        lines.append(f"## 题目详情 [ID: {data.id}]\n")
        lines.append(f"### 题干\n{data.prompt.strip()}\n")
        if data.options:
            lines.append("### 选项")
            lines.extend(f"- **{opt.key}**: {opt.text}" for opt in data.options)
            lines.append("")
        sub_tag = "已作答" if data.submitted else "未作答"
        right_tag = "正确" if data.is_correct is True else ("错误" if data.is_correct is False else "待判")
        ans_str = str(data.user_answer) if data.user_answer is not None else "无"
        score_str = str(data.score) if data.score is not None else "-"
        lines.append(f"- **状态**：{sub_tag} (我的作答: `{ans_str}`, 判定: {right_tag}, 得分: {score_str})")
        lines.append(f"- **尝试次数**：{data.attempt_count}")
        if data.resources:
            lines.append(f"- **本地资源**：{len(data.resources)} 个文件已下载到本地")

    elif isinstance(data, QuestionSubmitResult):
        right_tag = "正确" if data.is_correct else "错误"
        lines.append(f"## 单题提交结果：`{data.question_id}`\n")
        lines.append(f"- **提交选项**：`{data.submitted_answer}`")
        lines.append(f"- **提交成功**：{'是' if data.success else '否'}")
        lines.append(f"- **判题结果**：{right_tag}")
        if data.correct_answer:
            lines.append(f"- **正确答案**：`{data.correct_answer}`")
        if data.error:
            lines.append(f"- **提示信息**：{data.error}")

    elif isinstance(data, QuestionSubmitPreview):
        lines.append(f"## 单题提交预检：`{data.question_id}`\n")
        lines.append(f"- **课程 ID**：`{data.course_id}` | **作业 ID**：`{data.homework_id}`")
        lines.append(f"- **拟提交选项**：`{data.answer}`")

    elif isinstance(data, ArticleListData):
        lines.append(f"## 资料/图文列表 (共 {len(data.articles)} 项)\n")
        lines.append("| 序号 | 资料 ID | 节点名称 | 阅读状态 |")
        lines.append("|:---|:---|:---|:---|")
        for i, a in enumerate(data.articles, 1):
            tag = "已读" if a.completed else "未读"
            lines.append(f"| {i} | {a.id} | {a.name} | {tag} |")
    elif isinstance(data, ArticleReadResult):
        lines.append("## 资料阅读结果\n")
        lines.append(f"- **资料节点**：`{data.leaf_id}` ({data.name})")
        lines.append(f"- **完成状态**：{'已完成' if data.completed else '未完成'}")

    elif isinstance(data, DiscussionListData):
        lines.append(f"## 讨论题列表 (共 {len(data.discussions)} 题)\n")
        lines.append("| 序号 | 讨论 ID | 节点名称 | 发言状态 |")
        lines.append("|:---|:---|:---|:---|")
        for i, d in enumerate(data.discussions, 1):
            tag = "已发言" if d.completed else "未发言"
            lines.append(f"| {i} | {d.id} | {d.name} | {tag} |")
    elif isinstance(data, DiscussionTopicDetail):
        tag = "已发言" if data.completed else "未发言"
        lines.append(f"## 讨论题详情：{data.title}\n")
        lines.append(f"- **节点 ID**：`{data.leaf_id}` | **Topic ID**：`{data.topic_id}`")
        lines.append(f"- **发言状态**：{tag} (我的发言数: {data.user_comment_num})")
        lines.append(f"- **全班参与人数**：{data.comment_count} 人\n")
        lines.append(f"### 教师要求与引导题干\n{data.prompt.strip()}")

    elif isinstance(data, DiscussionCommentResult):
        tag = "成功" if data.success else "失败"
        lines.append(f"## 讨论回复提交：{tag}\n")
        lines.append(f"- **节点 ID**：`{data.leaf_id}` | **Topic ID**：`{data.topic_id}`")
        if data.comment_id:
            lines.append(f"- **回复记录 ID**：`{data.comment_id}`")
        lines.append(f"- **回执信息**：{data.message}")

    elif isinstance(data, StatusData):
        lines.append("## 本地运行环境与账号概况\n")
        cs = data.credential_storage
        lines.append(f"- **凭据保管模式**：`{cs.mode}` ({cs.backend})")
        lines.append(f"- **数据存储目录**：`{cs.data_dir}`")
        lines.append(f"- **已保存账号数**：{data.accounts_count}\n")
        if data.accounts:
            lines.append("### 已绑定账号")
            lines.append("| 平台 | 账号 ID | 用户姓名 | 院校归属 |")
            lines.append("|:---|:---|:---|:---|")
            lines.extend(f"| {acc.platform} | {acc.user_id} | {acc.name} | {acc.school or '-'} |" for acc in data.accounts)

    elif isinstance(data, AccountListData):
        lines.append(f"## 已保存账号列表 (共 {len(data.accounts)} 个)\n")
        lines.append("| 平台 | 账号 ID | 姓名 | 院校归属 |")
        lines.append("|:---|:---|:---|:---|")
        lines.extend(f"| {acc.platform} | {acc.user_id} | {acc.name} | {acc.school or '-'} |" for acc in data.accounts)

    elif isinstance(data, AuthStatusResult):
        tag = "有效" if data.valid else "无效/已过期"
        lines.append(f"## 账号登录态验证：{tag}\n")
        lines.append(f"- **平台**：`{data.platform}` | **账号 ID**：`{data.user_id}`")
        lines.append(f"- **用户姓名**：{data.name}")

    elif isinstance(data, LoginResult):
        lines.append("## 登录成功\n")
        lines.append(f"- **平台**：`{data.platform}` | **账号 ID**：`{data.user_id}`")
        lines.append(f"- **用户姓名**：{data.name} ({data.school or '无院校'})")
        lines.append(f"- **凭据持久化**：{'已存入系统安全库' if data.persisted else '仅当前会话有效'}")

    elif isinstance(data, RemoveResult):
        lines.append(f"## 账号凭据删除：{'成功' if data.deleted else '未找到'}\n")
        lines.append(f"- **平台**：`{data.platform}` | **账号 ID**：`{data.account}`")

    elif isinstance(data, StorageStatus):
        lines.append("## 系统凭据后端状态\n")
        lines.append(f"- **模式**：`{data.mode}` ({data.backend})")
        lines.append(f"- **可用性**：{'正常' if data.available else '不可用'} | **优先级**：{data.priority}")
        lines.append(f"- **数据目录**：`{data.data_dir}`")
        lines.append(f"- **元数据数据库**：`{data.metadata_db}`")

    elif isinstance(data, ExportResult):
        lines.append("## 题目导出完成\n")
        lines.append(f"- **导出文件路径**：`{data.file_path}`")
        res_info = f" | **本地资源**：{data.resources_count} 个文件" if data.resources_count > 0 else ""
        lines.append(f"- **包含作业数**：{data.homeworks_count} 项 | **总题目数**：{data.questions_count} 题{res_info}")

    elif isinstance(data, OutputFileResult):
        lines.append("## 文件写入完成\n")
        lines.append(f"- **输出路径**：`{data.output_file}`")

    else:
        # 通用兜底格式化
        lines.append("```json")
        lines.append(msgspec.json.format(msgspec.json.encode(data), indent=2).decode("utf-8"))
        lines.append("```")

    if meta:
        offset = json_int(meta.get("offset"), 0)
        limit = json_int(meta.get("limit"), 10)
        total = json_int(meta.get("total"), 0)
        has_more = bool(meta.get("has_more", False))
        next_cursor = str(meta.get("next_cursor")) if meta.get("next_cursor") else None
        if has_more or offset > 0:
            start_idx = offset + 1
            end_idx = min(total, offset + limit) if total > 0 else (offset + limit)
            total_str = f"，共 {total} 条" if total > 0 else ""
            cursor_str = f" (下一页参数: `--cursor {next_cursor}`)" if next_cursor else ""
            lines.append(f"\n*分页提示: 显示第 {start_idx}~{end_idx} 条{total_str}{cursor_str}*")
    actions_md = _format_next_actions(next_actions)
    if actions_md:
        lines.append(actions_md)

    return "\n".join(lines).strip()
