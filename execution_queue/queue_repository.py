from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from shared.database import init_database, session, utc_now
from shared.queue_rules import evaluate_task_candidate, resolve_timezone

from .models import BLOCKING_PHONE_STATUSES, CLAIMABLE_TASK_STATUSES, READY_PHONE_STATUS, ExecutionClaim, QueueCandidate


def new_attempt_id() -> str:
    return "ATT-" + uuid.uuid4().hex[:12].upper()


def new_log_id() -> str:
    return "LOG-" + uuid.uuid4().hex[:12].upper()


class ExecutionQueueRepository:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = db_path
        init_database(db_path)

    def list_candidates(
        self,
        *,
        task_id: str = "",
        phone_id: str = "",
        adb_serial: str = "",
        account_type: str = "",
        timezone: str = "Asia/Shanghai",
        preparation_window_minutes: int = 60,
        require_phone_ready: bool = True,
        allow_overdue: bool = False,
        limit: int = 20,
    ) -> list[QueueCandidate]:
        self.mark_completed_dry_run_tasks()
        now = datetime.now(resolve_timezone(timezone)).replace(microsecond=0)
        params: list[Any] = []
        where = [f"t.status IN ({placeholders(CLAIMABLE_TASK_STATUSES)})"]
        params.extend(sorted(CLAIMABLE_TASK_STATUSES))
        if task_id:
            where.append("t.task_id = ?")
            params.append(task_id)
        if phone_id:
            where.append("t.phone_id = ?")
            params.append(phone_id)
        if adb_serial:
            where.append("p.adb_serial = ?")
            params.append(adb_serial)
        if account_type:
            where.append("p.account_type = ?")
            params.append(account_type)
        params.append(max(1, min(limit, 200)))
        with session(self.db_path) as connection:
            rows = connection.execute(
                task_sql("WHERE " + " AND ".join(where))
                + " ORDER BY t.scheduled_at ASC, t.created_at ASC LIMIT ?",
                params,
            ).fetchall()
        candidates: list[QueueCandidate] = []
        for row in rows:
            task = row_to_dict(row)
            eligible, reason = evaluate_task_candidate(
                task,
                now=now,
                preparation_window_minutes=preparation_window_minutes,
                require_phone_ready=require_phone_ready,
                allow_overdue=allow_overdue,
            )
            candidates.append(QueueCandidate(task=task, eligible=eligible, reason=reason))
        return candidates

    def mark_completed_dry_run_tasks(self) -> int:
        with session(self.db_path) as connection:
            result = connection.execute(
                """
                UPDATE release_tasks
                SET status = 'dry_run',
                    locked_by_worker = '',
                    lock_expires_at = NULL,
                    updated_at = (
                        SELECT e.ended_at
                        FROM execution_logs e
                        WHERE e.task_id = release_tasks.task_id
                          AND e.result = 'dry_run'
                          AND e.ended_at IS NOT NULL
                        ORDER BY e.ended_at DESC
                        LIMIT 1
                    )
                WHERE status IN ('pending', 'ready')
                  AND EXISTS (
                    SELECT 1
                    FROM execution_logs e
                    WHERE e.task_id = release_tasks.task_id
                      AND e.result = 'dry_run'
                      AND e.ended_at IS NOT NULL
                      AND e.ended_at >= COALESCE(release_tasks.updated_at, '')
                      AND e.ended_at = (
                        SELECT MAX(e2.ended_at)
                        FROM execution_logs e2
                        WHERE e2.task_id = release_tasks.task_id
                          AND e2.ended_at IS NOT NULL
                      )
                  )
                """
            )
        return result.rowcount

    def claim_next(
        self,
        *,
        task_id: str = "",
        phone_id: str = "",
        adb_serial: str = "",
        account_type: str = "",
        worker_id: str = "",
        lease_seconds: int = 300,
        timezone: str = "Asia/Shanghai",
        preparation_window_minutes: int = 60,
        require_phone_ready: bool = True,
        allow_overdue: bool = False,
    ) -> ExecutionClaim | None:
        candidates = self.list_candidates(
            task_id=task_id,
            phone_id=phone_id,
            adb_serial=adb_serial,
            account_type=account_type,
            timezone=timezone,
            preparation_window_minutes=preparation_window_minutes,
            require_phone_ready=require_phone_ready,
            allow_overdue=allow_overdue,
            limit=50,
        )
        selected = next((candidate for candidate in candidates if candidate.eligible), None)
        if not selected:
            return None
        selected_task_id = str(selected.task["task_id"])
        now = utc_now()
        attempt_id = new_attempt_id()
        log_id = new_log_id()

        with session(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(task_sql("WHERE t.task_id = ?"), (selected_task_id,)).fetchone()
            if not row:
                raise KeyError(f"Task not found: {selected_task_id}")
            task = row_to_dict(row)
            if phone_id and str(task.get("phone_id") or "") != phone_id:
                return None
            if adb_serial and str(task.get("phone_adb_serial") or "") != adb_serial:
                return None
            if account_type and str(task.get("phone_account_type") or "") != account_type:
                return None
            eligible, reason = evaluate_task_candidate(
                task,
                now=datetime.now(resolve_timezone(timezone)).replace(microsecond=0),
                preparation_window_minutes=preparation_window_minutes,
                require_phone_ready=require_phone_ready,
                allow_overdue=allow_overdue,
            )
            if not eligible:
                return None
            attempt_no = next_attempt_no(connection, selected_task_id)
            phone_id = str(task["phone_id"])
            if phone_has_running_task(connection, phone_id, exclude_task_id=selected_task_id):
                return None
            connection.execute(
                """
                UPDATE release_tasks
                SET status = 'running',
                    locked_by_worker = ?,
                    lock_expires_at = ?,
                    updated_at = ?
                WHERE task_id = ?
                """,
                (worker_id, lease_expires_at(lease_seconds) if worker_id else None, now, selected_task_id),
            )
            connection.execute(
                "UPDATE phones SET current_status = 'running', updated_at = ? WHERE phone_id = ?",
                (now, phone_id),
            )
            connection.execute(
                """
                INSERT INTO task_attempts(attempt_id, task_id, attempt_no, phone_id, started_at, result, run_dir)
                VALUES(?, ?, ?, ?, ?, 'running', '')
                """,
                (attempt_id, selected_task_id, attempt_no, phone_id, now),
            )
            connection.execute(
                """
                INSERT INTO execution_logs(log_id, task_id, phone_id, started_at, result, created_at)
                VALUES(?, ?, ?, ?, 'running', ?)
                """,
                (log_id, selected_task_id, phone_id, now, now),
            )
        return ExecutionClaim(
            task_id=selected_task_id,
            attempt_id=attempt_id,
            log_id=log_id,
            phone_id=phone_id,
            attempt_no=attempt_no,
            task=selected.task,
        )

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with session(self.db_path) as connection:
            row = connection.execute(task_sql("WHERE t.task_id = ?"), (task_id,)).fetchone()
        return row_to_dict(row) if row else None

    def mark_dry_run(self, claim: ExecutionClaim, *, run_dir: str, state_path: str) -> None:
        ended_at = utc_now()
        original_phone_status = str(claim.task.get("phone_status") or READY_PHONE_STATUS)
        if original_phone_status in BLOCKING_PHONE_STATUSES:
            original_phone_status = READY_PHONE_STATUS
        with session(self.db_path) as connection:
            connection.execute(
                """
                UPDATE task_attempts
                SET ended_at = ?, result = 'dry_run', run_dir = ?
                WHERE attempt_id = ?
                """,
                (ended_at, run_dir, claim.attempt_id),
            )
            connection.execute(
                """
                UPDATE execution_logs
                SET ended_at = ?, result = 'dry_run', state_path = ?, trace_dir = ?, screenshot_dir = ?
                WHERE log_id = ?
                """,
                (ended_at, state_path, str(Path(run_dir) / "traces"), str(Path(run_dir) / "screenshots"), claim.log_id),
            )
            connection.execute(
                """
                UPDATE release_tasks
                SET status = 'dry_run',
                    run_dir = ?,
                    locked_by_worker = '',
                    lock_expires_at = NULL,
                    updated_at = ?
                WHERE task_id = ?
                """,
                (run_dir, ended_at, claim.task_id),
            )
            connection.execute(
                "UPDATE phones SET current_status = ?, updated_at = ? WHERE phone_id = ?",
                (original_phone_status, ended_at, claim.phone_id),
            )

    def mark_debug_ready(self, claim: ExecutionClaim, *, run_dir: str, state_path: str) -> None:
        ended_at = utc_now()
        actual_scheduled_at = read_state_scheduled_at(state_path)
        with session(self.db_path) as connection:
            connection.execute(
                """
                UPDATE task_attempts
                SET ended_at = ?, result = 'debug_ready', run_dir = ?
                WHERE attempt_id = ?
                """,
                (ended_at, run_dir, claim.attempt_id),
            )
            connection.execute(
                """
                UPDATE execution_logs
                SET ended_at = ?, result = 'debug_ready', state_path = ?, trace_dir = ?, screenshot_dir = ?
                WHERE log_id = ?
                """,
                (ended_at, state_path, str(Path(run_dir) / "traces"), str(Path(run_dir) / "screenshots"), claim.log_id),
            )
            connection.execute(
                """
                UPDATE release_tasks
                SET status = 'debug_ready',
                    scheduled_at = CASE WHEN ? != '' THEN ? ELSE scheduled_at END,
                    failure_reason = '',
                    run_dir = ?,
                    locked_by_worker = '',
                    lock_expires_at = NULL,
                    updated_at = ?
                WHERE task_id = ?
                """,
                (actual_scheduled_at, actual_scheduled_at, run_dir, ended_at, claim.task_id),
            )
            connection.execute(
                "UPDATE phones SET current_status = ?, updated_at = ? WHERE phone_id = ?",
                (READY_PHONE_STATUS, ended_at, claim.phone_id),
            )

    def mark_success(self, claim: ExecutionClaim, *, run_dir: str, state_path: str) -> None:
        ended_at = utc_now()
        actual_scheduled_at = read_state_scheduled_at(state_path)
        with session(self.db_path) as connection:
            task = connection.execute("SELECT * FROM release_tasks WHERE task_id = ?", (claim.task_id,)).fetchone()
            if not task:
                raise KeyError(f"Task not found: {claim.task_id}")
            connection.execute(
                """
                UPDATE task_attempts
                SET ended_at = ?, result = 'published', run_dir = ?
                WHERE attempt_id = ?
                """,
                (ended_at, run_dir, claim.attempt_id),
            )
            connection.execute(
                """
                UPDATE execution_logs
                SET ended_at = ?, result = 'published', state_path = ?, trace_dir = ?, screenshot_dir = ?
                WHERE log_id = ?
                """,
                (ended_at, state_path, str(Path(run_dir) / "traces"), str(Path(run_dir) / "screenshots"), claim.log_id),
            )
            connection.execute(
                """
                UPDATE release_tasks
                SET status = 'published',
                    scheduled_at = CASE WHEN ? != '' THEN ? ELSE scheduled_at END,
                    failure_reason = '',
                    run_dir = ?,
                    locked_by_worker = '',
                    lock_expires_at = NULL,
                    updated_at = ?
                WHERE task_id = ?
                """,
                (actual_scheduled_at, actual_scheduled_at, run_dir, ended_at, claim.task_id),
            )
            connection.execute(
                """
                UPDATE videos
                SET status = 'published', used_count = used_count + 1, last_used_at = ?, updated_at = ?
                WHERE video_id = ?
                """,
                (ended_at, ended_at, task["video_id"]),
            )
            if task["caption_id"]:
                connection.execute(
                    """
                    UPDATE captions
                    SET status = 'used', used_count = used_count + 1, last_used_at = ?, updated_at = ?
                    WHERE caption_id = ?
                    """,
                    (ended_at, ended_at, task["caption_id"]),
                )
            connection.execute(
                """
                UPDATE phones
                SET current_status = 'online_idle', daily_publish_count = daily_publish_count + 1, updated_at = ?
                WHERE phone_id = ?
                """,
                (ended_at, claim.phone_id),
            )
            upsert_video_usage(connection, task, "published", "", ended_at)

    def mark_failure(
        self,
        claim: ExecutionClaim,
        *,
        run_dir: str,
        state_path: str,
        failure_reason: str,
        retryable: bool = True,
    ) -> str:
        ended_at = utc_now()
        actual_scheduled_at = read_state_scheduled_at(state_path)
        with session(self.db_path) as connection:
            task = connection.execute("SELECT * FROM release_tasks WHERE task_id = ?", (claim.task_id,)).fetchone()
            if not task:
                raise KeyError(f"Task not found: {claim.task_id}")
            retry_count = int(task["retry_count"] or 0) + 1
            max_retries = int(task["max_retries"] or 0)
            next_status = "failed" if (not retryable or retry_count > max_retries) else "pending"
            connection.execute(
                """
                UPDATE task_attempts
                SET ended_at = ?, result = 'failed', failure_reason = ?, run_dir = ?
                WHERE attempt_id = ?
                """,
                (ended_at, failure_reason, run_dir, claim.attempt_id),
            )
            connection.execute(
                """
                UPDATE execution_logs
                SET ended_at = ?, result = 'failed', failure_reason = ?, state_path = ?, trace_dir = ?, screenshot_dir = ?
                WHERE log_id = ?
                """,
                (ended_at, failure_reason, state_path, str(Path(run_dir) / "traces"), str(Path(run_dir) / "screenshots"), claim.log_id),
            )
            connection.execute(
                """
                UPDATE release_tasks
                SET status = ?,
                    scheduled_at = CASE WHEN ? != '' THEN ? ELSE scheduled_at END,
                    retry_count = ?,
                    failure_reason = ?,
                    run_dir = ?,
                    locked_by_worker = '',
                    lock_expires_at = NULL,
                    updated_at = ?
                WHERE task_id = ?
                """,
                (next_status, actual_scheduled_at, actual_scheduled_at, retry_count, failure_reason, run_dir, ended_at, claim.task_id),
            )
            connection.execute(
                "UPDATE phones SET current_status = 'error', updated_at = ? WHERE phone_id = ?",
                (ended_at, claim.phone_id),
            )
            if next_status == "failed":
                connection.execute(
                    "UPDATE videos SET status = 'failed', updated_at = ? WHERE video_id = ?",
                    (ended_at, task["video_id"]),
                )
                upsert_video_usage(connection, task, "failed", failure_reason, ended_at)
        return next_status


def next_attempt_no(connection: sqlite3.Connection, task_id: str) -> int:
    row = connection.execute("SELECT COALESCE(MAX(attempt_no), 0) + 1 AS next_no FROM task_attempts WHERE task_id = ?", (task_id,)).fetchone()
    return int(row["next_no"] if row else 1)


def phone_has_running_task(connection: sqlite3.Connection, phone_id: str, *, exclude_task_id: str = "") -> bool:
    params: list[Any] = [phone_id]
    where = "phone_id = ? AND status = 'running'"
    if exclude_task_id:
        where += " AND task_id != ?"
        params.append(exclude_task_id)
    row = connection.execute(
        f"SELECT task_id FROM release_tasks WHERE {where} LIMIT 1",
        params,
    ).fetchone()
    return row is not None


def placeholders(values: set[str]) -> str:
    return ", ".join("?" for _ in values)


def lease_expires_at(lease_seconds: int) -> str:
    seconds = max(60, int(lease_seconds or 300))
    return (datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=seconds)).isoformat()


