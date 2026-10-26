"""平台协议与响应边界数据模型。"""

import random

from pydantic import ConfigDict, Field, JsonValue, field_validator

from wkhelper.domain.model import Model


def create_page_nonce() -> str:
    """页面编号后缀，算法对齐前端 `Math.floor(1048576*(1+random)).toString(36)`。"""
    return _base36(int(1048576 * (1 + random.random())))


def _base36(value: int) -> str:
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    if value <= 0:
        return "0"
    out: list[str] = []
    while value:
        value, rem = divmod(value, 36)
        out.append(digits[rem])
    return "".join(reversed(out))


class HeartbeatProfile(Model):
    """心跳报文中随平台变化的固定字段，由平台适配层显式提供。

    `lob` 是业务线标识（雨课堂 `ykt`、学堂在线 `xt`）；`n` 是 CDN 代号，
    只有解析出播放源时才有意义，未知时留空；`sp` 为播放倍速。
    """

    model_config = ConfigDict(frozen=True)

    lob: str
    n: str = ""
    sp: float = 1.0
    interval: int = 5


class PlatformExamTokenData(Model):
    """考场令牌接口响应数据。"""

    model_config = ConfigDict(extra="allow")

    token: str = ""
    crypt: str = ""
    exam_host: str = ""
    user_id: str = ""

    @field_validator("token", "crypt", "exam_host", "user_id", mode="before")
    @classmethod
    def _coerce_str(cls, v: object) -> str:
        return str(v or "")


class PlatformContentInfo(Model):
    """叶子节点内容信息。"""

    model_config = ConfigDict(extra="allow")

    leaf_type_id: int | str | None = None


class PlatformLeafInfo(Model):
    """叶子节点简要信息。"""

    model_config = ConfigDict(extra="allow")

    leaf_type: int | str | None = None
    leaf_type_id: int | str | None = None
    name: str | None = None
    start_time: int | str | None = None
    deadline: int | str | None = None
    content_info: PlatformContentInfo | None = None

    @field_validator("content_info", mode="before")
    @classmethod
    def _val_content_info(cls, v: object) -> object:
        return v if isinstance(v, dict) else None


class PlatformFaceAuthStatus(Model):
    """人脸与身份验证配置。"""

    model_config = ConfigDict(extra="allow")

    online_proctor: bool = False
    has_idphoto: bool = False

    @field_validator("online_proctor", "has_idphoto", mode="before")
    @classmethod
    def _val_truthy(cls, v: object) -> bool:
        return bool(v)


class PlatformExamRoomResult(Model):
    """考场房间状态条目。"""

    model_config = ConfigDict(extra="allow")

    status: int | str | None = None


class PlatformExamCoverData(Model):
    """考试封面与考场元数据。"""

    model_config = ConfigDict(extra="allow")

    title: str | None = None
    problem_count: int | str | None = None
    total_score: float | int | str | None = None
    limit: int | str | None = None
    start_time: int | str | None = None
    deadline: int | str | None = None
    description: str | None = None
    result: PlatformExamRoomResult | None = None
    face_auth_status: PlatformFaceAuthStatus | None = None
    online_proctor: bool = False
    identity_auth: bool = False
    capture_screen: bool = False
    app_capture_screen: bool = False
    app_front_random_snap: bool = False
    open_screen_cuts: bool = False
    is_offline: bool = False

    @field_validator("result", mode="before")
    @classmethod
    def _val_result(cls, v: object) -> object:
        return v if isinstance(v, dict) else None

    @field_validator("face_auth_status", mode="before")
    @classmethod
    def _val_face_status(cls, v: object) -> object:
        return v if isinstance(v, dict) else None

    @field_validator(
        "online_proctor",
        "identity_auth",
        "capture_screen",
        "app_capture_screen",
        "app_front_random_snap",
        "open_screen_cuts",
        "is_offline",
        mode="before",
    )
    @classmethod
    def _val_truthy(cls, v: object) -> bool:
        return bool(v)


class PlatformExamCacheItem(Model):
    """考场暂存作答条目。"""

    model_config = ConfigDict(extra="allow")

    problem_id: int | str | None = None
    result: JsonValue = None


