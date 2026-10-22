"""系统安全凭据存储管理。"""

import json
import logging
import os
import sqlite3
import time
from abc import ABC, abstractmethod
from pathlib import Path
from threading import Lock
from typing import Annotated

import keyring
from keyring.errors import KeyringError, PasswordDeleteError
from pydantic import BeforeValidator, TypeAdapter

from wkhelper.domain.accounts import AccountSummary, StorageStatus, UserInfo
from wkhelper.domain.errors import AuthError
from wkhelper.infra.serialization import decode_json

logger = logging.getLogger(__name__)

SERVICE_NAME = "wkhelper"
_COOKIES_ADAPTER: TypeAdapter[dict[str, str]] = TypeAdapter(dict[str, Annotated[str, BeforeValidator(str)]])


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
        except KeyringError as exc:
            raise AuthError(f"写入系统凭据存储失败: {exc}") from exc

    def get_secret(self, service: str, key: str) -> str | None:
        try:
            return self._kr.get_password(service, key)
        except KeyringError as exc:
            raise AuthError(f"读取系统凭据存储失败: {exc}") from exc

    def delete_secret(self, service: str, key: str) -> None:
        try:
            self._kr.delete_password(service, key)
        except PasswordDeleteError:
            # 秘密不存在，视为已删除
            pass
        except KeyringError as exc:
            raise AuthError(f"从系统凭据管理器删除凭据失败: {exc}") from exc

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
            # session 模式使用内存 SQLite，彻底隔离磁盘
            self.data_dir = Path(data_dir) if data_dir else Path(os.environ.get("TEMP", "/tmp"))
            self.db_path = Path(":memory:")
            self._memory_conn: sqlite3.Connection | None = sqlite3.connect(":memory:", check_same_thread=False)
        else:
            self.data_dir = Path(data_dir) if data_dir else get_default_data_dir()
            self.data_dir.mkdir(parents=True, exist_ok=True)
            self.db_path = self.data_dir / "metadata.db"
            self._memory_conn = None

        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        if self._memory_conn is not None:
            return self._memory_conn
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        with self._db_lock:
            conn = self._get_connection()
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS accounts (
                        platform TEXT,
                        user_id TEXT,
                        name TEXT,
                        school TEXT,
                        key_id TEXT,
                        saved_at REAL,
                        PRIMARY KEY (platform, user_id)
                    )
                """)
                conn.commit()
            finally:
                if self._memory_conn is None:
                    conn.close()

    @staticmethod
    def _make_key_id(platform: str, user_id: str | int) -> str:
        return f"{platform}:{user_id}:cookies"

    def save(self, platform: str, user_info: UserInfo, cookies: dict[str, str]) -> None:
        """保存凭据。原子性地操作秘密存储与元数据。"""
        key_id = self._make_key_id(platform, user_info.id)
        encoded_json = json.dumps(cookies, ensure_ascii=False)
        now = time.time()

        with self._db_lock:
            # 记录修改前的旧秘密，以便在 SQLite 失败时精确恢复
            old_secret = self.backend.get_secret(SERVICE_NAME, key_id)

            # 1. 写秘密
            self.backend.set_secret(SERVICE_NAME, key_id, encoded_json)
            # 2. 写元数据（连接/执行/提交失败均进行补偿回滚与事务 rollback）
            conn: sqlite3.Connection | None = None
            try:
                conn = self._get_connection()
                conn.execute(
                    """
                    INSERT OR REPLACE INTO accounts (platform, user_id, name, school, key_id, saved_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (platform, str(user_info.id), user_info.name, user_info.school, key_id, now),
                )
                conn.commit()
            except Exception as db_exc:
                if conn is not None:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                try:
                    if old_secret is not None:
                        self.backend.set_secret(SERVICE_NAME, key_id, old_secret)
                    else:
                        self.backend.delete_secret(SERVICE_NAME, key_id)
                except Exception as comp_exc:
                    raise comp_exc from db_exc
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
                if platform:
                    cursor = conn.execute(
                        "SELECT platform, user_id, name, school, saved_at FROM accounts WHERE platform = ? ORDER BY saved_at DESC",
                        (platform,),
                    )
                else:
                    cursor = conn.execute("SELECT platform, user_id, name, school, saved_at FROM accounts ORDER BY saved_at DESC")
                rows = cursor.fetchall()
                return [
                    AccountSummary(
                        platform=str(row[0]),
                        user_id=str(row[1]),
                        name=str(row[2]),
                        school=row[3] if isinstance(row[3], str) else None,
                        saved_at=float(row[4]),
                    )
                    for row in rows
                ]
            finally:
                if conn is not None and self._memory_conn is None:
                    conn.close()

    def get_cookies(self, platform: str, user_id: str | int) -> dict[str, str] | None:
        """获取账号的 Cookie 字典。"""
        key_id = self._make_key_id(platform, user_id)
        with self._db_lock:
            raw_secret = self.backend.get_secret(SERVICE_NAME, key_id)
        if not raw_secret:
            return None
        parsed = decode_json(raw_secret)
        if isinstance(parsed, dict):
            return _COOKIES_ADAPTER.validate_python(parsed)
        return None

    def delete(self, platform: str, user_id: str | int) -> bool:
        """原子删除指定账号的凭据与元数据。"""
        key_id = self._make_key_id(platform, user_id)
        with self._db_lock:
            # 记录修改前的旧秘密，以便在 SQLite 失败时精确恢复
            old_secret = self.backend.get_secret(SERVICE_NAME, key_id)

            # 1. 先删秘密
            self.backend.delete_secret(SERVICE_NAME, key_id)

            # 2. 删元数据（连接/执行/提交失败均进行补偿回滚与事务 rollback）
            conn: sqlite3.Connection | None = None
            try:
                conn = self._get_connection()
                cursor = conn.execute(
                    "DELETE FROM accounts WHERE platform = ? AND user_id = ?",
                    (platform, str(user_id)),
                )
                conn.commit()
                return cursor.rowcount > 0
            except Exception as db_exc:
                if conn is not None:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                try:
                    if old_secret is not None:
                        self.backend.set_secret(SERVICE_NAME, key_id, old_secret)
                except Exception as comp_exc:
                    raise comp_exc from db_exc
                raise
            finally:
                if conn is not None and self._memory_conn is None:
                    conn.close()

    def get_storage_status(self) -> StorageStatus:
        """获取底层存储状态。"""
        status = self.backend.get_status()
        values = status.model_dump()
        values.update(data_dir=str(self.data_dir), metadata_db=str(self.db_path))
        return StorageStatus.model_validate(values)
