from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .schema import SCHEMA_SQL, SCHEMA_VERSION


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = PROJECT_ROOT / "storage" / "group_control_system.db"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def resolve_db_path(path: str | Path | None = None) -> Path:
    configured = path or os.environ.get("GROUP_CONTROL_DB", "")
    if configured:
        return Path(configured).expanduser().resolve()
    return DEFAULT_DB_PATH


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    db_path = resolve_db_path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


@contextmanager
def session(path: str | Path | None = None) -> Iterator[sqlite3.Connection]:
    connection = connect(path)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_database(path: str | Path | None = None) -> Path:
    db_path = resolve_db_path(path)
    with session(db_path) as connection:
        connection.executescript(SCHEMA_SQL)
        migrate_database(connection)
        now = utc_now()
        connection.execute(
            """
            INSERT INTO schema_meta(key, value, updated_at)
            VALUES('schema_version', ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (str(SCHEMA_VERSION), now),
        )
    return db_path


def migrate_database(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA_SQL)
    ensure_video_indexes(connection)
    ensure_column(connection, "phones", "account_type", "TEXT NOT NULL DEFAULT 'marketing'")
    ensure_column(connection, "release_tasks", "product_id", "TEXT NOT NULL DEFAULT ''")
    ensure_column(connection, "release_tasks", "product_link", "TEXT NOT NULL DEFAULT ''")
    ensure_column(connection, "release_tasks", "product_name", "TEXT NOT NULL DEFAULT ''")
    ensure_column(connection, "release_tasks", "product_search_title", "TEXT NOT NULL DEFAULT ''")
    ensure_column(connection, "release_tasks", "product_publish_name", "TEXT NOT NULL DEFAULT ''")
    ensure_column(connection, "release_tasks", "locked_by_worker", "TEXT NOT NULL DEFAULT ''")
    ensure_column(connection, "release_tasks", "lock_expires_at", "TEXT")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_release_tasks_locked_by_worker ON release_tasks(locked_by_worker)")


def ensure_video_indexes(connection: sqlite3.Connection) -> None:
    for row in connection.execute("PRAGMA index_list(videos)").fetchall():
        if str(row["name"]) == "idx_videos_sha256" and int(row["unique"] or 0):
            connection.execute("DROP INDEX idx_videos_sha256")
            break
    connection.execute("CREATE INDEX IF NOT EXISTS idx_videos_sha256 ON videos(sha256)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_videos_file_name ON videos(file_name)")


def ensure_column(connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    existing = {
        str(row["name"])
        for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column not in existing:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
