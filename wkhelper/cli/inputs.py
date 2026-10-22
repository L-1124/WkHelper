"""CLI 答案 inputs 与命令参数校验归一模型。"""

import math
from pathlib import Path
from typing import Annotated, Self

from cyclopts import Group, Parameter
from cyclopts.validators import LimitedChoice
from pydantic import BeforeValidator, ConfigDict, Field, RootModel, TypeAdapter, ValidationError, model_validator

from wkhelper.domain.model import Model
from wkhelper.domain.validation import validation_message


def _normalize_answer(value: object) -> object:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if item is not None and not isinstance(item, bool)]
    return value


type AnswerValues = Annotated[list[str], BeforeValidator(_normalize_answer)]


class AnswerEntry(Model):
    model_config = ConfigDict(extra="ignore")

    question_id: Annotated[str, Field(min_length=1)]
    answer: AnswerValues


_ENTRIES = TypeAdapter(list[AnswerEntry])


class AnswersInput(RootModel[dict[str, AnswerValues]]):
    model_config = ConfigDict(strict=True, hide_input_in_errors=True)

    @model_validator(mode="before")
    @classmethod
    def normalize_shape(cls, value: object) -> object:
        if isinstance(value, dict) and isinstance(value.get("answers"), list):
            entries = _ENTRIES.validate_python(value["answers"])
            return {entry.question_id: entry.answer for entry in entries}
        return value


# 互斥输入组：答案直接输入与文件输入二选一 (exactly one)
answers_group = Group("Answers", validator=LimitedChoice(min=1, max=1))


def validate_positive_limit(type_: type, value: int) -> None:
    """校验 limit 参数为正整数 (>= 1)。"""
    if value <= 0:
        raise ValueError(f"limit 必须为正整数 (>= 1)，当前提供: {value}")


Limit = Annotated[
    int,
    Parameter(
        name=["--limit"],
        validator=validate_positive_limit,
        help="每页条数",
    ),
]


class VideoSnapshotInput(Model):
    """视频抽帧参数输入模型与严格归一化校验。"""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid", allow_inf_nan=False)

    course: str | None = None
    video: str | None = None
    timestamps: str | None = None
    start: float | None = None
    end: float | None = None
    step: float | None = None
    count: int | None = None
    width: int = 1280
    quality: int = 85
    output_dir: Path | str | None = None

    parsed_timestamps: list[float] | None = None
    effective_count: int | None = None
    effective_quality: int = 85
    target_output_dir: Path | None = None

    @model_validator(mode="after")
    def validate_and_normalize(self) -> Self:
        if not self.course or not self.video:
            raise ValueError("执行抽帧必须指定 `--course` 和 `--video` 参数")

        if self.timestamps is None and self.step is None and self.count is None:
            raise ValueError(
                "必须指定抽帧模式：`--timestamps`、`--step` 或 `--count` 之一（`--start/--end` 需配合 `--step` 或 `--count` 使用）"
            )

        if self.step is not None:
            if not math.isfinite(self.step) or self.step <= 0:
                raise ValueError(f"采样步长 step 必须大于 0: {self.step}")

        if self.start is not None:
            if not math.isfinite(self.start) or self.start < 0:
                raise ValueError(f"采样起始时间 start 不能小于 0: {self.start}")

        if self.end is not None:
            if not math.isfinite(self.end) or self.end < 0:
                raise ValueError(f"采样结束时间 end 不能小于 0: {self.end}")

        if self.start is not None and self.end is not None and self.start > self.end:
            raise ValueError(f"采样起始时间 start ({self.start}) 不能大于结束时间 end ({self.end})")

        if self.count is not None and self.count <= 0:
            raise ValueError(f"均匀抽帧数量 count 必须大于 0: {self.count}")

        if self.width <= 0:
            raise ValueError(f"图像宽度 width 必须大于 0: {self.width}")

        parsed_ts: list[float] | None = None
        if self.timestamps is not None:
            raw_tokens = [x.strip() for x in self.timestamps.split(",") if x.strip()]
            if not raw_tokens:
                raise ValueError(f"未能解析出有效时间戳: {self.timestamps}")
            ts_list: list[float] = []
            for item in raw_tokens:
                try:
                    val = float(item)
                except ValueError as exc:
                    raise ValueError(f"时间戳格式错误: {self.timestamps}") from exc
                if not math.isfinite(val) or val < 0:
                    raise ValueError(f"时间戳格式错误: {self.timestamps}")
                ts_list.append(val)
            parsed_ts = ts_list[:30]

        eff_count = min(30, self.count) if self.count is not None else None
        eff_qual = max(30, min(95, self.quality))
        out_dir = Path(self.output_dir) if self.output_dir is not None else None

        object.__setattr__(self, "parsed_timestamps", parsed_ts)
        object.__setattr__(self, "effective_count", eff_count)
        object.__setattr__(self, "effective_quality", eff_qual)
        object.__setattr__(self, "target_output_dir", out_dir)

        return self


def validate_snapshot_input(
    *,
    course: str | None,
    video: str | None,
    timestamps: str | None = None,
    start: float | None = None,
    end: float | None = None,
    step: float | None = None,
    count: int | None = None,
    width: int = 1280,
    quality: int = 85,
    output_dir: Path | str | None = None,
) -> VideoSnapshotInput:
    """校验并归一化视频抽帧参数。"""
    try:
        return VideoSnapshotInput(
            course=course,
            video=video,
            timestamps=timestamps,
            start=start,
            end=end,
            step=step,
            count=count,
            width=width,
            quality=quality,
            output_dir=output_dir,
        )
    except ValidationError as exc:
        raise ValueError(validation_message(exc)) from None


__all__ = [
    "AnswerEntry",
    "AnswersInput",
    "Limit",
    "VideoSnapshotInput",
    "answers_group",
    "validate_positive_limit",
    "validate_snapshot_input",
]
