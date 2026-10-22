"""CredentialStore 测试：内存后端与元数据分离。"""

import sqlite3
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from wkhelper.domain.accounts import StorageStatus, UserInfo
from wkhelper.infra.credentials import CredentialStore, SecretBackend


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


class FaultInjectionConnection:
    """包装真实 SQLite 连接以注入执行或提交故障，并观测回滚调用。"""

    def __init__(
        self,
        real_conn: sqlite3.Connection,
        fail_execute: bool = False,
        fail_commit: bool = False,
    ) -> None:
        self._conn = real_conn
        self.fail_execute = fail_execute
        self.fail_commit = fail_commit
        self.rollback_called = False

    def execute(self, sql: str, parameters: Any = ()) -> sqlite3.Cursor:
        if self.fail_execute:
            raise sqlite3.OperationalError("injected execute failure")
        return self._conn.execute(sql, parameters)

    def commit(self) -> None:
        if self.fail_commit:
            raise sqlite3.OperationalError("injected commit failure")
        self._conn.commit()

    def rollback(self) -> None:
        self.rollback_called = True
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()

    def cursor(self) -> sqlite3.Cursor:
        return self._conn.cursor()


class FaultyBackend(MockKeyringBackend):
    """支持在指定调用序号注入异常的测试后端。"""

    def __init__(
        self,
        fail_set_on_call: int | None = None,
        fail_delete_on_call: int | None = None,
    ) -> None:
        super().__init__()
        self.fail_set_on_call = fail_set_on_call
        self.fail_delete_on_call = fail_delete_on_call
        self._set_count = 0
        self._delete_count = 0

    def set_secret(self, service: str, key: str, secret: str) -> None:
        self._set_count += 1
        if self.fail_set_on_call is not None and self._set_count == self.fail_set_on_call:
            raise RuntimeError("injected set_secret compensation failure")
        super().set_secret(service, key, secret)

    def delete_secret(self, service: str, key: str) -> None:
        self._delete_count += 1
        if self.fail_delete_on_call is not None and self._delete_count == self.fail_delete_on_call:
            raise RuntimeError("injected delete_secret compensation failure")
        super().delete_secret(service, key)


