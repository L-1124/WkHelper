"""CredentialStore 测试：加密 SQLite 存储、Keyring 统一主密钥管理及安全退化防护。"""

import json
import sqlite3
import tempfile
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from cryptography.fernet import Fernet
from keyring.errors import KeyringError

from wkhelper.domain.accounts import StorageStatus, UserInfo
from wkhelper.domain.errors import AuthError
from wkhelper.infra.credentials import (
    SERVICE_NAME,
    CredentialStore,
    KeyringSecretBackend,
    SecretBackend,
)


class MockKeyringBackend(SecretBackend):
    """用于测试的可注入 Keyring 模拟后端。"""

    def __init__(self) -> None:
        self._storage: dict[tuple[str, str], str] = {}
        self._lock = threading.Lock()

    def set_secret(self, service: str, key: str, secret: str) -> None:
        with self._lock:
            self._storage[(service, key)] = secret

    def get_secret(self, service: str, key: str) -> str | None:
        with self._lock:
            return self._storage.get((service, key))

    def delete_secret(self, service: str, key: str) -> None:
        with self._lock:
            self._storage.pop((service, key), None)

    def get_status(self) -> StorageStatus:
        return StorageStatus(mode="system", backend="MockKeyring", available=True, persisted=True)


@pytest.fixture
def temp_db_dir() -> Iterator[str]:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
        yield temp_dir


@pytest.fixture
def mock_backend() -> MockKeyringBackend:
    return MockKeyringBackend()


@pytest.fixture
def store(temp_db_dir: str, mock_backend: MockKeyringBackend) -> CredentialStore:
    return CredentialStore(data_dir=temp_db_dir, credential_mode="system", backend=mock_backend)


def test_credential_store_save_and_get(store: CredentialStore, mock_backend: MockKeyringBackend) -> None:
    """验证保存并获取 Cookie 正常往返，SQLite 中存储加密 BLOB 且系统 Keyring 仅存短主密钥。"""
    user = UserInfo(id="123", name="test_user", school="test_school")
    cookies = {"sessionid": "sensitive-session-token-123456789", "csrftoken": "sensitive-csrf-token-123456789"}

    store.save("ykt", user, cookies)

    retrieved = store.get_cookies("ykt", "123")
    assert retrieved == cookies

    # 验证 SQLite accounts 数据库行中仅包含密文字节，绝无明文 cookies
    conn = sqlite3.connect(store.db_path)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT platform, user_id, name, school, encrypted_cookies, encryption_version "
        "FROM accounts WHERE platform = 'ykt' AND user_id = '123'"
    )
    row = cursor.fetchone()
    conn.close()

    assert row is not None
    assert row[0] == "ykt"
    assert row[1] == "123"
    assert row[2] == "test_user"
    assert isinstance(row[4], bytes)
    assert row[5] == 1
    # 确保密文字节与明文列中均未出现明文敏感数据
    for col in row[:4]:
        assert "sessionid" not in str(col)
        assert cookies["sessionid"] not in str(col)
    assert cookies["sessionid"].encode() not in row[4]
    assert cookies["csrftoken"].encode() not in row[4]

    # 验证 SecretBackend 中仅存储短 Fernet 主密钥（44 字符），绝不保存明文或密文 Cookies 载荷
    storage = mock_backend._storage
    assert len(storage) == 1
    (srv, key_id), master_key = next(iter(storage.items()))
    assert srv == SERVICE_NAME
    assert key_id.startswith("master:")
    assert len(master_key) == 44
    assert "sessionid" not in master_key


def test_credential_store_large_unicode_cookie_roundtrip_reopen(temp_db_dir: str, mock_backend: MockKeyringBackend) -> None:
    """验证超过 Windows 凭据管理器限制（>2560 字节）的 Unicode Cookie 能安全持久化并在重新打开 store 后无损解密。"""
    store1 = CredentialStore(data_dir=temp_db_dir, credential_mode="system", backend=mock_backend)
    user = UserInfo(id="large_user", name="大型用户")
    large_token = "🔑_Token_" + "测试多字节中文字符串_🌟_繁體中文_日本語_한국어_" * 150 + "_END"
    cookies = {
        "large_sess": large_token,
        "signature": "🔐_Sig_" + "x" * 1000,
    }
    assert len(large_token.encode("utf-8")) > 4000

    store1.save("ykt", user, cookies)

    # 验证 backend 中仅有 44 字节主密钥，不受超大 Cookie 影响
    assert len(mock_backend._storage) == 1
    key_val = next(iter(mock_backend._storage.values()))
    assert len(key_val) == 44

    # 重新初始化全新的 CredentialStore 实例（模拟进程重启），验证可正常解密
    store2 = CredentialStore(data_dir=temp_db_dir, credential_mode="system", backend=mock_backend)
    retrieved = store2.get_cookies("ykt", "large_user")
    assert retrieved == cookies


