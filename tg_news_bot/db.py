import sqlite3
from datetime import datetime


class Database:
    def __init__(self, path: str):
        self.path = path
        self._init()

    def _init(self):
        with sqlite3.connect(self.path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS published (
                    url TEXT PRIMARY KEY,
                    title TEXT,
                    published_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pending (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT UNIQUE,
                    title TEXT,
                    content TEXT,
                    image_url TEXT,
                    source_url TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS config (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)

    def is_published(self, url: str) -> bool:
        with sqlite3.connect(self.path) as conn:
            return conn.execute(
                "SELECT 1 FROM published WHERE url = ?", (url,)
            ).fetchone() is not None

    def mark_published(self, url: str, title: str):
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO published (url, title) VALUES (?, ?)",
                (url, title),
            )

    def add_pending(self, url: str, title: str, content: str, image_url: str, source_url: str) -> int | None:
        with sqlite3.connect(self.path) as conn:
            try:
                conn.execute(
                    """INSERT OR IGNORE INTO pending (url, title, content, image_url, source_url)
                       VALUES (?, ?, ?, ?, ?)""",
                    (url, title, content, image_url, source_url),
                )
                row = conn.execute("SELECT id FROM pending WHERE url = ?", (url,)).fetchone()
                return row[0] if row else None
            except Exception:
                return None

    def get_pending(self, id: int):
        with sqlite3.connect(self.path) as conn:
            return conn.execute(
                "SELECT id, url, title, content, image_url, source_url FROM pending WHERE id = ?",
                (id,),
            ).fetchone()

    def remove_pending(self, id: int):
        with sqlite3.connect(self.path) as conn:
            conn.execute("DELETE FROM pending WHERE id = ?", (id,))

    def get_config(self, key: str, default: str = "") -> str:
        with sqlite3.connect(self.path) as conn:
            row = conn.execute(
                "SELECT value FROM config WHERE key = ?", (key,)
            ).fetchone()
            return row[0] if row else default

    def set_config(self, key: str, value: str):
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
                (key, value),
            )