def task_sql(where_sql: str = "") -> str:
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
            p.app_package AS phone_app_package,
            p.remote_video_dir AS phone_remote_video_dir,
            p.current_status AS phone_status
        FROM release_tasks t
        JOIN videos v ON v.video_id = t.video_id
        LEFT JOIN captions c ON c.caption_id = t.caption_id
        JOIN phones p ON p.phone_id = t.phone_id
        {where_sql}
    """


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def read_state_scheduled_at(state_path: str) -> str:
    path = Path(str(state_path or ""))
    if not path.exists():
        return ""
    try:
        with path.open("r", encoding="utf-8") as file:
            state = json.load(file)
    except Exception:  # noqa: BLE001
        return ""
    if not isinstance(state, dict):
        return ""
    return str(state.get("scheduled_at") or "").strip()


def upsert_video_usage(connection: sqlite3.Connection, task: sqlite3.Row, result: str, failure_reason: str, used_at: str) -> None:
    existing = connection.execute("SELECT usage_id FROM video_usage WHERE task_id = ? LIMIT 1", (task["task_id"],)).fetchone()
    if existing:
        connection.execute(
            "UPDATE video_usage SET result = ?, failure_reason = ?, used_at = ? WHERE usage_id = ?",
            (result, failure_reason, used_at, existing["usage_id"]),
        )
        return
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
            used_at,
            result,
            failure_reason,
        ),
    )