def test_credential_store_list_accounts(store: CredentialStore) -> None:
    """验证列出账号元数据与按平台过滤。"""
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
    """验证原子删除账号与对应凭据。"""
    user = UserInfo(id="123", name="test_user")
    store.save("ykt", user, {"sessionid": "abc"})

    assert store.get_cookies("ykt", "123") is not None
    assert store.delete("ykt", "123") is True
    assert store.get_cookies("ykt", "123") is None
    assert len(store.list_accounts("ykt")) == 0
    # 再次删除不存在账号返回 False
    assert store.delete("ykt", "123") is False


def test_credential_store_legacy_migration_success(temp_db_dir: str, mock_backend: MockKeyringBackend) -> None:
    """旧版本 accounts 表（无 encrypted_cookies）及 Keyring 明文 Secret 懒加载迁移成功，且迁移后删除旧 Secret。"""
    db_path = Path(temp_db_dir) / "metadata.db"
    conn = sqlite3.connect(db_path)
    # 创建旧 schema accounts 表
    conn.execute("""
        CREATE TABLE accounts (
            platform TEXT,
            user_id TEXT,
            name TEXT,
            school TEXT,
            key_id TEXT,
            saved_at REAL,
            PRIMARY KEY (platform, user_id)
        )
    """)
    conn.execute(
        "INSERT INTO accounts (platform, user_id, name, school, key_id, saved_at) VALUES (?, ?, ?, ?, ?, ?)",
        ("ykt", "legacy_user", "旧用户", "旧学校", "ykt:legacy_user:cookies", 1000.0),
    )
    conn.commit()
    conn.close()

    # 在 backend 中存入旧明文 JSON
    legacy_cookies = {"sessionid": "legacy_token_123", "client": "old"}
    mock_backend.set_secret(SERVICE_NAME, "ykt:legacy_user:cookies", json.dumps(legacy_cookies))

    # 启动 store，触发 schema 升级与数据迁移
    store = CredentialStore(data_dir=temp_db_dir, credential_mode="system", backend=mock_backend)

    # 读取旧账号触发懒迁移
    retrieved = store.get_cookies("ykt", "legacy_user")
    assert retrieved == legacy_cookies

    # 验证 SQLite 中已经填充密文与版本号
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT encrypted_cookies, encryption_version FROM accounts WHERE platform = 'ykt' AND user_id = 'legacy_user'")
    row = cur.fetchone()
    conn.close()

    assert row is not None
    assert isinstance(row[0], bytes)
    assert row[1] == 1

    # 验证旧 Keyring 凭据已被清理，避免残留明文
    assert mock_backend.get_secret(SERVICE_NAME, "ykt:legacy_user:cookies") is None

    # 后续再次读取依然正常（基于 SQLite 密文解密）
    assert store.get_cookies("ykt", "legacy_user") == legacy_cookies


