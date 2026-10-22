"""后续动作。"""

from dataclasses import dataclass

from wkhelper.domain.json_types import JsonDict


@dataclass(frozen=True)
class NextAction:
    """自引导下一步动作的强类型契约。"""

    reason: str
    argv: list[str]
    description: str = ""

    def to_dict(self) -> JsonDict:
        payload: JsonDict = {
            "reason": self.reason,
            "argv": list(self.argv),
        }
        if self.description:
            payload["description"] = self.description
        return payload
