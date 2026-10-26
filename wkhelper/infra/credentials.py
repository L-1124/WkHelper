"""系统安全凭据存储管理。"""

import logging
import os
import sqlite3
import time
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from threading import Lock
from typing import Annotated

import keyring
from cryptography.fernet import Fernet, InvalidToken
from keyring.errors import PasswordDeleteError
from pydantic import BeforeValidator, TypeAdapter

from wkhelper.domain.accounts import AccountSummary, StorageStatus, UserInfo
from wkhelper.domain.errors import AuthError
from wkhelper.domain.model import Model
from wkhelper.infra.serialization import decode_json, encode_json

logger = logging.getLogger(__name__)

SERVICE_NAME = "wkhelper"
_COOKIES_ADAPTER: TypeAdapter[dict[str, str]] = TypeAdapter(dict[str, Annotated[str, BeforeValidator(str)]])


class _EncryptedCookiePayload(Model):
    version: int = 1
    platform: str
    user_id: str
    cookies: dict[str, str]


def _build_fernet(key: str) -> Fernet:
    try:
        return Fernet(key.encode("ascii"))
    except Exception:
        raise AuthError("系统凭据存储中的主密钥格式无效") from None


def get_default_data_dir() -> Path:
    """获取默认应用数据目录。"""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    path = Path(base) / "wkhelper" / "agent-v1"
    path.mkdir(parents=True, exist_ok=True)
    return path


class SecretBackend(ABC):
    """秘密存储后端抽象。"""

    @abstractmethod
    def set_secret(self, service: str, key: str, secret: str) -> None: ...

    @abstractmethod
    def get_secret(self, service: str, key: str) -> str | None: ...

    @abstractmethod
    def delete_secret(self, service: str, key: str) -> None: ...

    @abstractmethod
    def get_status(self) -> StorageStatus: ...


class KeyringSecretBackend(SecretBackend):
    """基于操作系统 Keyring 的安全秘密存储后端。"""

    def __init__(self) -> None:
        self._kr = keyring.get_keyring()

    def set_secret(self, service: str, key: str, secret: str) -> None:
        try:
            self._kr.set_password(service, key, secret)
        except Exception as exc:
            raise AuthError("写入系统凭据存储失败") from exc

    def get_secret(self, service: str, key: str) -> str | None:
        try:
            return self._kr.get_password(service, key)
        except Exception as exc:
            raise AuthError("读取系统凭据存储失败") from exc

    def delete_secret(self, service: str, key: str) -> None:
        try:
            self._kr.delete_password(service, key)
        except PasswordDeleteError:
            pass
        except Exception as exc:
            raise AuthError("从系统凭据管理器删除凭据失败") from exc

    def get_status(self) -> StorageStatus:
        priority = getattr(self._kr, "priority", 0)
        return StorageStatus(
            mode="system",
            backend=type(self._kr).__name__,
            available=priority > 0,
            priority=priority,
            persisted=True,
        )


class MemorySecretBackend(SecretBackend):
    """仅内存会话秘密存储后端（不落盘）。"""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self._lock = Lock()

    def set_secret(self, service: str, key: str, secret: str) -> None:
        with self._lock:
            self._store[f"{service}:{key}"] = secret

    def get_secret(self, service: str, key: str) -> str | None:
        with self._lock:
            return self._store.get(f"{service}:{key}")

    def delete_secret(self, service: str, key: str) -> None:
        with self._lock:
            self._store.pop(f"{service}:{key}", None)

    def get_status(self) -> StorageStatus:
        return StorageStatus(
            mode="session",
            backend="MemorySecretBackend",
            available=True,
            priority=0,
            persisted=False,
        )


