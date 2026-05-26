from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta, timezone as datetime_timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from shared.account_rules import require_account_workflow
from shared.database import init_database, session, utc_now
from shared.queue_rules import evaluate_task_candidate

from .models import ACTIVE_TASK_STATUSES, PUBLISH_MODES, TASK_STATUSES, ReleaseTaskCreateResult


def new_task_id() -> str:
    return "TSK-" + uuid.uuid4().hex[:12].upper()


class ReleaseTaskRepository:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = db_path
        init_database(db_path)

    def create_task(
        self,
        *,
        video_id: str,
        phone_id: str,
        scheduled_at: str,
        caption_id: str = "",
        publish_mode: str = "scheduled",
        product_id: str = "",
        product_link: str = "",
        product_name: str = "",
        product_search_title: str = "",
        product_publish_name: str = "",
        max_retries: int = 3,
        note: str = "",
        status: str = "pending",
        allow_reuse: bool = False,
        timezone: str = "Asia/Shanghai",
    ) -> ReleaseTaskCreateResult:
        validate_publish_mode(publish_mode)
        validate_task_status(status)
        clean_scheduled_at = normalize_scheduled_at(scheduled_at, timezone=timezone)
        clean_caption_id = caption_id.strip()
        clean_video_id = video_id.strip()
        clean_phone_id = phone_id.strip()
        clean_product_id = product_id.strip()
        clean_product_link = product_link.strip()
        clean_product_name = product_name.strip()
        clean_product_search_title = product_search_title.strip()
        clean_product_publish_name = product_publish_name.strip()
        if not clean_video_id or not clean_phone_id:
            raise ValueError("video_id and phone_id are required.")
        if max_retries < 0:
            raise ValueError("max_retries cannot be negative.")
        now = utc_now()

        with session(self.db_path) as connection:
            product = ensure_related_records(connection, clean_video_id, clean_caption_id, clean_phone_id, clean_product_id)
            if product is not None:
                clean_product_link = clean_product_link or str(product["product_link"] or "")
                clean_product_search_title = clean_product_search_title or str(product["search_title"] or "")
                clean_product_publish_name = clean_product_publish_name or str(product["publish_name"] or "")
            if not clean_product_search_title:
                clean_product_search_title = clean_product_name
            if not clean_product_name:
                clean_product_name = clean_product_search_title
            if not clean_product_publish_name:
                clean_product_publish_name = clean_product_search_title or clean_product_name
            validate_phone_task_workflow(
                connection,
                phone_id=clean_phone_id,
                publish_mode=publish_mode,
                product_id=clean_product_id,
                product_link=clean_product_link,
                product_name=clean_product_name,
                product_search_title=clean_product_search_title,
                product_publish_name=clean_product_publish_name,
            )
            if not allow_reuse:
                duplicate = connection.execute(
                    f"""
                    SELECT task_id, status FROM release_tasks
                    WHERE video_id = ? AND status IN ({placeholders(ACTIVE_TASK_STATUSES)})
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (clean_video_id, *sorted(ACTIVE_TASK_STATUSES)),
                ).fetchone()
                if duplicate:
                    return ReleaseTaskCreateResult(task_id=str(duplicate["task_id"]), status=str(duplicate["status"]), duplicate=True)

            task_id = new_task_id()
            connection.execute(
                """
                INSERT INTO release_tasks(
                    task_id, video_id, caption_id, phone_id, scheduled_at, publish_mode,
                    product_id, product_link, product_name, product_search_title, product_publish_name,
                    status, max_retries, note, created_at, updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    clean_video_id,
                    clean_caption_id or None,
                    clean_phone_id,
                    clean_scheduled_at,
                    publish_mode,
                    clean_product_id,
                    clean_product_link,
                    clean_product_name,
                    clean_product_search_title,
                    clean_product_publish_name,
                    status,
                    max_retries,
                    note,
                    now,
                    now,
                ),
            )
            mark_related_assigned(connection, clean_video_id, clean_caption_id, now)
        return ReleaseTaskCreateResult(task_id=task_id, status=status, duplicate=False)

    def create_from_binding(
        self,
        *,
        binding_id: str,
        phone_id: str,
        scheduled_at: str,
        publish_mode: str = "scheduled",
        product_id: str = "",
        product_link: str = "",
        product_name: str = "",
        product_search_title: str = "",
        product_publish_name: str = "",
        max_retries: int = 3,
        note: str = "",
        allow_reuse: bool = False,
        timezone: str = "Asia/Shanghai",
    ) -> ReleaseTaskCreateResult:
        with session(self.db_path) as connection:
            row = connection.execute(
                "SELECT caption_id, video_id FROM caption_video_bindings WHERE binding_id = ?",
                (binding_id,),
            ).fetchone()
        if not row:
            raise KeyError(f"Caption/video binding not found: {binding_id}")
        return self.create_task(
            video_id=str(row["video_id"]),
            caption_id=str(row["caption_id"]),
            phone_id=phone_id,
            scheduled_at=scheduled_at,
            publish_mode=publish_mode,
            product_id=product_id,
            product_link=product_link,
            product_name=product_name,
            product_search_title=product_search_title,
            product_publish_name=product_publish_name,
            max_retries=max_retries,
            note=note,
            allow_reuse=allow_reuse,
            timezone=timezone,
        )

    def get(self, task_id: str) -> dict[str, Any] | None:
        with session(self.db_path) as connection:
            row = connection.execute(task_detail_sql("WHERE t.task_id = ?"), (task_id,)).fetchone()
        return task_row_to_dict(row)

    def list_tasks(
        self,
        *,
        status: str = "",
        phone_id: str = "",
        video_id: str = "",
        date_from: str = "",
        date_to: str = "",
        search: str = "",
        limit: int = 100,
        include_removed: bool = False,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("t.status = ?")
            params.append(status)
        elif not include_removed:
            clauses.append("t.status != 'removed'")
        if phone_id:
            clauses.append("t.phone_id = ?")
            params.append(phone_id)
        if video_id:
            clauses.append("t.video_id = ?")
            params.append(video_id)
        if date_from:
            clauses.append("t.scheduled_at >= ?")
            params.append(normalize_scheduled_at(date_from))
        if date_to:
            clauses.append("t.scheduled_at <= ?")
            params.append(normalize_scheduled_at(date_to))
        if search:
            clauses.append("(t.task_id LIKE ? OR v.title LIKE ? OR v.file_name LIKE ? OR p.device_name LIKE ? OR p.account_name LIKE ? OR p.account_type LIKE ? OR t.product_name LIKE ? OR t.product_search_title LIKE ? OR t.product_publish_name LIKE ? OR t.product_link LIKE ? OR pr.title LIKE ?)")
            params.extend([f"%{search}%"] * 11)
        where_sql = "WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(max(1, min(limit, 1000)))
        with session(self.db_path) as connection:
            rows = connection.execute(
                task_detail_sql(where_sql) + " ORDER BY t.scheduled_at ASC, t.created_at ASC LIMIT ?",
                params,
            ).fetchall()
        return [task_row_to_dict(row) for row in rows]

    def update_status(
        self,
        task_id: str,
        status: str,
        *,
        failure_reason: str = "",
        run_dir: str = "",
    ) -> None:
        validate_task_status(status)
        now = utc_now()
        with session(self.db_path) as connection:
            task = connection.execute("SELECT * FROM release_tasks WHERE task_id = ?", (task_id,)).fetchone()
            if not task:
                raise KeyError(f"Task not found: {task_id}")
            connection.execute(
                """
                UPDATE release_tasks
                SET status = ?,
                    failure_reason = ?,
                    run_dir = CASE WHEN ? != '' THEN ? ELSE run_dir END,
                    locked_by_worker = CASE WHEN ? IN ('published', 'failed', 'cancelled', 'pending', 'ready') THEN '' ELSE locked_by_worker END,
                    lock_expires_at = CASE WHEN ? IN ('published', 'failed', 'cancelled', 'pending', 'ready') THEN NULL ELSE lock_expires_at END,
                    updated_at = ?
                WHERE task_id = ?
                """,
                (status, failure_reason, run_dir, run_dir, status, status, now, task_id),
            )
            if status in {"published", "failed", "cancelled"}:
                record_task_usage(connection, task, status, failure_reason, now)
        return None

    def requeue(
        self,
        task_id: str,
        *,
        reset_retry_count: bool = False,
        force_running: bool = False,
        reason: str = "",
    ) -> dict[str, Any]:
        now = utc_now()
        clean_task_id = task_id.strip()
        if not clean_task_id:
            raise ValueError("task_id is required.")
        with session(self.db_path) as connection:
            task = connection.execute("SELECT * FROM release_tasks WHERE task_id = ?", (clean_task_id,)).fetchone()
            if not task:
                raise KeyError(f"Task not found: {clean_task_id}")

            old_status = str(task["status"] or "")
            if old_status == "published":
                raise ValueError("Published tasks cannot be requeued.")
            if old_status == "running" and not force_running:
                raise ValueError("Running tasks require force_running=true before they can be requeued.")

            retry_count = 0 if reset_retry_count else int(task["retry_count"] or 0)
            connection.execute(
                """
                UPDATE release_tasks
                SET status = 'pending',
                    retry_count = ?,
                    failure_reason = '',
                    locked_by_worker = '',
                    lock_expires_at = NULL,
                    updated_at = ?
                WHERE task_id = ?
                """,
                (retry_count, now, clean_task_id),
            )
            connection.execute(
                "UPDATE videos SET status = 'assigned', updated_at = ? WHERE video_id = ? AND status IN ('unused', 'failed')",
                (now, task["video_id"]),
            )
            if task["caption_id"]:
                connection.execute(
                    "UPDATE captions SET status = 'assigned', updated_at = ? WHERE caption_id = ? AND status IN ('unused', 'failed')",
                    (now, task["caption_id"]),
                )

            phone_released = False
            if old_status == "running" and force_running:
                connection.execute(
                    """
                    UPDATE task_attempts
                    SET ended_at = ?, result = 'requeued', failure_reason = ?
                    WHERE task_id = ? AND ended_at IS NULL
                    """,
                    (now, reason or "Task was manually requeued.", clean_task_id),
                )
                connection.execute(
                    """
                    UPDATE execution_logs
                    SET ended_at = ?, result = 'requeued', failure_reason = ?
                    WHERE task_id = ? AND ended_at IS NULL
                    """,
                    (now, reason or "Task was manually requeued.", clean_task_id),
                )
                result = connection.execute(
                    """
                    UPDATE phones
                    SET current_status = 'online_idle', updated_at = ?
                    WHERE phone_id = ? AND current_status = 'running'
                    """,
                    (now, task["phone_id"]),
                )
                phone_released = result.rowcount > 0

        return {
            "task_id": clean_task_id,
            "old_status": old_status,
            "status": "pending",
            "retry_count": retry_count,
            "reset_retry_count": reset_retry_count,
            "phone_released": phone_released,
        }

    def reschedule(self, task_id: str, scheduled_at: str, *, timezone: str = "Asia/Shanghai") -> None:
        clean_scheduled_at = normalize_scheduled_at(scheduled_at, timezone=timezone)
        with session(self.db_path) as connection:
            task = connection.execute("SELECT * FROM release_tasks WHERE task_id = ?", (task_id,)).fetchone()
            if not task:
                raise KeyError(f"Task not found: {task_id}")
            validate_phone_task_workflow(
                connection,
                phone_id=str(task["phone_id"]),
                publish_mode=str(task["publish_mode"] or "scheduled"),
                product_id=str(task["product_id"] or ""),
                product_link=str(task["product_link"] or ""),
                product_name=str(task["product_name"] or ""),
                product_search_title=str(task["product_search_title"] or ""),
                product_publish_name=str(task["product_publish_name"] or ""),
            )
            result = connection.execute(
                "UPDATE release_tasks SET scheduled_at = ?, updated_at = ? WHERE task_id = ?",
                (clean_scheduled_at, utc_now(), task_id),
            )
            if result.rowcount == 0:
                raise KeyError(f"Task not found: {task_id}")

    def assign_phone(self, task_id: str, phone_id: str) -> None:
        with session(self.db_path) as connection:
            task = connection.execute("SELECT * FROM release_tasks WHERE task_id = ?", (task_id,)).fetchone()
            if not task:
                raise KeyError(f"Task not found: {task_id}")
            validate_phone_task_workflow(
                connection,
                phone_id=phone_id,
                publish_mode=str(task["publish_mode"] or "scheduled"),
                product_id=str(task["product_id"] or ""),
                product_link=str(task["product_link"] or ""),
                product_name=str(task["product_name"] or ""),
                product_search_title=str(task["product_search_title"] or ""),
                product_publish_name=str(task["product_publish_name"] or ""),
            )
            result = connection.execute(
                "UPDATE release_tasks SET phone_id = ?, updated_at = ? WHERE task_id = ?",
                (phone_id, utc_now(), task_id),
            )
            if result.rowcount == 0:
                raise KeyError(f"Task not found: {task_id}")

    def remove_published_tasks(self, task_ids: list[str]) -> dict[str, Any]:
        return self.remove_tasks(task_ids)

    def remove_tasks(self, task_ids: list[str]) -> dict[str, Any]:
        clean_ids = normalize_ids(task_ids)
        if not clean_ids:
            return {"requested": 0, "removed": 0, "skipped": [], "missing": []}
        placeholders_sql = ", ".join("?" for _ in clean_ids)
        with session(self.db_path) as connection:
            rows = connection.execute(
                f"SELECT task_id, status FROM release_tasks WHERE task_id IN ({placeholders_sql})",
                clean_ids,
            ).fetchall()
            existing = {str(row["task_id"]): str(row["status"]) for row in rows}
            protected_statuses = {"running", "removed"}
            to_remove = [
                task_id
                for task_id in clean_ids
                if task_id in existing and existing[task_id] not in protected_statuses
            ]
            if to_remove:
                remove_placeholders = ", ".join("?" for _ in to_remove)
                connection.execute(
                    f"""
                    UPDATE release_tasks
                    SET status = 'removed',
                        locked_by_worker = '',
                        lock_expires_at = NULL,
                        updated_at = ?
                    WHERE task_id IN ({remove_placeholders})
                    """,
                    (utc_now(), *to_remove),
                )
        return {
            "requested": len(clean_ids),
            "removed": len(to_remove),
            "skipped": [
                {"task_id": task_id, "status": existing[task_id], "reason": task_remove_skip_reason(existing[task_id])}
                for task_id in clean_ids
                if task_id in existing and task_id not in to_remove
            ],
            "missing": [task_id for task_id in clean_ids if task_id not in existing],
        }

    def next_due(
        self,
        *,
        phone_id: str = "",
        limit: int = 10,
        timezone: str = "Asia/Shanghai",
        preparation_window_minutes: int = 60,
        require_phone_ready: bool = True,
        allow_overdue: bool = False,
    ) -> list[dict[str, Any]]:
        now = datetime.now(resolve_timezone(timezone)).replace(microsecond=0)
        clauses = ["t.status IN ('pending', 'ready')"]
        params: list[Any] = []
        if phone_id:
            clauses.append("t.phone_id = ?")
            params.append(phone_id)
        clean_limit = max(1, min(limit, 100))
        candidate_limit = max(clean_limit, min(clean_limit * 10, 1000))
        params.append(candidate_limit)
        with session(self.db_path) as connection:
            rows = connection.execute(
                task_detail_sql("WHERE " + " AND ".join(clauses))
                + " ORDER BY t.scheduled_at ASC, t.created_at ASC LIMIT ?",
                params,
            ).fetchall()
        due_tasks: list[dict[str, Any]] = []
        for row in rows:
            task = task_row_to_dict(row)
            if task is None:
                continue
            eligible, reason = evaluate_task_candidate(
                task,
                now=now,
                preparation_window_minutes=preparation_window_minutes,
                require_phone_ready=require_phone_ready,
                allow_overdue=allow_overdue,
            )
            if eligible:
                task["queue_reason"] = reason
                due_tasks.append(task)
            if len(due_tasks) >= clean_limit:
                break
        return due_tasks


def normalize_scheduled_at(value: str, *, timezone: str = "Asia/Shanghai") -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("scheduled_at is required.")
    if text.casefold() == "now":
        return datetime.now(resolve_timezone(timezone)).replace(microsecond=0).isoformat()
    clean = text.replace(" ", "T", 1)
    try:
        parsed = datetime.fromisoformat(clean)
    except ValueError as exc:
        raise ValueError("scheduled_at must be ISO-like, for example 2026-05-01 10:30") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=resolve_timezone(timezone))
    return parsed.replace(microsecond=0).isoformat()


