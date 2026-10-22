"""后续动作。"""

from pydantic import ConfigDict, Field

from wkhelper.domain.model import Model


class NextAction(Model):
    """自引导下一步动作的强类型契约。"""

    model_config = ConfigDict(frozen=True)

    reason: str
    argv: list[str]
    description: str = Field(default="", exclude_if=lambda v: not v)
