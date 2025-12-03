import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from threading import Lock


class DB:
    _instance = None
    _lock = Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._init_db()
        return cls._instance

    def _init_db(self):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        db_path = os.path.join(base_dir, "questions.db")
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self.conn.commit()
        self.lock = Lock()

    def _get_table_name(self, library_id: str) -> str:
        return f"lib_{str(library_id).replace('-', '_')}"

    def save_answer(self, library_id: str, version: str, answer: list | str):
        table_name = self._get_table_name(library_id)

        tz = timezone(timedelta(hours=8))
        now = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")

        with self.lock:
            try:
                self.cursor.execute(f"""
                    CREATE TABLE IF NOT EXISTS "{table_name}" (
                        version TEXT PRIMARY KEY,
                        answer TEXT,
                        update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)

                answer_json = json.dumps(answer, ensure_ascii=False)
                self.cursor.execute(
                    f"""
                    INSERT OR REPLACE INTO "{table_name}" (version, answer, update_time)
                    VALUES (?, ?, ?)
                """,
                    (str(version), answer_json, now),
                )
                self.conn.commit()
            except Exception as e:
                print(f"Error saving answer: {e}")

    def get_answer(self, library_id: str, version: str) -> list | str | None:
        table_name = self._get_table_name(library_id)
        with self.lock:
            try:
                self.cursor.execute(
                    f"""
                    SELECT answer FROM "{table_name}" 
                    WHERE version = ?
                """,
                    (str(version),),
                )
                row = self.cursor.fetchone()
                if row:
                    try:
                        return json.loads(row[0])
                    except Exception:
                        return row[0]
            except sqlite3.OperationalError:
                return None
            except Exception as e:
                print(f"Error getting answer: {e}")
                return None
            return None


db = DB()