def task_remove_skip_reason(status: str) -> str:
    if status == "running":
        return "running tasks cannot be deleted"
    if status == "removed":
        return "task is already removed"
    return "task cannot be deleted"


def resolve_timezone(name: str):
    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001
        if name in {"Asia/Shanghai", "UTC+8", "+08:00"}:
            return datetime_timezone(timedelta(hours=8))
        if name in {"America/Sao_Paulo", "Brazil/East", "BRT", "UTC-3", "-03:00"}:
            return datetime_timezone(timedelta(hours=-3), name)
        return datetime_timezone.utc


def validate_publish_mode(value: str) -> None:
    if value not in PUBLISH_MODES:
        raise ValueError(f"Unsupported publish mode: {value}")


def validate_task_status(value: str) -> None:
    if value not in TASK_STATUSES:
        raise ValueError(f"Unsupported task status: {value}")


def validate_phone_task_workflow(
    connection: sqlite3.Connection,
    *,
    phone_id: str,
    publish_mode: str,
    product_id: str = "",
    product_link: str = "",
    product_name: str = "",
    product_search_title: str = "",
    product_publish_name: str = "",
) -> None:
    phone = connection.execute("SELECT account_type FROM phones WHERE phone_id = ?", (phone_id,)).fetchone()
    if not phone:
        raise KeyError(f"Phone not found: {phone_id}")
    require_account_workflow(
        account_type=str(phone["account_type"] or "marketing"),
        publish_mode=publish_mode,
        product_fields={
            "product_id": product_id,
            "product_link": product_link,
            "product_name": product_name,
            "product_search_title": product_search_title,
            "product_publish_name": product_publish_name,
        },
    )


