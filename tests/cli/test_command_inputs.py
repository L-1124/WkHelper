"""CLI 命令输入校验、互斥参数、分页限制与抽帧模型规范测试。"""

from io import StringIO

import pytest

from wkhelper.cli.commands.homework import homework_submit_cmd
from wkhelper.cli.commands.video import video_snapshot_cmd
from wkhelper.cli.inputs import (
    validate_positive_limit,
    validate_snapshot_input,
)
from wkhelper.main import async_main

# =========================================================================
# 1. 预认证前置校验：各分页命令在遇到非法 --limit 时均由 CLI 解析层拦截退出 (exit code 2)
# =========================================================================


@pytest.mark.parametrize(
    "cmd_args",
    [
        ["course", "list", "--limit", "0"],
        ["course", "list", "--limit", "-5"],
        ["video", "list", "--course", "c1", "--limit", "0"],
        ["homework", "list", "--course", "c1", "--limit", "0"],
        ["exam", "list", "--course", "c1", "--limit", "-1"],
        ["article", "list", "--course", "c1", "--limit", "0"],
        ["discussion", "list", "--course", "c1", "--limit", "0"],
        ["notice", "list", "--course", "c1", "--limit", "0"],
        ["question", "list", "--course", "c1", "--homework", "h1", "--limit", "0"],
    ],
)
@pytest.mark.asyncio
async def test_paginated_commands_reject_non_positive_limit_pre_auth(cmd_args: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    """所有支持分页的命令必须在进入鉴权/网络请求前，直接通过参数校验拦截非法 limit (exit 2)。"""
    buf = StringIO()
    monkeypatch.setattr("sys.stdout", buf)

    code = await async_main(cmd_args)
    assert code == 2
    output = buf.getvalue()
    assert "limit 必须为正整数 (>= 1)" in output or "INVALID_ARGUMENT" in output


def test_validate_positive_limit_direct_calls() -> None:
    """直接函数调用方式校验 limit 参数。"""
    validate_positive_limit(int, 1)
    validate_positive_limit(int, 100)

    with pytest.raises(ValueError, match="limit 必须为正整数"):
        validate_positive_limit(int, 0)

    with pytest.raises(ValueError, match="limit 必须为正整数"):
        validate_positive_limit(int, -10)


# =========================================================================
# 2. 负向参数：--no-cache 不再自动衍生出非预期的 --no-no-cache
# =========================================================================


@pytest.mark.asyncio
async def test_no_cache_negative_flag_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """--no-cache 属于负向命名参数，禁止生成 --no-no-cache 别名。"""
    buf = StringIO()
    monkeypatch.setattr("sys.stdout", buf)

    code = await async_main(["video", "snapshot", "--no-no-cache"])
    assert code == 2
    output = buf.getvalue()
    assert "Unknown option: --no-no-cache" in output


# =========================================================================
# 3. 互斥参数组校验：homework submit --answers / --answers-file 二选一
# =========================================================================


@pytest.mark.asyncio
async def test_homework_submit_requires_exactly_one_answer_source(monkeypatch: pytest.MonkeyPatch) -> None:
    """未提供任何答案输入时在 CLI 参数解析期拦截 (exit 2)。"""
    buf = StringIO()
    monkeypatch.setattr("sys.stdout", buf)

    code = await async_main(["homework", "submit", "--course", "c1", "--homework", "h1"])
    assert code == 2
    output = buf.getvalue()
    assert "Answers" in output
    assert "Only [1, 1] choices may be specified" in output


@pytest.mark.asyncio
async def test_homework_submit_rejects_both_answer_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    """同时提供内联答案与答案文件时在 CLI 参数解析期拦截 (exit 2)。"""
    buf = StringIO()
    monkeypatch.setattr("sys.stdout", buf)

    code = await async_main(
        [
            "homework",
            "submit",
            "--course",
            "c1",
            "--homework",
            "h1",
            "--answers",
            '{"1": ["A"]}',
            "--answers-file",
            "answers.json",
        ]
    )
    assert code == 2
    output = buf.getvalue()
    assert "Answers" in output
    assert "Only [1, 1] choices may be specified" in output


@pytest.mark.asyncio
async def test_homework_submit_cmd_direct_call_exclusivity() -> None:
    """直接调用 homework_submit_cmd 协同验证互斥语义。"""
    with pytest.raises(ValueError, match="只能提供其一"):
        await homework_submit_cmd(course="c1", homework="h1", answers='{"1": ["A"]}', answers_file="ans.json")

    with pytest.raises(ValueError, match="至少提供一种答案输入"):
        await homework_submit_cmd(course="c1", homework="h1", answers=None, answers_file=None)


# =========================================================================
# 4. 视频抽帧 Pydantic 输入模型边界、截断与归一化
# =========================================================================


def test_video_snapshot_input_timestamps_parsing_and_clamp() -> None:
    """逗号分隔时间戳解析、浮点转换与 30 帧截断。"""
    snap = validate_snapshot_input(
        course="1001",
        video="2001",
        timestamps="10, 20.5, 30",
    )
    assert snap.parsed_timestamps == [10.0, 20.5, 30.0]

    # 超过 30 帧时自动截断至前 30 帧
    large_ts = ",".join(str(i) for i in range(50))
    snap_large = validate_snapshot_input(
        course="1001",
        video="2001",
        timestamps=large_ts,
    )
    assert snap_large.parsed_timestamps is not None
    assert len(snap_large.parsed_timestamps) == 30
    assert snap_large.parsed_timestamps[-1] == 29.0


def test_video_snapshot_input_count_and_quality_clamps() -> None:
    """抽帧数量上限 30 帧截断，JPEG 质量 30~95 钳制。"""
    snap_high = validate_snapshot_input(
        course="1001",
        video="2001",
        count=50,
        quality=100,
    )
    assert snap_high.effective_count == 30
    assert snap_high.effective_quality == 95

    snap_low = validate_snapshot_input(
        course="1001",
        video="2001",
        count=5,
        quality=10,
    )
    assert snap_low.effective_count == 5
    assert snap_low.effective_quality == 30


def test_video_snapshot_input_non_finite_rejection() -> None:
    """严防 NaN / Inf 等非有限数值逃逸。"""
    with pytest.raises(ValueError):
        validate_snapshot_input(
            course="1001",
            video="2001",
            step=float("nan"),
        )

    with pytest.raises(ValueError):
        validate_snapshot_input(
            course="1001",
            video="2001",
            start=float("inf"),
            step=10.0,
        )

    with pytest.raises(ValueError, match="时间戳格式错误"):
        validate_snapshot_input(
            course="1001",
            video="2001",
            timestamps="10, nan, 20",
        )

    with pytest.raises(ValueError, match="时间戳格式错误"):
        validate_snapshot_input(
            course="1001",
            video="2001",
            timestamps="10, inf, 20",
        )


def test_video_snapshot_input_negative_and_range_validation() -> None:
    """抽帧数值区间、负数与步长校验。"""
    with pytest.raises(ValueError, match="采样步长 step 必须大于 0"):
        validate_snapshot_input(course="1001", video="2001", step=0)

    with pytest.raises(ValueError, match="采样步长 step 必须大于 0"):
        validate_snapshot_input(course="1001", video="2001", step=-5)

    with pytest.raises(ValueError, match="采样起始时间 start 不能小于 0"):
        validate_snapshot_input(course="1001", video="2001", start=-1, step=5)

    with pytest.raises(ValueError, match="采样结束时间 end 不能小于 0"):
        validate_snapshot_input(course="1001", video="2001", end=-1, step=5)

    with pytest.raises(ValueError, match="不能大于结束时间"):
        validate_snapshot_input(course="1001", video="2001", start=100, end=50, step=5)

    with pytest.raises(ValueError, match="均匀抽帧数量 count 必须大于 0"):
        validate_snapshot_input(course="1001", video="2001", count=0)


def test_video_snapshot_input_mode_and_target_requirements() -> None:
    """必须提供 course、video 以及有效抽帧模式。"""
    with pytest.raises(ValueError, match="执行抽帧必须指定 `--course` 和 `--video` 参数"):
        validate_snapshot_input(course=None, video="2001", count=5)

    with pytest.raises(ValueError, match="执行抽帧必须指定 `--course` 和 `--video` 参数"):
        validate_snapshot_input(course="1001", video=None, count=5)

    with pytest.raises(ValueError, match="必须指定抽帧模式"):
        validate_snapshot_input(course="1001", video="2001")


@pytest.mark.asyncio
async def test_video_snapshot_clean_cache_bypasses_input_validation() -> None:
    """--clean-cache 模式下无需提供课程 ID 或抽帧模式即可成功执行退出。"""
    result = await video_snapshot_cmd(clean_cache=True)
    assert result.data.total_frames == 0
    assert result.meta is not None
    assert "cleaned_sessions" in result.meta
