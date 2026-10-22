"""账号数据。"""

from wkhelper.domain.model import Model


class UserInfo(Model):
    """用户信息模型。"""

    id: int | str
    name: str
    school: str | None = None


class AccountSummary(Model):
    """账号元数据摘要（不含秘密）。"""

    platform: str
    user_id: str
    name: str
    school: str | None
    saved_at: float


class StorageStatus(Model):
    """系统凭据存储后端状态。"""

    mode: str
    backend: str
    available: bool
    priority: int | float = 0
    persisted: bool = True
    data_dir: str = ""
    metadata_db: str = ""


class StatusData(Model):
    credential_storage: StorageStatus
    accounts_count: int
    accounts: list[AccountSummary]


class AccountListData(Model):
    accounts: list[AccountSummary]


class AuthStatusResult(Model):
    platform: str
    user_id: str
    name: str
    valid: bool


class LoginResult(Model):
    platform: str
    user_id: str
    name: str
    school: str | None
    persisted: bool


class RemoveResult(Model):
    platform: str
    account: str
    deleted: bool