def ensure_related_records(
    connection: sqlite3.Connection,
    video_id: str,
    caption_id: str,
    phone_id: str,
    product_id: str = "",
) -> sqlite3.Row | None:
    if not connection.execute("SELECT video_id FROM videos WHERE video_id = ?", (video_id,)).fetchone():
        raise KeyError(f"Video not found: {video_id}")
    if caption_id and not connection.execute("SELECT caption_id FROM captions WHERE caption_id = ?", (caption_id,)).fetchone():
        raise KeyError(f"Caption not found: {caption_id}")
    if not connection.execute("SELECT phone_id FROM phones WHERE phone_id = ?", (phone_id,)).fetchone():
        raise KeyError(f"Phone not found: {phone_id}")
    if not product_id:
        return None
    product = connection.execute("SELECT * FROM products WHERE product_id = ?", (product_id,)).fetchone()
    if not product:
        raise KeyError(f"Product not found: {product_id}")
    if str(product["status"]) != "active":
        raise ValueError(f"Product is not active: {product_id}")
    return product


def mark_related_assigned(connection: sqlite3.Connection, video_id: str, caption_id: str, now: str) -> None:
    connection.execute(
        "UPDATE videos SET status = 'assigned', updated_at = ? WHERE video_id = ? AND status = 'unused'",
        (now, video_id),
    )
    if caption_id:
        connection.execute(
            "UPDATE captions SET status = 'assigned', updated_at = ? WHERE caption_id = ? AND status = 'unused'",
            (now, caption_id),
        )


