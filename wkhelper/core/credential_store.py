"""登录凭证存储管理。"""

import base64
import json
import logging
import os
import sqlite3
import time
from threading import Lock

from wkhelper.core.models import UserInfo

logger = logging.getLogger(__name__)


def _default_db_path() -> str:
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_dir, "credentials.db")


class CredentialStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    def _encode(self, text: str) -> str:
        return base64.b64encode(text.encode("utf-8")).decode("utf-8")

    def _decode(self, text: str) -> str:
        return base64.b64decode(text.encode("utf-8")).decode("utf-8")

    def _init_db(self):
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self._db_lock = Lock()

        with self._db_lock:
            self.cursor.execute("""
                CREATE TABLE IF NOT EXISTS credentials (
                    platform TEXT,
                    user_id TEXT,
                    name TEXT,
                    school TEXT,
                    cookies TEXT,
                    saved_at REAL,
                    PRIMARY KEY (platform, user_id)
                )
            """)
            self.conn.commit()

    def save(self, platform: str, user_info: UserInfo, cookies: dict[str, str]) -> None:
        """保存或更新登录凭证。"""
        with self._db_lock:
            try:
                cookies_json = json.dumps(cookies, ensure_ascii=False)
                encoded_cookies = self._encode(cookies_json)
                now = time.time()
                self.cursor.execute(
                    """
                    INSERT OR REPLACE INTO credentials (platform, user_id, name, school, cookies, saved_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (platform, str(user_info.id), user_info.name, user_info.school, encoded_cookies, now),
                )
                self.conn.commit()
            except Exception as e:
                logger.error(f"Error saving credential: {e}")

    def list_accounts(self, platform: str | None = None) -> list[dict]:
        """列出已保存的账号。

        Returns:
            list[dict]: 包含 platform, user_id, name, school, saved_at 的列表
        """
        with self._db_lock:
            try:
                if platform:
                    self.cursor.execute(
                        "SELECT platform, user_id, name, school, saved_at FROM credentials WHERE platform = ? ORDER BY saved_at DESC",
                        (platform,),
                    )
                else:
                    self.cursor.execute("SELECT platform, user_id, name, school, saved_at FROM credentials ORDER BY saved_at DESC")

                rows = self.cursor.fetchall()
                return [
                    {
                        "platform": row[0],
                        "user_id": row[1],
                        "name": row[2],
                        "school": row[3],
                        "saved_at": row[4],
                    }
                    for row in rows
                ]
            except Exception as e:
                logger.error(f"Error listing accounts: {e}")
                return []

    def get_cookies(self, platform: str, user_id: str) -> dict[str, str] | None:
        """获取指定账号的 cookies。"""
        with self._db_lock:
            try:
                self.cursor.execute(
                    "SELECT cookies FROM credentials WHERE platform = ? AND user_id = ?",
                    (platform, user_id),
                )
                row = self.cursor.fetchone()
                if row:
                    decrypted = self._decode(row[0])
                    return json.loads(decrypted)
            except Exception as e:
                logger.error(f"Error getting cookies: {e}")
            return None

    def delete(self, platform: str, user_id: str) -> None:
        """删除指定账号的凭证。"""
        with self._db_lock:
            try:
                self.cursor.execute(
                    "DELETE FROM credentials WHERE platform = ? AND user_id = ?",
                    (platform, user_id),
                )
                self.conn.commit()
            except Exception as e:
                logger.error(f"Error deleting credential: {e}")


credential_store = CredentialStore(_default_db_path())