def test_credential_store_legacy_migration_commit_failure_preserves_legacy(
    temp_db_dir: str, mock_backend: MockKeyringBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    """旧版本迁移写入 SQLite 提交失败时，回滚事务且绝不删除旧 Keyring Secret。"""
    db_path = Path(temp_db_dir) / "metadata.db"
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE accounts (
            platform TEXT,
            user_id TEXT,
            name TEXT,
            school TEXT,
            key_id TEXT,
            saved_at REAL,
            PRIMARY KEY (platform, user_id)
        )
    """)
    conn.execute(
        "INSERT INTO accounts (platform, user_id, name, school, key_id, saved_at) VALUES (?, ?, ?, ?, ?, ?)",
        ("ykt", "fail_user", "故障用户", "学校", "ykt:fail_user:cookies", 1000.0),
    )
    conn.commit()
    conn.close()

    legacy_cookies = {"sessionid": "safe_token"}
    mock_backend.set_secret(SERVICE_NAME, "ykt:fail_user:cookies", json.dumps(legacy_cookies))

    store = CredentialStore(data_dir=temp_db_dir, credential_mode="system", backend=mock_backend)

    real_get_conn = store._get_connection

    def faulty_get_conn() -> FaultInjectionConnection:
        real = real_get_conn()
        return FaultInjectionConnection(real, fail_commit=True)

    monkeypatch.setattr(store, "_get_connection", faulty_get_conn)

    with pytest.raises(sqlite3.OperationalError):
        store.get_cookies("ykt", "fail_user")

    monkeypatch.undo()

    # 关键验证：旧 Secret 必须完好无损，未被提前删除
    assert mock_backend.get_secret(SERVICE_NAME, "ykt:fail_user:cookies") == json.dumps(legacy_cookies)

    # 验证 SQLite 未被更新为已加密状态
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT encrypted_cookies FROM accounts WHERE platform = 'ykt' AND user_id = 'fail_user'")
    row = cur.fetchone()
    conn.close()
    assert row[0] is None

    # 解除故障后，重试应能成功迁移并清理旧 Secret
    assert store.get_cookies("ykt", "fail_user") == legacy_cookies
    assert mock_backend.get_secret(SERVICE_NAME, "ykt:fail_user:cookies") is None


def test_credential_store_legacy_delete_cleans_up_keyring(temp_db_dir: str, mock_backend: MockKeyringBackend) -> None:
    """删除未迁移的旧账号时，SQLite 元数据与 Keyring 旧 Secret 均被清理。"""
    db_path = Path(temp_db_dir) / "metadata.db"
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE accounts (
            platform TEXT,
            user_id TEXT,
            name TEXT,
            school TEXT,
            key_id TEXT,
            saved_at REAL,
            encrypted_cookies BLOB,
            encryption_version INTEGER,
            PRIMARY KEY (platform, user_id)
        )
    """)
    conn.execute(
        "INSERT INTO accounts (platform, user_id, name, school, key_id, saved_at) VALUES (?, ?, ?, ?, ?, ?)",
        ("ykt", "del_legacy", "旧用户", None, "ykt:del_legacy:cookies", 1000.0),
    )
    conn.commit()
    conn.close()

    mock_backend.set_secret(SERVICE_NAME, "ykt:del_legacy:cookies", json.dumps({"token": "123"}))

    store = CredentialStore(data_dir=temp_db_dir, credential_mode="system", backend=mock_backend)
    assert store.delete("ykt", "del_legacy") is True
    assert mock_backend.get_secret(SERVICE_NAME, "ykt:del_legacy:cookies") is None
    assert store.get_cookies("ykt", "del_legacy") is None


def test_credential_store_get_cookies_nonexistent_metadata_returns_none_without_fallback(
    store: CredentialStore, mock_backend: MockKeyringBackend
) -> None:
    """当 accounts 元数据中不存在账号记录时，绝不能从 Keyring 残留 key 回退读取，避免幽灵账号复活。"""
    mock_backend.set_secret(SERVICE_NAME, "ykt:ghost_user:cookies", json.dumps({"token": "ghost"}))
    assert store.get_cookies("ykt", "ghost_user") is None


@pytest.mark.parametrize("missing", ["keyring", "config"])
def test_credential_store_missing_master_key_fails_closed(store: CredentialStore, mock_backend: MockKeyringBackend, missing: str) -> None:
    """数据库已初始化但系统 Keyring 丢失主密钥时，获取与保存均必须 Fail Closed 抛出 AuthError，且不得静默覆写数据。"""
    user = UserInfo(id="u1", name="用户1")
    store.save("ykt", user, {"token": "secret1"})

    if missing == "keyring":
        mock_backend._storage.clear()
    else:
        with sqlite3.connect(store.db_path) as conn:
            conn.execute("DELETE FROM storage_config")

    with pytest.raises(AuthError):
        store.get_cookies("ykt", "u1")

    # 尝试保存也必须 Fail Closed，不得静默使用新密钥覆写并破坏原有已加密数据
    with pytest.raises(AuthError):
        store.save("ykt", UserInfo(id="u2", name="用户2"), {"token": "secret2"})


def test_credential_store_wrong_master_key_fails_closed(store: CredentialStore, mock_backend: MockKeyringBackend) -> None:
    """当 Keyring 中的主密钥被替换为错误的有效 Fernet 密钥时，解密校验失败必须抛出 AuthError。"""
    user = UserInfo(id="u1", name="用户1")
    store.save("ykt", user, {"token": "secret1"})

    # 替换为另一个不同的有效 Fernet 密钥
    (srv, key_id), _ = next(iter(mock_backend._storage.items()))
    mock_backend.set_secret(srv, key_id, Fernet.generate_key().decode("ascii"))

    with pytest.raises(AuthError):
        store.get_cookies("ykt", "u1")
    with pytest.raises(AuthError):
        store.save("ykt", user, {"token": "replacement"})


