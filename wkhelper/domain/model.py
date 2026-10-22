"""数据契约的公共校验配置。"""

from pydantic import BaseModel, ConfigDict


class Model(BaseModel):
    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        allow_inf_nan=False,
        validate_default=True,
        validate_assignment=True,
        hide_input_in_errors=True,
    )
