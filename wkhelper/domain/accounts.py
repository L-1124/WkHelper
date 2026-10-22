"""账号数据。"""

from dataclasses import dataclass


@dataclass
class UserInfo:
    """用户信息模型。"""

    id: int | str
    name: str
    school: str | None = None


@dataclass
class AccountSummary:
    """账号元数据摘要（不含秘密）。"""

    platform: str
    user_id: str
    name: str
    school: str | None
    saved_at: float


@dataclass
class StorageStatus:
    """系统凭据存储后端状态。"""

    mode: str
    backend: str
    available: bool
    priority: int = 0
    persisted: bool = True
    data_dir: str = ""
    metadata_db: str = ""


@dataclass
class StatusData:
    credential_storage: StorageStatus
    accounts_count: int
    accounts: list[AccountSummary]


@dataclass
class AccountListData:
    accounts: list[AccountSummary]


@dataclass
class AuthStatusResult:
    platform: str
    user_id: str
    name: str
    valid: bool


@dataclass
class LoginResult:
    platform: str
    user_id: str
    name: str
    school: str | None
    persisted: bool


@dataclass
class RemoveResult:
    platform: str
    account: str
    deleted: bool