@pytest.mark.parametrize("failure_point", ["connect", "execute", "commit"])
def test_credential_store_save_new_account_failure_compensation(
    store: CredentialStore,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    """新账号保存失败时（连接/执行/提交），补偿机制精准删除新秘密并回滚。"""
    user = UserInfo(id="new_user", name="New User")
    cookies = {"token": "secret_val"}

    faulty_conn: FaultInjectionConnection | None = None
    if failure_point == "connect":
        monkeypatch.setattr(
            store,
            "_get_connection",
            lambda: (_ for _ in ()).throw(sqlite3.OperationalError("injected connect failure")),
        )
    else:
        real_conn = sqlite3.connect(store.db_path)
        faulty_conn = FaultInjectionConnection(
            real_conn,
            fail_execute=(failure_point == "execute"),
            fail_commit=(failure_point == "commit"),
        )
        monkeypatch.setattr(store, "_get_connection", lambda: faulty_conn)

    with pytest.raises(sqlite3.OperationalError):
        store.save("ykt", user, cookies)
    monkeypatch.undo()

    if failure_point in {"execute", "commit"}:
        assert faulty_conn is not None
        assert faulty_conn.rollback_called is True
    # 失败后，新秘密必须已被补偿删除，元数据为空，状态一致
    assert store.get_cookies("ykt", "new_user") is None
    assert len(store.list_accounts("ykt")) == 0


@pytest.mark.parametrize("failure_point", ["connect", "execute", "commit"])
def test_credential_store_save_overwrite_account_failure_compensation(
    store: CredentialStore,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    """覆盖已有账号保存失败时，补偿机制精准恢复旧秘密，元数据保持不变。"""
    user_old = UserInfo(id="existing_user", name="Old Name")
    cookies_old = {"token": "old_secret"}
    store.save("ykt", user_old, cookies_old)

    user_new = UserInfo(id="existing_user", name="New Name")
    cookies_new = {"token": "new_secret"}
    faulty_conn: FaultInjectionConnection | None = None
    if failure_point == "connect":
        monkeypatch.setattr(
            store,
            "_get_connection",
            lambda: (_ for _ in ()).throw(sqlite3.OperationalError("injected connect failure")),
        )
    else:
        real_conn = sqlite3.connect(store.db_path)
        faulty_conn = FaultInjectionConnection(
            real_conn,
            fail_execute=(failure_point == "execute"),
            fail_commit=(failure_point == "commit"),
        )
        monkeypatch.setattr(store, "_get_connection", lambda: faulty_conn)

    with pytest.raises(sqlite3.OperationalError):
        store.save("ykt", user_new, cookies_new)
    monkeypatch.undo()

    if failure_point in {"execute", "commit"}:
        assert faulty_conn is not None
        assert faulty_conn.rollback_called is True
    # 失败后旧秘密精准恢复，元数据保持原样
    assert store.get_cookies("ykt", "existing_user") == cookies_old
    accounts = store.list_accounts("ykt")
    assert len(accounts) == 1
    assert accounts[0].name == "Old Name"


def test_credential_store_save_compensation_failure_chaining(
    temp_db_dir: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """保存时若补偿过程也发生异常，不可吞掉错误，保留异常因果链。"""
    # 第一次 set_secret 是正常写入，补偿时的 delete_secret 失败
    backend = FaultyBackend(fail_delete_on_call=1)
    faulty_store = CredentialStore(data_dir=temp_db_dir, credential_mode="system", backend=backend)

    monkeypatch.setattr(
        faulty_store,
        "_get_connection",
        lambda: (_ for _ in ()).throw(sqlite3.OperationalError("db connect failure")),
    )

    user = UserInfo(id="fail_comp_user", name="User")
    with pytest.raises(RuntimeError) as exc_info:
        faulty_store.save("ykt", user, {"token": "abc"})

    assert "injected delete_secret compensation failure" in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, sqlite3.OperationalError)
    assert "db connect failure" in str(exc_info.value.__cause__)


@pytest.mark.parametrize("failure_point", ["connect", "execute", "commit"])
def test_credential_store_delete_failure_compensation(
    store: CredentialStore,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    """删除账号时若 DB 失败（连接/执行/提交），补偿机制精准恢复秘密，元数据保持不变。"""
    user = UserInfo(id="del_user", name="Del User")
    cookies = {"token": "existing_token"}
    store.save("ykt", user, cookies)

    faulty_conn: FaultInjectionConnection | None = None
    if failure_point == "connect":
        monkeypatch.setattr(
            store,
            "_get_connection",
            lambda: (_ for _ in ()).throw(sqlite3.OperationalError("injected connect failure")),
        )
    else:
        real_conn = sqlite3.connect(store.db_path)
        faulty_conn = FaultInjectionConnection(
            real_conn,
            fail_execute=(failure_point == "execute"),
            fail_commit=(failure_point == "commit"),
        )
        monkeypatch.setattr(store, "_get_connection", lambda: faulty_conn)

    with pytest.raises(sqlite3.OperationalError):
        store.delete("ykt", "del_user")
    monkeypatch.undo()

    if failure_point in {"execute", "commit"}:
        assert faulty_conn is not None
        assert faulty_conn.rollback_called is True
    # 失败后秘密精准恢复，元数据仍存在
    assert store.get_cookies("ykt", "del_user") == cookies
    accounts = store.list_accounts("ykt")
    assert len(accounts) == 1
    assert accounts[0].user_id == "del_user"


def test_credential_store_delete_compensation_failure_chaining(
    temp_db_dir: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """删除账号时若 DB 失败且补偿写入秘密也失败，不可吞掉错误，保留异常因果链。"""
    # 第一次 set_secret 是初始 save，第二次 set_secret 是 delete 补偿恢复旧秘密
    backend = FaultyBackend(fail_set_on_call=2)
    faulty_store = CredentialStore(data_dir=temp_db_dir, credential_mode="system", backend=backend)

    user = UserInfo(id="del_fail_user", name="User")
    faulty_store.save("ykt", user, {"token": "secret"})

    monkeypatch.setattr(
        faulty_store,
        "_get_connection",
        lambda: (_ for _ in ()).throw(sqlite3.OperationalError("db connect failure")),
    )

    with pytest.raises(RuntimeError) as exc_info:
        faulty_store.delete("ykt", "del_fail_user")

    assert "injected set_secret compensation failure" in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, sqlite3.OperationalError)
    assert "db connect failure" in str(exc_info.value.__cause__)


def test_credential_store_session_mode_rollback_and_reuse(temp_db_dir: str) -> None:
    """session 内存模式下发生异常回滚后，内存连接状态保持健康且可继续正常使用。"""
    store_session = CredentialStore(data_dir=temp_db_dir, credential_mode="session")

    # 手动触发一次 execute 异常
    assert store_session._memory_conn is not None
    real_memory_conn = store_session._memory_conn

    faulty_conn = FaultInjectionConnection(real_memory_conn, fail_execute=True)
    store_session._memory_conn = faulty_conn  # type: ignore[assignment]

    user1 = UserInfo(id="user1", name="User 1")
    with pytest.raises(sqlite3.OperationalError):
        store_session.save("ykt", user1, {"token": "1"})

    assert faulty_conn.rollback_called is True
    assert store_session.get_cookies("ykt", "user1") is None

    # 恢复真实内存连接，验证后续操作可以正常执行
    store_session._memory_conn = real_memory_conn
    user2 = UserInfo(id="user2", name="User 2")
    store_session.save("ykt", user2, {"token": "2"})

    assert store_session.get_cookies("ykt", "user2") == {"token": "2"}
    accounts = store_session.list_accounts("ykt")
    assert len(accounts) == 1
    assert accounts[0].user_id == "user2"


@pytest.mark.parametrize("priority", [5.1, 4.9, 4.8])
def test_keyring_fractional_priority_survives_status_reconstruction(tmp_path, monkeypatch, priority) -> None:
    from types import SimpleNamespace

    from wkhelper.infra.credentials import KeyringSecretBackend

    monkeypatch.setattr("wkhelper.infra.credentials.keyring.get_keyring", lambda: SimpleNamespace(priority=priority))
    backend = KeyringSecretBackend()
    original = backend.get_status()
    store = CredentialStore(data_dir=str(tmp_path), credential_mode="system", backend=backend)
    status = store.get_storage_status()
    assert status.priority == priority
    assert status.available is True
    assert status.data_dir == str(tmp_path)
    assert status.metadata_db == str(store.db_path)
    assert original.data_dir == "" and original.metadata_db == ""