class CredentialStore:
    """账号凭据与元数据存储。"""

    def __init__(
        self,
        data_dir: str | Path | None = None,
        credential_mode: str = "system",
        backend: SecretBackend | None = None,
    ) -> None:
        self.credential_mode = credential_mode
        self._is_session = credential_mode == "session" or isinstance(backend, MemorySecretBackend)

        if backend is not None:
            self.backend = backend
        elif self._is_session:
            self.backend = MemorySecretBackend()
        else:
            self.backend = KeyringSecretBackend()

        self._db_lock = Lock()
        if self._is_session:
            self.data_dir = Path(data_dir) if data_dir else Path(os.environ.get("TEMP", "/tmp"))
            self.db_path = Path(":memory:")
            self._memory_conn: sqlite3.Connection | None = sqlite3.connect(":memory:", check_same_thread=False, isolation_level=None)
        else:
            self.data_dir = Path(data_dir) if data_dir else get_default_data_dir()
            self.data_dir.mkdir(parents=True, exist_ok=True)
            self.db_path = self.data_dir / "metadata.db"
            self._memory_conn = None

        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = self._memory_conn
        if conn is None:
            conn = sqlite3.connect(self.db_path, timeout=30.0, isolation_level=None)
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_db(self) -> None:
        with self._db_lock:
            conn = self._get_connection()
            in_tx = False
            try:
                conn.execute("BEGIN IMMEDIATE")
                in_tx = True
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS accounts (
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
                cursor = conn.execute("PRAGMA table_info(accounts)")
                existing_cols = {row[1] for row in cursor.fetchall()}
                if "encrypted_cookies" not in existing_cols:
                    conn.execute("ALTER TABLE accounts ADD COLUMN encrypted_cookies BLOB")
                if "encryption_version" not in existing_cols:
                    conn.execute("ALTER TABLE accounts ADD COLUMN encryption_version INTEGER")

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS storage_config (
                        id INTEGER PRIMARY KEY CHECK (id = 1),
                        key_id TEXT NOT NULL
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS legacy_tombstones (
                        key_id TEXT PRIMARY KEY
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS active_context (
                        id INTEGER PRIMARY KEY CHECK (id = 1),
                        platform TEXT NOT NULL,
                        user_id TEXT NOT NULL,
                        updated_at REAL NOT NULL,
                        FOREIGN KEY (platform, user_id) REFERENCES accounts(platform, user_id) ON DELETE CASCADE
                    )
                """)
                conn.execute("COMMIT")
                in_tx = False
            except Exception:
                if in_tx:
                    try:
                        conn.execute("ROLLBACK")
                    except Exception:
                        pass
                raise
            finally:
                if self._memory_conn is None:
                    conn.close()

    def _get_or_create_master_key_in_tx(
        self,
        conn: sqlite3.Connection,
        *,
        create_if_missing: bool = False,
    ) -> str:
        cursor = conn.execute("SELECT key_id FROM storage_config WHERE id = 1")
        row = cursor.fetchone()
        if row is not None:
            key_id = str(row[0])
            secret = self.backend.get_secret(SERVICE_NAME, key_id)
            if not secret:
                raise AuthError(f"系统凭据存储中未找到数据库主密钥 ({key_id})，拒绝访问")
            return secret

        cursor = conn.execute("SELECT 1 FROM accounts WHERE encrypted_cookies IS NOT NULL LIMIT 1")
        if cursor.fetchone() is not None:
            raise AuthError("数据库缺少加密存储配置但已存在加密凭据，拒绝重新生成主密钥")

        if not create_if_missing:
            raise AuthError("数据库未初始化加密存储配置")

        key_id = f"master:{uuid.uuid4().hex}"
        fernet_key = Fernet.generate_key().decode("ascii")
        self.backend.set_secret(SERVICE_NAME, key_id, fernet_key)
        conn.execute("INSERT INTO storage_config (id, key_id) VALUES (1, ?)", (key_id,))
        return fernet_key

    def _drain_legacy_tombstones(self, conn: sqlite3.Connection) -> None:
        try:
            cursor = conn.execute("SELECT key_id FROM legacy_tombstones LIMIT 10")
            rows = cursor.fetchall()
            for (key_id,) in rows:
                try:
                    self.backend.delete_secret(SERVICE_NAME, key_id)
                    conn.execute("DELETE FROM legacy_tombstones WHERE key_id = ?", (key_id,))
                except Exception:
                    break
        except Exception:
            pass

    def save(self, platform: str, user_info: UserInfo, cookies: dict[str, str]) -> None:
        """保存凭据。原子性地将加密 Cookie 与元数据持久化至 SQLite。"""
        validated_cookies = _COOKIES_ADAPTER.validate_python(cookies)
        now = time.time()
        uid = str(user_info.id)

        with self._db_lock:
            conn: sqlite3.Connection | None = None
            in_tx = False
            try:
                conn = self._get_connection()
                conn.execute("BEGIN IMMEDIATE")
                in_tx = True

                fernet_key = self._get_or_create_master_key_in_tx(conn, create_if_missing=True)
                fernet = _build_fernet(fernet_key)

                cursor = conn.execute("SELECT encrypted_cookies FROM accounts WHERE encrypted_cookies IS NOT NULL LIMIT 1")
                sample = cursor.fetchone()
                if sample and sample[0]:
                    try:
                        fernet.decrypt(sample[0])
                    except Exception:
                        raise AuthError("系统主密钥与现有凭据不匹配，拒绝操作") from None

                payload = _EncryptedCookiePayload(
                    version=1,
                    platform=platform,
                    user_id=uid,
                    cookies=validated_cookies,
                )
                encrypted_blob = fernet.encrypt(encode_json(payload))

                cursor = conn.execute(
                    "SELECT key_id FROM accounts WHERE platform = ? AND user_id = ?",
                    (platform, uid),
                )
                existing_row = cursor.fetchone()
                if existing_row and existing_row[0]:
                    conn.execute(
                        "INSERT OR REPLACE INTO legacy_tombstones (key_id) VALUES (?)",
                        (str(existing_row[0]),),
                    )

                conn.execute(
                    """
                    INSERT INTO accounts (
                        platform, user_id, name, school, key_id, saved_at, encrypted_cookies, encryption_version
                    ) VALUES (?, ?, ?, ?, NULL, ?, ?, 1)
                    ON CONFLICT (platform, user_id) DO UPDATE SET
                        name = excluded.name, school = excluded.school, key_id = NULL,
                        saved_at = excluded.saved_at, encrypted_cookies = excluded.encrypted_cookies,
                        encryption_version = excluded.encryption_version
                    """,
                    (platform, uid, user_info.name, user_info.school, now, encrypted_blob),
                )
                cursor = conn.execute("""
                    SELECT 1 FROM active_context c
                    JOIN accounts a ON a.platform = c.platform AND a.user_id = c.user_id
                    WHERE c.id = 1
                """)
                if cursor.fetchone() is None:
                    conn.execute(
                        "INSERT OR REPLACE INTO active_context (id, platform, user_id, updated_at) VALUES (1, ?, ?, ?)",
                        (platform, uid, now),
                    )
                conn.execute("COMMIT")
                in_tx = False

                self._drain_legacy_tombstones(conn)
            except Exception:
                if in_tx and conn is not None:
                    try:
                        conn.execute("ROLLBACK")
                    except Exception:
                        pass
                raise
            finally:
                if conn is not None and self._memory_conn is None:
                    conn.close()

    def list_accounts(self, platform: str | None = None) -> list[AccountSummary]:
        """列出所有保存的账号元数据（无敏感 Cookies）。"""
        with self._db_lock:
            conn: sqlite3.Connection | None = None
            try:
                conn = self._get_connection()
                base_sql = """
                    SELECT a.platform, a.user_id, a.name, a.school, a.saved_at, (c.id IS NOT NULL) AS is_default
                    FROM accounts a
                    LEFT JOIN active_context c ON a.platform = c.platform AND a.user_id = c.user_id
                """
                if platform:
                    cursor = conn.execute(f"{base_sql} WHERE a.platform = ? ORDER BY is_default DESC, a.saved_at DESC", (platform,))
                else:
                    cursor = conn.execute(f"{base_sql} ORDER BY is_default DESC, a.saved_at DESC")
                rows = cursor.fetchall()
                return [
                    AccountSummary(
                        platform=str(row[0]),
                        user_id=str(row[1]),
                        name=str(row[2]),
                        school=row[3] if isinstance(row[3], str) else None,
                        saved_at=float(row[4]),
                        is_default=bool(row[5]),
                    )
                    for row in rows
                ]
            finally:
                if conn is not None and self._memory_conn is None:
                    conn.close()

    def get_cookies(self, platform: str, user_id: str | int) -> dict[str, str] | None:
        """获取账号的 Cookie 字典。"""
        uid = str(user_id)
        with self._db_lock:
            conn: sqlite3.Connection | None = None
            in_tx = False
            try:
                conn = self._get_connection()
                conn.execute("BEGIN IMMEDIATE")
                in_tx = True

                cursor = conn.execute(
                    """
                    SELECT platform, user_id, key_id, encrypted_cookies, encryption_version
                    FROM accounts
                    WHERE platform = ? AND user_id = ?
                    """,
                    (platform, uid),
                )
                row = cursor.fetchone()
                if row is None:
                    conn.execute("COMMIT")
                    in_tx = False
                    return None

                row_plat, row_uid, key_id, encrypted_cookies, encryption_version = row

                if encrypted_cookies is not None:
                    if encryption_version != 1:
                        raise AuthError(f"不支持的凭据加密版本: {encryption_version}")

                    fernet_key = self._get_or_create_master_key_in_tx(conn, create_if_missing=False)
                    fernet = _build_fernet(fernet_key)

                    try:
                        decrypted_bytes = fernet.decrypt(encrypted_cookies)
                    except InvalidToken:
                        raise AuthError("凭据密文损坏或认证失败") from None
                    except Exception:
                        raise AuthError("凭据解密失败") from None

                    try:
                        payload = _EncryptedCookiePayload.model_validate_json(decrypted_bytes)
                    except Exception:
                        raise AuthError("凭据载荷校验失败") from None

                    if payload.version != 1:
                        raise AuthError(f"不支持的载荷版本: {payload.version}")

                    if payload.platform != platform or payload.user_id != uid:
                        raise AuthError("凭据数据身份不匹配")

                    if key_id:
                        conn.execute(
                            "INSERT OR REPLACE INTO legacy_tombstones (key_id) VALUES (?)",
                            (str(key_id),),
                        )
                        conn.execute(
                            "UPDATE accounts SET key_id = NULL WHERE platform = ? AND user_id = ?",
                            (platform, uid),
                        )

                    conn.execute("COMMIT")
                    in_tx = False

                    self._drain_legacy_tombstones(conn)
                    return payload.cookies

                if not key_id:
                    conn.execute("COMMIT")
                    in_tx = False
                    return None

                legacy_key_id = str(key_id)
                raw_secret = self.backend.get_secret(SERVICE_NAME, legacy_key_id)
                if not raw_secret:
                    conn.execute("COMMIT")
                    in_tx = False
                    return None

                parsed = decode_json(raw_secret)
                if not isinstance(parsed, dict):
                    conn.execute("COMMIT")
                    in_tx = False
                    return None
                validated_cookies = _COOKIES_ADAPTER.validate_python(parsed)

                fernet_key = self._get_or_create_master_key_in_tx(conn, create_if_missing=True)
                fernet = _build_fernet(fernet_key)

                payload = _EncryptedCookiePayload(
                    version=1,
                    platform=platform,
                    user_id=uid,
                    cookies=validated_cookies,
                )
                encrypted_blob = fernet.encrypt(encode_json(payload))

                conn.execute(
                    "INSERT OR REPLACE INTO legacy_tombstones (key_id) VALUES (?)",
                    (legacy_key_id,),
                )
                conn.execute(
                    """
                    UPDATE accounts
                    SET encrypted_cookies = ?, encryption_version = 1, key_id = NULL
                    WHERE platform = ? AND user_id = ?
                    """,
                    (encrypted_blob, platform, uid),
                )
                conn.execute("COMMIT")
                in_tx = False

                self._drain_legacy_tombstones(conn)
                return validated_cookies
            except Exception:
                if in_tx and conn is not None:
                    try:
                        conn.execute("ROLLBACK")
                    except Exception:
                        pass
                raise
            finally:
                if conn is not None and self._memory_conn is None:
                    conn.close()

    def delete(self, platform: str, user_id: str | int) -> bool:
        """原子删除指定账号的凭据与元数据。"""
        uid = str(user_id)
        with self._db_lock:
            conn: sqlite3.Connection | None = None
            in_tx = False
            try:
                conn = self._get_connection()
                conn.execute("BEGIN IMMEDIATE")
                in_tx = True

                cursor = conn.execute(
                    "SELECT key_id FROM accounts WHERE platform = ? AND user_id = ?",
                    (platform, uid),
                )
                row = cursor.fetchone()
                if row is None:
                    conn.execute("COMMIT")
                    in_tx = False
                    return False

                legacy_key_id = str(row[0]) if row[0] else None
                if legacy_key_id:
                    conn.execute(
                        "INSERT OR REPLACE INTO legacy_tombstones (key_id) VALUES (?)",
                        (legacy_key_id,),
                    )

                removed_default = (
                    conn.execute("DELETE FROM active_context WHERE id = 1 AND platform = ? AND user_id = ?", (platform, uid)).rowcount > 0
                )
                conn.execute(
                    "DELETE FROM accounts WHERE platform = ? AND user_id = ?",
                    (platform, uid),
                )
                if removed_default:
                    remaining = conn.execute("SELECT platform, user_id FROM accounts LIMIT 2").fetchall()
                    if len(remaining) == 1:
                        conn.execute(
                            "INSERT OR REPLACE INTO active_context (id, platform, user_id, updated_at) VALUES (1, ?, ?, ?)",
                            (str(remaining[0][0]), str(remaining[0][1]), time.time()),
                        )
                conn.execute("COMMIT")
                in_tx = False

                self._drain_legacy_tombstones(conn)
                return True
            except Exception:
                if in_tx and conn is not None:
                    try:
                        conn.execute("ROLLBACK")
                    except Exception:
                        pass
                raise
            finally:
                if conn is not None and self._memory_conn is None:
                    conn.close()

    def get_default_account(self) -> tuple[str, str] | None:
        """获取当前默认账号及其平台标识 (platform, user_id)。"""
        with self._db_lock:
            conn = self._get_connection()
            try:
                cursor = conn.execute(
                    """
                    SELECT c.platform, c.user_id
                    FROM active_context c
                    JOIN accounts a ON c.platform = a.platform AND c.user_id = a.user_id
                    WHERE c.id = 1
                    """
                )
                row = cursor.fetchone()
                if row is not None:
                    return str(row[0]), str(row[1])
                conn.execute("""
                    DELETE FROM active_context
                    WHERE id = 1 AND NOT EXISTS (
                        SELECT 1 FROM accounts a
                        WHERE a.platform = active_context.platform AND a.user_id = active_context.user_id
                    )
                """)
                return None
            finally:
                if self._memory_conn is None:
                    conn.close()

    def set_default_account(self, platform: str, user_id: str | int) -> None:
        """切换当前默认账号；目标账号必须已存在于本地存储。"""
        uid = str(user_id)
        now = time.time()
        with self._db_lock:
            conn = self._get_connection()
            in_tx = False
            try:
                conn.execute("BEGIN IMMEDIATE")
                in_tx = True
                cursor = conn.execute(
                    "SELECT 1 FROM accounts WHERE platform = ? AND user_id = ?",
                    (platform, uid),
                )
                if cursor.fetchone() is None:
                    raise AuthError(f"平台 '{platform}' 下未找到已保存账号 '{uid}'，无法设为默认账号")
                conn.execute(
                    "INSERT OR REPLACE INTO active_context (id, platform, user_id, updated_at) VALUES (1, ?, ?, ?)",
                    (platform, uid, now),
                )
                conn.execute("COMMIT")
                in_tx = False
            except Exception:
                if in_tx:
                    try:
                        conn.execute("ROLLBACK")
                    except Exception:
                        pass
                raise
            finally:
                if self._memory_conn is None:
                    conn.close()

    def clear_default_account(self) -> None:
        """清除当前默认账号指针。"""
        with self._db_lock:
            conn = self._get_connection()
            try:
                conn.execute("DELETE FROM active_context WHERE id = 1")
            finally:
                if self._memory_conn is None:
                    conn.close()

    def get_storage_status(self) -> StorageStatus:
        """获取底层存储状态。"""
        status = self.backend.get_status()
        values = status.model_dump()
        values.update(data_dir=str(self.data_dir), metadata_db=str(self.db_path))
        return StorageStatus.model_validate(values)