class PlatformExamCacheData(Model):
    """考场暂存作答响应数据。"""

    model_config = ConfigDict(extra="allow")

    results: list[PlatformExamCacheItem] = Field(default_factory=list)

    @field_validator("results", mode="before")
    @classmethod
    def _filter_results(cls, v: object) -> list[object]:
        if not isinstance(v, list):
            return []
        return [item for item in v if isinstance(item, dict)]


class PlatformExamResult(Model):
    """考场成绩与作答结果数据。"""

    model_config = ConfigDict(extra="allow")

    grade: float | None = None
    duration: int | None = None
    problem_results: list[JsonValue] = Field(default_factory=list)

    @field_validator("grade", mode="before")
    @classmethod
    def _coerce_grade(cls, v: object) -> float | None:
        if isinstance(v, (int, float)):
            return float(v)
        return None

    @field_validator("duration", mode="before")
    @classmethod
    def _coerce_duration(cls, v: object) -> int | None:
        if isinstance(v, (int, float)):
            return int(v)
        return None

    @field_validator("problem_results", mode="before")
    @classmethod
    def _filter_problem_results(cls, v: object) -> list[JsonValue]:
        return v if isinstance(v, list) else []


class PlatformExamOptionRaw(Model):
    """试卷题目选项原始数据。"""

    model_config = ConfigDict(extra="allow")

    key: str = ""
    value: str = ""

    @field_validator("key", "value", mode="before")
    @classmethod
    def _coerce_str(cls, v: object) -> str:
        return str(v or "")


class PlatformExamProblemRaw(Model):
    """试卷题目原始数据。"""

    model_config = ConfigDict(extra="allow")

    problem_id: int | str | None = None
    Type: str | int | None = None
    TypeText: str | None = None
    Body: str | None = None
    Options: list[PlatformExamOptionRaw] = Field(default_factory=list)
    Answer: list[JsonValue] | None = None
    score: float | None = None
    Score: float | None = None
    HasRemark: bool | int | None = None

    @field_validator("score", "Score", mode="before")
    @classmethod
    def _coerce_score(cls, v: object) -> object:
        return float(v) if isinstance(v, (str, int, float)) else v

    @field_validator("Options", mode="before")
    @classmethod
    def _filter_options(cls, v: object) -> list[object]:
        if not isinstance(v, list):
            return []
        return [opt for opt in v if isinstance(opt, dict)]

    @field_validator("Answer", mode="before")
    @classmethod
    def _val_answer(cls, v: object) -> list[JsonValue] | None:
        if isinstance(v, list):
            return v
        return None


class PlatformExamPaperData(Model):
    """考场整卷原始数据。"""

    model_config = ConfigDict(extra="allow")

    title: str | None = None
    problems: list[JsonValue] = Field(default_factory=list)

    @field_validator("problems", mode="before")
    @classmethod
    def _filter_problems(cls, v: object) -> list[JsonValue]:
        return v if isinstance(v, list) else []


class PlatformNoticeData(Model):
    """雨课堂公告正文响应数据。"""

    model_config = ConfigDict(extra="allow")

    title: str = ""
    name: str = ""
    content: JsonValue = None
    content_info: JsonValue = None
    publish_time: int | str | None = None
    create_time: int | str | None = None
    created: int | str | None = None
    publisher: str = ""
    author: str = ""
    teacher_name: str = ""
    creator: str = ""

    @field_validator("title", "name", "publisher", "author", "teacher_name", "creator", mode="before")
    @classmethod
    def _coerce_str(cls, v: object) -> str:
        return v if isinstance(v, str) and v.strip() else ""

    def resolve_content_html(self) -> str:
        """解析公告正文 HTML。"""
        if isinstance(self.content, dict):
            for key in ("text", "html", "content"):
                val = self.content.get(key)
                if isinstance(val, str) and val.strip():
                    return val
        elif isinstance(self.content, str) and self.content.strip():
            return self.content
        if isinstance(self.content_info, dict):
            for key in ("text", "content"):
                val = self.content_info.get(key)
                if isinstance(val, str) and val.strip():
                    return val
        return ""

    @property
    def resolved_title(self) -> str:
        """解析公告标题。"""
        return self.title or self.name

    @property
    def resolved_publisher(self) -> str:
        """解析公告发布者。"""
        return self.publisher or self.author or self.teacher_name or self.creator

    @property
    def resolved_time(self) -> int | str | None:
        """解析公告发布时间。"""
        return self.publish_time or self.create_time or self.created


PlatformExamProblemRaw.model_rebuild()