def record_task_usage(connection: sqlite3.Connection, task: sqlite3.Row, status: str, failure_reason: str, now: str) -> None:
    video_status = "published" if status == "published" else "failed" if status == "failed" else "assigned"
    caption_status = "used" if status == "published" else "assigned"
    connection.execute("UPDATE videos SET status = ?, updated_at = ? WHERE video_id = ?", (video_status, now, task["video_id"]))
    if task["caption_id"]:
        connection.execute(
            "UPDATE captions SET status = ?, updated_at = ?, used_count = CASE WHEN ? = 'published' THEN used_count + 1 ELSE used_count END, last_used_at = CASE WHEN ? = 'published' THEN ? ELSE last_used_at END WHERE caption_id = ?",
            (caption_status, now, status, status, now, task["caption_id"]),
        )
    if status == "published":
        connection.execute(
            "UPDATE videos SET used_count = used_count + 1, last_used_at = ? WHERE video_id = ?",
            (now, task["video_id"]),
        )
        connection.execute(
            "UPDATE phones SET daily_publish_count = daily_publish_count + 1, updated_at = ? WHERE phone_id = ?",
            (now, task["phone_id"]),
        )
    existing = connection.execute("SELECT usage_id FROM video_usage WHERE task_id = ? LIMIT 1", (task["task_id"],)).fetchone()
    if existing:
        connection.execute(
            "UPDATE video_usage SET result = ?, failure_reason = ?, used_at = ? WHERE usage_id = ?",
            (status, failure_reason, now, existing["usage_id"]),
        )
    else:
        connection.execute(
            """
            INSERT INTO video_usage(usage_id, video_id, task_id, caption_id, phone_id, used_at, result, failure_reason)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "USE-" + uuid.uuid4().hex[:12].upper(),
                task["video_id"],
                task["task_id"],
                task["caption_id"],
                task["phone_id"],
                now,
                status,
                failure_reason,
            ),
        )


def placeholders(values: set[str]) -> str:
    return ", ".join("?" for _ in values)


def normalize_ids(values: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value or "").strip()
        if item and item not in seen:
            seen.add(item)
            cleaned.append(item)
    return cleaned


def task_detail_sql(where_sql: str = "") -> str:
    return f"""
        SELECT
            t.*,
            v.title AS video_title,
            v.file_name AS video_file_name,
            v.file_path AS video_file_path,
            v.status AS video_status,
            c.content AS caption_content,
            c.status AS caption_status,
            p.device_name AS phone_name,
            p.account_name AS phone_account,
            p.account_type AS phone_account_type,
            p.adb_serial AS phone_adb_serial,
            p.current_status AS phone_status,
            pr.title AS product_title,
            pr.search_title AS product_library_search_title,
            pr.publish_name AS product_library_publish_name
        FROM release_tasks t
        JOIN videos v ON v.video_id = t.video_id
        LEFT JOIN captions c ON c.caption_id = t.caption_id
        JOIN phones p ON p.phone_id = t.phone_id
        LEFT JOIN products pr ON pr.product_id = t.product_id
        {where_sql}
    """


def task_row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    content = str(data.get("caption_content") or "")
    data["caption_preview"] = content.replace("\n", " ")[:80]
    return data

