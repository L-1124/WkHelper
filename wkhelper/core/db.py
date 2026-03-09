import json
import os
import sqlite3
from collections.abc import Iterable
from threading import Lock
from typing import Any, Self

type AnswerPayload = list[Any] | str


class DB:
    _instance = None
    _lock = Lock()
    conn: sqlite3.Connection
    cursor: sqlite3.Cursor
    lock: Lock

    def __new__(cls) -> Self:
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._init_db()
        return cls._instance

    def _init_db(self):
        # base_dir is wkhelper/core, we want wkhelper/
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        db_path = os.path.join(base_dir, "questions.db")
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self.lock = Lock()

        with self.lock:
            # 创建统一的数据表
            self.cursor.execute("""
                CREATE TABLE IF NOT EXISTS answers (
                    library_id TEXT,
                    version TEXT,
                    answer TEXT,
                    PRIMARY KEY (library_id, version)
                )
            """)
            # 如果存在旧表，则进行数据迁移
            self._migrate_old_tables()
            # 统一答案存储格式
            self._migrate_answer_payloads()
            self.conn.commit()

    def _migrate_old_tables(self):
        """从旧的 lib_xxx 表迁移数据到统一的 answers 表。"""
        self.cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'lib_%'")
        old_tables = [row[0] for row in self.cursor.fetchall()]

        if not old_tables:
            return

        print(f"发现 {len(old_tables)} 个旧表，正在迁移数据...")
        for table in old_tables:
            try:
                # 从表名提取 library_id: lib_123_456 -> 123-456
                # 注意：replace('_', '-') 是一种启发式方法，因为我们不知道确切的原始映射，
                # 但大多数 ID 是数字或简单的字符串。
                library_id = table[4:].replace("_", "-")
                self.cursor.execute(
                    f'INSERT OR REPLACE INTO answers (library_id, version, answer) SELECT ?, version, answer FROM "{table}"',
                    (library_id,),
                )
                self.cursor.execute(f'DROP TABLE "{table}"')
            except Exception as e:
                print(f"迁移表 {table} 时出错: {e}")

    def save_answer(self, library_id: str, version: str, answer: AnswerPayload):
        with self.lock:
            try:
                normalized = self.normalize_answer(answer)
                answer_json = json.dumps(normalized, ensure_ascii=False)
                self.cursor.execute(
                    """
                    INSERT OR REPLACE INTO answers (library_id, version, answer)
                    VALUES (?, ?, ?)
                """,
                    (str(library_id), str(version), answer_json),
                )
                self.conn.commit()
            except Exception as e:
                print(f"Error saving answer: {e}")

    def get_answer(self, library_id: str, version: str) -> list[str] | None:
        with self.lock:
            try:
                self.cursor.execute(
                    """
                    SELECT answer FROM answers 
                    WHERE library_id = ? AND version = ?
                """,
                    (str(library_id), str(version)),
                )
                row = self.cursor.fetchone()
                if row:
                    try:
                        parsed = json.loads(row[0])
                        if isinstance(parsed, list):
                            return self.normalize_answer(parsed)
                        return self.normalize_answer(str(parsed))
                    except Exception:
                        return self.normalize_answer(str(row[0]))
            except Exception as e:
                print(f"Error getting answer: {e}")
                return None
            return None

    def normalize_answer(self, answer: AnswerPayload) -> list[str]:
        """标准化答案格式为去重且去空的 list[str]。"""
        raw: Iterable[Any] = [answer] if isinstance(answer, str) else answer
        result: list[str] = []
        for item in raw:
            val = str(item).strip()
            if not val:
                continue
            if val not in result:
                result.append(val)
        return result

    def _normalize_payload_text(self, text: str) -> list[str]:
        """尝试从存储文本中解析并标准化答案。"""
        try:
            data = json.loads(text)
            return self.normalize_answer(data)
        except Exception:
            return self.normalize_answer(text)

    def _migrate_answer_payloads(self) -> None:
        """迁移旧的答案存储格式为统一的 JSON 数组。"""
        self.cursor.execute("SELECT library_id, version, answer FROM answers")
        rows = self.cursor.fetchall()
        for library_id, version, raw in rows:
            normalized = self._normalize_payload_text(raw)
            fixed_json = json.dumps(normalized, ensure_ascii=False)
            if fixed_json != raw:
                self.cursor.execute(
                    "UPDATE answers SET answer=? WHERE library_id=? AND version=?",
                    (fixed_json, library_id, version),
                )
        self.conn.commit()


db = DB()
