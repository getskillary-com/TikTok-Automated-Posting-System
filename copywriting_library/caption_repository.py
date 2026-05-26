from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from shared.database import init_database, session, utc_now

from .models import CAPTION_STATUSES, CaptionImportResult


def new_caption_id() -> str:
    return "CPY-" + uuid.uuid4().hex[:12].upper()


def new_binding_id() -> str:
    return "CBN-" + uuid.uuid4().hex[:12].upper()


class CaptionRepository:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = db_path
        init_database(db_path)

    def add_caption(
        self,
        content: str,
        *,
        tags: list[str] | None = None,
        platform: str = "",
        account_scope: str = "",
        note: str = "",
        status: str = "unused",
    ) -> CaptionImportResult:
        if status not in CAPTION_STATUSES:
            raise ValueError(f"Unsupported caption status: {status}")
        normalized_content = normalize_content(content)
        if not normalized_content:
            raise ValueError("Caption content cannot be empty.")
        clean_tags = normalize_tags(tags or [])
        now = utc_now()

        with session(self.db_path) as connection:
            duplicate = connection.execute(
                "SELECT caption_id FROM captions WHERE content = ? LIMIT 1",
                (normalized_content,),
            ).fetchone()
            if duplicate:
                return CaptionImportResult(
                    caption_id=str(duplicate["caption_id"]),
                    status="duplicate",
                    duplicate=True,
                )

            caption_id = new_caption_id()
            connection.execute(
                """
                INSERT INTO captions(
                    caption_id, content, status, tags, platform, account_scope, note,
                    created_at, updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    caption_id,
                    normalized_content,
                    status,
                    json.dumps(clean_tags, ensure_ascii=False),
                    platform,
                    account_scope,
                    note,
                    now,
                    now,
                ),
            )
        return CaptionImportResult(caption_id=caption_id, status="imported", duplicate=False)

    def get(self, caption_id: str) -> dict[str, Any] | None:
        with session(self.db_path) as connection:
            row = connection.execute("SELECT * FROM captions WHERE caption_id = ?", (caption_id,)).fetchone()
        return row_to_dict(row)

    def list_captions(
        self,
        *,
        status: str = "",
        tag: str = "",
        platform: str = "",
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
        if platform:
            clauses.append("platform = ?")
            params.append(platform)
        if search:
            clauses.append("(caption_id LIKE ? OR content LIKE ? OR note LIKE ?)")
            params.extend([f"%{search}%", f"%{search}%", f"%{search}%"])

        where_sql = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(max(1, min(limit, 1000)))
        with session(self.db_path) as connection:
            rows = connection.execute(
                f"SELECT * FROM captions{where_sql} ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [row_to_dict(row) for row in rows]

    def update_caption(
        self,
        caption_id: str,
        *,
        content: str | None = None,
        tags: list[str] | None = None,
        platform: str | None = None,
        account_scope: str | None = None,
        note: str | None = None,
    ) -> None:
        fields: list[str] = []
        params: list[Any] = []
        if content is not None:
            normalized_content = normalize_content(content)
            if not normalized_content:
                raise ValueError("Caption content cannot be empty.")
            fields.append("content = ?")
            params.append(normalized_content)
        if tags is not None:
            fields.append("tags = ?")
            params.append(json.dumps(normalize_tags(tags), ensure_ascii=False))
        if platform is not None:
            fields.append("platform = ?")
            params.append(platform)
        if account_scope is not None:
            fields.append("account_scope = ?")
            params.append(account_scope)
        if note is not None:
            fields.append("note = ?")
            params.append(note)
        if not fields:
            return
        fields.append("updated_at = ?")
        params.append(utc_now())
        params.append(caption_id)
        with session(self.db_path) as connection:
            result = connection.execute(
                f"UPDATE captions SET {', '.join(fields)} WHERE caption_id = ?",
                params,
            )
            if result.rowcount == 0:
                raise KeyError(f"Caption not found: {caption_id}")

    def update_status(self, caption_id: str, status: str) -> None:
        if status not in CAPTION_STATUSES:
            raise ValueError(f"Unsupported caption status: {status}")
        with session(self.db_path) as connection:
            result = connection.execute(
                "UPDATE captions SET status = ?, updated_at = ? WHERE caption_id = ?",
                (status, utc_now(), caption_id),
            )
            if result.rowcount == 0:
                raise KeyError(f"Caption not found: {caption_id}")

    def remove_caption(self, caption_id: str) -> None:
        self.update_status(caption_id, "removed")

    def remove_captions(self, caption_ids: list[str]) -> dict[str, Any]:
        clean_ids = normalize_ids(caption_ids)
        if not clean_ids:
            return {"requested": 0, "removed": 0, "already_removed": [], "missing": []}
        now = utc_now()
        placeholders = ", ".join("?" for _ in clean_ids)
        with session(self.db_path) as connection:
            rows = connection.execute(
                f"SELECT caption_id, status FROM captions WHERE caption_id IN ({placeholders})",
                clean_ids,
            ).fetchall()
            existing = {str(row["caption_id"]): str(row["status"]) for row in rows}
            to_remove = [caption_id for caption_id in clean_ids if existing.get(caption_id) and existing[caption_id] != "removed"]
            if to_remove:
                remove_placeholders = ", ".join("?" for _ in to_remove)
                connection.execute(
                    f"UPDATE captions SET status = 'removed', updated_at = ? WHERE caption_id IN ({remove_placeholders})",
                    (now, *to_remove),
                )
        return {
            "requested": len(clean_ids),
            "removed": len(to_remove),
            "already_removed": [caption_id for caption_id in clean_ids if existing.get(caption_id) == "removed"],
            "missing": [caption_id for caption_id in clean_ids if caption_id not in existing],
        }

    def bind_video(self, caption_id: str, video_id: str, *, note: str = "") -> str:
        now = utc_now()
        with session(self.db_path) as connection:
            caption = connection.execute("SELECT caption_id FROM captions WHERE caption_id = ?", (caption_id,)).fetchone()
            if not caption:
                raise KeyError(f"Caption not found: {caption_id}")
            video = connection.execute("SELECT video_id FROM videos WHERE video_id = ?", (video_id,)).fetchone()
            if not video:
                raise KeyError(f"Video not found: {video_id}")
            existing = connection.execute(
                "SELECT binding_id FROM caption_video_bindings WHERE caption_id = ? AND video_id = ?",
                (caption_id, video_id),
            ).fetchone()
            if existing:
                return str(existing["binding_id"])
            binding_id = new_binding_id()
            connection.execute(
                """
                INSERT INTO caption_video_bindings(binding_id, caption_id, video_id, note, created_at)
                VALUES(?, ?, ?, ?, ?)
                """,
                (binding_id, caption_id, video_id, note, now),
            )
            connection.execute(
                "UPDATE captions SET status = 'assigned', updated_at = ? WHERE caption_id = ? AND status = 'unused'",
                (now, caption_id),
            )
        return binding_id

    def list_video_bindings(self, caption_id: str = "", video_id: str = "") -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if caption_id:
            clauses.append("b.caption_id = ?")
            params.append(caption_id)
        if video_id:
            clauses.append("b.video_id = ?")
            params.append(video_id)
        where_sql = " WHERE " + " AND ".join(clauses) if clauses else ""
        with session(self.db_path) as connection:
            rows = connection.execute(
                f"""
                SELECT b.*, c.content, c.status AS caption_status, v.file_name, v.title AS video_title
                FROM caption_video_bindings b
                JOIN captions c ON c.caption_id = b.caption_id
                JOIN videos v ON v.video_id = b.video_id
                {where_sql}
                ORDER BY b.created_at DESC
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]


def normalize_content(content: str) -> str:
    lines = [line.rstrip() for line in str(content).strip().splitlines()]
    return "\n".join(line for line in lines).strip()


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
