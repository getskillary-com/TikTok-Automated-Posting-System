from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from shared.database import init_database, session, utc_now

from .models import VIDEO_STATUSES, VideoImportResult, VideoMetadata


def new_video_id() -> str:
    return "VID-" + uuid.uuid4().hex[:12].upper()


class VideoRepository:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = db_path
        init_database(db_path)

    def add_video(
        self,
        metadata: VideoMetadata,
        *,
        title: str = "",
        tags: list[str] | None = None,
        batch_name: str = "",
        note: str = "",
        status: str = "unused",
    ) -> VideoImportResult:
        if status not in VIDEO_STATUSES:
            raise ValueError(f"Unsupported video status: {status}")
        clean_tags = normalize_tags(tags or [])
        file_path = str(metadata.file_path)
        now = utc_now()

        with session(self.db_path) as connection:
            duplicate = connection.execute(
                """
                SELECT video_id, file_path, status
                FROM videos
                WHERE file_path = ? OR lower(file_name) = lower(?)
                LIMIT 1
                """,
                (file_path, metadata.file_name),
            ).fetchone()
            if duplicate:
                if str(duplicate["status"]) == "removed":
                    connection.execute(
                        """
                        UPDATE videos
                        SET title = ?, file_path = ?, file_name = ?, file_ext = ?, file_size = ?,
                            sha256 = ?, duration_seconds = ?, width = ?, height = ?,
                            status = ?, tags = ?, batch_name = ?, note = ?,
                            imported_at = ?, updated_at = ?
                        WHERE video_id = ?
                        """,
                        (
                            title or metadata.file_path.stem,
                            file_path,
                            metadata.file_name,
                            metadata.file_ext,
                            metadata.file_size,
                            metadata.sha256,
                            metadata.duration_seconds,
                            metadata.width,
                            metadata.height,
                            status,
                            json.dumps(clean_tags, ensure_ascii=False),
                            batch_name,
                            note,
                            now,
                            now,
                            str(duplicate["video_id"]),
                        ),
                    )
                    return VideoImportResult(
                        video_id=str(duplicate["video_id"]),
                        file_path=file_path,
                        status="restored",
                        duplicate=False,
                    )
                return VideoImportResult(
                    video_id=str(duplicate["video_id"]),
                    file_path=str(duplicate["file_path"]),
                    status="duplicate",
                    duplicate=True,
                )

            video_id = new_video_id()
            connection.execute(
                """
                INSERT INTO videos(
                    video_id, title, file_path, file_name, file_ext, file_size, sha256,
                    duration_seconds, width, height, status, tags, batch_name, note,
                    created_at, imported_at, updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    video_id,
                    title or metadata.file_path.stem,
                    file_path,
                    metadata.file_name,
                    metadata.file_ext,
                    metadata.file_size,
                    metadata.sha256,
                    metadata.duration_seconds,
                    metadata.width,
                    metadata.height,
                    status,
                    json.dumps(clean_tags, ensure_ascii=False),
                    batch_name,
                    note,
                    now,
                    now,
                    now,
                ),
            )
        return VideoImportResult(video_id=video_id, file_path=file_path, status="imported", duplicate=False)

    def get(self, video_id: str) -> dict[str, Any] | None:
        with session(self.db_path) as connection:
            row = connection.execute("SELECT * FROM videos WHERE video_id = ?", (video_id,)).fetchone()
        return row_to_dict(row)

    def list_videos(
        self,
        *,
        status: str = "",
        tag: str = "",
        batch_name: str = "",
        search: str = "",
        limit: int = 100,
        include_removed: bool = False,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        elif not include_removed:
            clauses.append("status != 'removed'")
        if tag:
            clauses.append("tags LIKE ?")
            params.append(f'%"{tag}"%')
        if batch_name:
            clauses.append("batch_name = ?")
            params.append(batch_name)
        if search:
            clauses.append("(video_id LIKE ? OR title LIKE ? OR file_name LIKE ?)")
            params.extend([f"%{search}%", f"%{search}%", f"%{search}%"])

        where_sql = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(max(1, min(limit, 1000)))
        with session(self.db_path) as connection:
            rows = connection.execute(
                f"SELECT * FROM videos{where_sql} ORDER BY imported_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [row_to_dict(row) for row in rows]

    def update_status(self, video_id: str, status: str) -> None:
        if status not in VIDEO_STATUSES:
            raise ValueError(f"Unsupported video status: {status}")
        with session(self.db_path) as connection:
            result = connection.execute(
                "UPDATE videos SET status = ?, updated_at = ? WHERE video_id = ?",
                (status, utc_now(), video_id),
            )
            if result.rowcount == 0:
                raise KeyError(f"Video not found: {video_id}")

    def remove_video(self, video_id: str) -> None:
        self.update_status(video_id, "removed")

    def remove_videos(self, video_ids: list[str]) -> dict[str, Any]:
        clean_ids = normalize_ids(video_ids)
        if not clean_ids:
            return {"requested": 0, "removed": 0, "already_removed": [], "missing": []}
        now = utc_now()
        placeholders = ", ".join("?" for _ in clean_ids)
        with session(self.db_path) as connection:
            rows = connection.execute(
                f"SELECT video_id, status FROM videos WHERE video_id IN ({placeholders})",
                clean_ids,
            ).fetchall()
            existing = {str(row["video_id"]): str(row["status"]) for row in rows}
            to_remove = [video_id for video_id in clean_ids if existing.get(video_id) and existing[video_id] != "removed"]
            if to_remove:
                remove_placeholders = ", ".join("?" for _ in to_remove)
                connection.execute(
                    f"UPDATE videos SET status = 'removed', updated_at = ? WHERE video_id IN ({remove_placeholders})",
                    (now, *to_remove),
                )
        return {
            "requested": len(clean_ids),
            "removed": len(to_remove),
            "already_removed": [video_id for video_id in clean_ids if existing.get(video_id) == "removed"],
            "missing": [video_id for video_id in clean_ids if video_id not in existing],
        }

    def file_exists(self, video_id: str) -> bool:
        video = self.get(video_id)
        if not video:
            raise KeyError(f"Video not found: {video_id}")
        return Path(str(video["file_path"])).exists()


def normalize_tags(tags: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        value = str(tag).strip()
        if value and value not in seen:
            seen.add(value)
            cleaned.append(value)
    return cleaned


def normalize_ids(values: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value or "").strip()
        if item and item not in seen:
            seen.add(item)
            cleaned.append(item)
    return cleaned


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    try:
        data["tags"] = json.loads(str(data.get("tags") or "[]"))
    except json.JSONDecodeError:
        data["tags"] = []
    return data
