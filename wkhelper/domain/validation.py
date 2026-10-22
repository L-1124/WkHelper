"""显式用户输入的严格校验边界。"""

from pydantic import BaseModel, ConfigDict, ValidationError


class InputModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", allow_inf_nan=False, validate_default=True, validate_assignment=True)


def validation_message(error: ValidationError) -> str:
    """只披露字段位置与规则，不包含用户输入。"""
    return "; ".join(
        f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
        for item in error.errors(include_input=False, include_context=False, include_url=False)
    )


def validate_input[T: BaseModel](model_type: type[T], value: object) -> T:
    try:
        return model_type.model_validate(value)
    except ValidationError as exc:
        raise ValueError(validation_message(exc)) from None