def test_credential_store_malformed_master_key_fails_closed(store: CredentialStore, mock_backend: MockKeyringBackend) -> None:
    """当 Keyring 中的主密钥为格式错误的非 Base64/非 Fernet 字符串时，必须抛出 AuthError。"""
    user = UserInfo(id="u1", name="用户1")
    store.save("ykt", user, {"token": "secret1"})

    (srv, key_id), _ = next(iter(mock_backend._storage.items()))
    mock_backend.set_secret(srv, key_id, "malformed-not-a-fernet-key")

    with pytest.raises(AuthError):
        store.get_cookies("ykt", "u1")

    with pytest.raises(AuthError):
        store.save("ykt", UserInfo(id="u2", name="用户2"), {"token": "secret2"})


def test_credential_store_corrupted_ciphertext_fails_closed(store: CredentialStore) -> None:
    """密文字节损坏时，解密必须 Fail Closed 抛出 AuthError。"""
    user = UserInfo(id="u1", name="用户1")
    store.save("ykt", user, {"token": "secret1"})

    conn = sqlite3.connect(store.db_path)
    conn.execute(
        "UPDATE accounts SET encrypted_cookies = ? WHERE platform = 'ykt' AND user_id = 'u1'",
        (b"corrupted_garbage_bytes",),
    )
    conn.commit()
    conn.close()

    with pytest.raises(AuthError):
        store.get_cookies("ykt", "u1")


def test_credential_store_identity_swap_tamper_fails_closed(store: CredentialStore) -> None:
    """跨账号密文互换（Row Swap / Identity Tampering）时，由于绑定了 platform+user_id，必须 Fail Closed 抛出 AuthError。"""
    user_a = UserInfo(id="user_a", name="用户A")
    user_b = UserInfo(id="user_b", name="用户B")
    store.save("ykt", user_a, {"token": "secret_a"})
    store.save("ykt", user_b, {"token": "secret_b"})

    conn = sqlite3.connect(store.db_path)
    cur = conn.cursor()
    cur.execute("SELECT encrypted_cookies FROM accounts WHERE platform = 'ykt' AND user_id = 'user_a'")
    cipher_a = cur.fetchone()[0]

    # 将 user_a 的密文替换到 user_b 的行
    conn.execute(
        "UPDATE accounts SET encrypted_cookies = ? WHERE platform = 'ykt' AND user_id = 'user_b'",
        (cipher_a,),
    )
    conn.commit()
    conn.close()

    with pytest.raises(AuthError):
        store.get_cookies("ykt", "user_b")


def test_credential_store_unknown_encryption_version_fails_closed(store: CredentialStore) -> None:
    """遇到未知的加密版本号时，必须 Fail Closed 抛出 AuthError。"""
    user = UserInfo(id="u1", name="用户1")
    store.save("ykt", user, {"token": "secret1"})

    conn = sqlite3.connect(store.db_path)
    conn.execute("UPDATE accounts SET encryption_version = 999 WHERE platform = 'ykt' AND user_id = 'u1'")
    conn.commit()
    conn.close()

    with pytest.raises(AuthError):
        store.get_cookies("ykt", "u1")


