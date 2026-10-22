"""CLI 答案输入的兼容归一与结构校验。"""

from typing import Annotated

from pydantic import BeforeValidator, ConfigDict, Field, RootModel, TypeAdapter, model_validator

from wkhelper.domain.model import Model


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
