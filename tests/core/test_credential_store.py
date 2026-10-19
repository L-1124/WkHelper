"""CredentialStore 测试：内存后端与元数据分离。"""

import sqlite3
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from wkhelper.core.credential_store import CredentialStore, SecretBackend
from wkhelper.core.entities import StorageStatus, UserInfo


class MockKeyringBackend(SecretBackend):
    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    def set_secret(self, service: str, key: str, secret: str) -> None:
        self._store[f"{service}:{key}"] = secret

    def get_secret(self, service: str, key: str) -> str | None:
        return self._store.get(f"{service}:{key}")

    def delete_secret(self, service: str, key: str) -> None:
        self._store.pop(f"{service}:{key}", None)

    def get_status(self) -> StorageStatus:
        return StorageStatus(mode="system", backend="MockKeyring", available=True, persisted=True)


@pytest.fixture
def temp_db_dir() -> Iterator[str]:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
        yield temp_dir


@pytest.fixture
def store(temp_db_dir: str) -> CredentialStore:
    backend = MockKeyringBackend()
    return CredentialStore(data_dir=temp_db_dir, credential_mode="system", backend=backend)


def test_credential_store_save_and_get(store: CredentialStore) -> None:
    user = UserInfo(id="123", name="test_user", school="test_school")
    cookies = {"sessionid": "abc", "csrftoken": "xyz"}

    store.save("ykt", user, cookies)

    retrieved = store.get_cookies("ykt", "123")
    assert retrieved == cookies

    # 验证 SQLite 数据库中绝不包含明文或 Base64 cookies
    conn = sqlite3.connect(store.db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM accounts WHERE platform = 'ykt' AND user_id = '123'")
    row = cursor.fetchone()
    conn.close()

    assert row is not None
    for col in row:
        assert "sessionid" not in str(col)
        assert "abc" not in str(col)


def test_credential_store_list_accounts(store: CredentialStore) -> None:
    user1 = UserInfo(id="123", name="user1", school="school1")
    user2 = UserInfo(id="456", name="user2", school="school2")

    store.save("ykt", user1, {"c": "1"})
    store.save("ykt", user2, {"c": "2"})
    store.save("xtzx", user1, {"c": "3"})

    all_acc = store.list_accounts()
    assert len(all_acc) == 3

    ykt_acc = store.list_accounts("ykt")
    assert len(ykt_acc) == 2
    uids = {account.user_id for account in ykt_acc}
    assert uids == {"123", "456"}

    xtzx_acc = store.list_accounts("xtzx")
    assert len(xtzx_acc) == 1
    assert xtzx_acc[0].user_id == "123"


def test_credential_store_delete(store: CredentialStore) -> None:
    user = UserInfo(id="123", name="test_user")
    store.save("ykt", user, {"sessionid": "abc"})

    assert store.get_cookies("ykt", "123") is not None
    assert store.delete("ykt", "123") is True
    assert store.get_cookies("ykt", "123") is None
    assert len(store.list_accounts("ykt")) == 0


def test_credential_store_session_mode_isolation(temp_db_dir: str) -> None:
    """验证 session 模式完全基于内存，不污染磁盘 metadata.db。"""
    store_session = CredentialStore(data_dir=temp_db_dir, credential_mode="session")
    user = UserInfo(id="session_user", name="临时用户")
    store_session.save("ykt", user, {"s": "temp"})

    assert store_session.get_cookies("ykt", "session_user") == {"s": "temp"}
    # 磁盘上不应该产生 metadata.db
    assert not (Path(temp_db_dir) / "metadata.db").exists()