def test_credential_store_concurrent_first_saves(temp_db_dir: str, mock_backend: MockKeyringBackend) -> None:
    """多线程同时对全新数据库执行首次 save 时，通过 BEGIN IMMEDIATE 保证生成且仅生成单一一致的主密钥，且所有凭据均能正常解密。"""
    barrier = threading.Barrier(4)

    def worker(idx: int) -> None:
        store = CredentialStore(data_dir=temp_db_dir, credential_mode="system", backend=mock_backend)
        barrier.wait()
        user = UserInfo(id=f"user_{idx}", name=f"User {idx}")
        store.save("ykt", user, {f"k_{idx}": f"v_{idx}"})

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(worker, i) for i in range(4)]
        for f in futures:
            f.result()

    # 验证 storage_config 只有一条记录
    db_path = Path(temp_db_dir) / "metadata.db"
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT id, key_id FROM storage_config")
    configs = cur.fetchall()
    conn.close()

    assert len(configs) == 1
    assert configs[0][0] == 1
    config_key_id = configs[0][1]

    # 验证 backend 中仅有该主密钥
    assert len(mock_backend._storage) == 1
    assert (SERVICE_NAME, config_key_id) in mock_backend._storage

    # 验证所有 4 个账号都能被读取并成功解密
    verify_store = CredentialStore(data_dir=temp_db_dir, credential_mode="system", backend=mock_backend)
    for i in range(4):
        assert verify_store.get_cookies("ykt", f"user_{i}") == {f"k_{i}": f"v_{i}"}


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
        normalized = sql.strip().upper()
        if normalized == "ROLLBACK":
            self.rollback_called = True
        if self.fail_execute and normalized not in {"BEGIN IMMEDIATE", "BEGIN", "ROLLBACK"}:
            raise sqlite3.OperationalError("injected execute failure")
        if self.fail_commit and normalized == "COMMIT":
            raise sqlite3.OperationalError("injected commit failure")
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

    def __enter__(self) -> "FaultInjectionConnection":
        return self

    def __exit__(self, exc_type: Any, _exc_val: Any, _exc_tb: Any) -> None:
        if exc_type is not None:
            self.rollback()
        else:
            self.commit()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)


@pytest.mark.parametrize("operation", ["create", "overwrite", "delete"])
def test_credential_store_save_db_failure_rollback(store: CredentialStore, monkeypatch: pytest.MonkeyPatch, operation: str) -> None:
    """保存凭据时若 SQLite 提交失败，事务应完整回滚，数据库不残留脏数据。"""
    user = UserInfo(id="rollback_user", name="回滚用户")
    if operation != "create":
        store.save("ykt", user, {"token": "original"})
    real_get_conn = store._get_connection

    faulty_conn: FaultInjectionConnection | None = None

    def faulty_get_conn() -> FaultInjectionConnection:
        nonlocal faulty_conn
        real = real_get_conn()
        faulty_conn = FaultInjectionConnection(real, fail_commit=True)
        return faulty_conn

    monkeypatch.setattr(store, "_get_connection", faulty_get_conn)

    with pytest.raises(sqlite3.OperationalError):
        if operation == "delete":
            store.delete("ykt", user.id)
        else:
            store.save("ykt", user, {"token": "replacement"})

    monkeypatch.undo()

    assert faulty_conn is not None
    assert faulty_conn.rollback_called is True
    expected = None if operation == "create" else {"token": "original"}
    assert store.get_cookies("ykt", user.id) == expected
    assert len(store.list_accounts("ykt")) == (0 if operation == "create" else 1)
    assert store.get_default_account() == (None if operation == "create" else ("ykt", user.id))


def test_credential_store_session_mode_isolation(temp_db_dir: str) -> None:
    """验证 session 模式完全基于内存，不污染磁盘 metadata.db，且不写入系统 Keyring。"""
    store_session = CredentialStore(data_dir=temp_db_dir, credential_mode="session")
    user = UserInfo(id="session_user", name="临时用户")
    store_session.save("ykt", user, {"s": "temp"})

    assert store_session.get_cookies("ykt", "session_user") == {"s": "temp"}
    # 磁盘上不应该产生 metadata.db
    assert not (Path(temp_db_dir) / "metadata.db").exists()


def test_credential_store_session_mode_rollback_and_reuse(temp_db_dir: str) -> None:
    """session 内存模式下发生异常回滚后，内存连接状态保持健康且可继续正常使用。"""
    store_session = CredentialStore(data_dir=temp_db_dir, credential_mode="session")

    assert store_session._memory_conn is not None
    real_memory_conn = store_session._memory_conn

    faulty_conn = FaultInjectionConnection(real_memory_conn, fail_execute=True)
    store_session._memory_conn = faulty_conn  # type: ignore[assignment]

    user1 = UserInfo(id="user1", name="User 1")
    with pytest.raises(sqlite3.OperationalError):
        store_session.save("ykt", user1, {"token": "1"})

    # 恢复真实内存连接，验证后续操作可以正常执行
    store_session._memory_conn = real_memory_conn
    assert store_session.get_cookies("ykt", "user1") is None
    user2 = UserInfo(id="user2", name="User 2")
    store_session.save("ykt", user2, {"token": "2"})

    assert store_session.get_cookies("ykt", "user2") == {"token": "2"}
    accounts = store_session.list_accounts("ykt")
    assert len(accounts) == 1
    assert accounts[0].user_id == "user2"


