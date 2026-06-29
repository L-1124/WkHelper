import json
import os
import sqlite3
from collections.abc import Iterable
from threading import Lock
from typing import Any, Self

type AnswerPayload = list[Any] | str

_singleton_lock = Lock()


class DB:
    _instance: "DB | None" = None
    conn: sqlite3.Connection
    cursor: sqlite3.Cursor

    def __new__(cls) -> Self:
        if cls._instance is not None:
            return cls._instance
        with _singleton_lock:
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
        self._db_lock = Lock()

        with self._db_lock:
            self.cursor.execute(
                "CREATE TABLE IF NOT EXISTS answers (library_id TEXT, version TEXT, answer TEXT, PRIMARY KEY (library_id, version))"
            )
            self.conn.commit()

    def save_answer(self, library_id: str, version: str | int, answer: AnswerPayload):
        if isinstance(answer, list):
            answer = json.dumps(answer, ensure_ascii=False)
        with self._db_lock:
            self.cursor.execute(
                "INSERT OR REPLACE INTO answers (library_id, version, answer) VALUES (?, ?, ?)",
                (library_id, str(version), answer),
            )
            self.conn.commit()

    def get_answer(self, library_id: str, version: str | int) -> list[str] | None:
        with self._db_lock:
            self.cursor.execute(
                "SELECT answer FROM answers WHERE library_id = ? AND version = ?",
                (library_id, str(version)),
            )
            row = self.cursor.fetchone()
        if row:
            try:
                parsed = json.loads(row[0])
                if isinstance(parsed, list):
                    return parsed
                return [parsed]
            except json.JSONDecodeError:
                return None
        return None

    def batch_save(self, records: Iterable[tuple[str, str | int, AnswerPayload]]):
        data = []
        for lib_id, ver, ans in records:
            if isinstance(ans, list):
                ans = json.dumps(ans, ensure_ascii=False)
            data.append((lib_id, str(ver), ans))
        if not data:
            return

        with self._db_lock:
            self.cursor.executemany(
                "INSERT OR REPLACE INTO answers (library_id, version, answer) VALUES (?, ?, ?)",
                data,
            )
            self.conn.commit()

    def get_all_answers(self, library_id: str) -> list[tuple[str, list[str]]]:
        with self._db_lock:
            self.cursor.execute(
                "SELECT version, answer FROM answers WHERE library_id = ?",
                (library_id,),
            )
            rows = self.cursor.fetchall()

        result = []
        for version, answer in rows:
            try:
                parsed = json.loads(answer)
                if isinstance(parsed, list):
                    result.append((str(version), parsed))
                else:
                    result.append((str(version), [parsed]))
            except json.JSONDecodeError:
                pass
        return result

    def remove_answer(self, library_id: str, version: str | int):
        with self._db_lock:
            self.cursor.execute(
                "DELETE FROM answers WHERE library_id = ? AND version = ?",
                (library_id, str(version)),
            )
            self.conn.commit()

    def clear_all(self):
        with self._db_lock:
            self.cursor.execute("DELETE FROM answers")
            self.conn.commit()

    @property
    def total_count(self) -> int:
        with self._db_lock:
            self.cursor.execute("SELECT COUNT(*) FROM answers")
            return self.cursor.fetchone()[0]


db = DB()