def test_keyring_backend_exceptions_map_to_auth_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """KeyringSecretBackend 在底层抛出 KeyringError 或系统原生异常时，统一映射为 AuthError 并保留因果链，且不泄露 payload。"""
    backend = KeyringSecretBackend()

    def raise_keyring_error(*args: Any, **kwargs: Any) -> None:
        raise KeyringError("OS keyring daemon unavailable")

    monkeypatch.setattr(backend._kr, "get_password", raise_keyring_error)
    monkeypatch.setattr(backend._kr, "set_password", raise_keyring_error)

    with pytest.raises(AuthError) as get_exc:
        backend.get_secret(SERVICE_NAME, "test_key")
    assert isinstance(get_exc.value.__cause__, KeyringError)

    secret_payload = "SUPER_SECRET_PAYLOAD_STRING"
    with pytest.raises(AuthError) as set_exc:
        backend.set_secret(SERVICE_NAME, "test_key", secret_payload)
    assert isinstance(set_exc.value.__cause__, KeyringError)
    assert secret_payload not in str(set_exc.value)

    # 原生系统异常映射测试
    class NativePlatformCredentialError(RuntimeError):
        pass

    def raise_native_error(*args: Any, **kwargs: Any) -> None:
        raise NativePlatformCredentialError("Native platform credential error 1312")

    monkeypatch.setattr(backend._kr, "get_password", raise_native_error)
    with pytest.raises(AuthError) as native_exc:
        backend.get_secret(SERVICE_NAME, "test_key")
    assert isinstance(native_exc.value.__cause__, NativePlatformCredentialError)


@pytest.mark.parametrize("priority", [5.1, 4.9, 4.8])
def test_keyring_fractional_priority_survives_status_reconstruction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, priority: float
) -> None:
    """验证 Keyring 浮点优先级能正确在 StorageStatus 重构中保持。"""
    from types import SimpleNamespace

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


def test_default_account_switch_relogin_and_reopen(store: CredentialStore) -> None:
    store.save("ykt", UserInfo(id="a", name="A"), {"token": "a"})
    store.save("icourse163", UserInfo(id="b", name="B"), {"token": "b"})
    assert store.get_default_account() == ("ykt", "a")
    store.set_default_account("icourse163", "b")
    store.save("icourse163", UserInfo(id="b", name="B renewed"), {"token": "renewed"})
    reopened = CredentialStore(data_dir=store.data_dir, backend=store.backend)
    assert reopened.get_default_account() == ("icourse163", "b")
    assert reopened.get_cookies("icourse163", "b") == {"token": "renewed"}
    assert {(a.platform, a.user_id) for a in reopened.list_accounts() if a.is_default} == {("icourse163", "b")}


@pytest.mark.parametrize("remaining", [0, 1, 2])
def test_deleting_default_only_adopts_unique_remaining_account(store: CredentialStore, remaining: int) -> None:
    store.save("ykt", UserInfo(id="default", name="Default"), {"token": "default"})
    for index in range(remaining):
        store.save("icourse163", UserInfo(id=str(index), name=str(index)), {"token": str(index)})
    assert store.delete("ykt", "default")
    assert store.get_default_account() == (("icourse163", "0") if remaining == 1 else None)


def test_default_clear_and_failed_switch_preserve_selection(store: CredentialStore) -> None:
    store.save("ykt", UserInfo(id="a", name="A"), {"token": "a"})
    store.save("icourse163", UserInfo(id="b", name="B"), {"token": "b"})
    with pytest.raises(AuthError):
        store.set_default_account("ykt", "missing")
    assert store.get_default_account() == ("ykt", "a")
    store.clear_default_account()
    assert store.delete("ykt", "a")
    assert store.get_default_account() is None
    assert not any(account.is_default for account in store.list_accounts())


def test_switch_commit_failure_preserves_previous_default(store: CredentialStore, monkeypatch: pytest.MonkeyPatch) -> None:
    store.save("ykt", UserInfo(id="a", name="A"), {"token": "a"})
    store.save("icourse163", UserInfo(id="b", name="B"), {"token": "b"})
    real_get_connection = store._get_connection
    monkeypatch.setattr(store, "_get_connection", lambda: FaultInjectionConnection(real_get_connection(), fail_commit=True))
    with pytest.raises(sqlite3.OperationalError):
        store.set_default_account("icourse163", "b")
    monkeypatch.undo()
    assert store.get_default_account() == ("ykt", "a")
