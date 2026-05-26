from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from shared.database import init_database, session


class WorkerStateRepository:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = db_path
        init_database(db_path)

    def register(
        self,
        *,
        worker_id: str,
        phone_id: str,
        adb_serial: str,
        pid: int,
        lease_seconds: int = 120,
    ) -> None:
        now = utc_now_text()
        expires_at = lease_expires_at(lease_seconds)
        with session(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            active = connection.execute(
                """
                SELECT worker_id, status, current_task_id, lease_expires_at
                FROM execution_workers
                WHERE phone_id = ?
                  AND worker_id != ?
                  AND status IN ('starting', 'idle', 'running')
                  AND lease_expires_at != ''
                  AND lease_expires_at >= ?
                ORDER BY lease_expires_at DESC
                LIMIT 1
                """,
                (phone_id, worker_id, now),
            ).fetchone()
            if active:
                raise RuntimeError(
                    "active worker already exists for phone "
                    f"{phone_id}: {active['worker_id']} "
                    f"status={active['status']} task={active['current_task_id']} "
                    f"lease_expires_at={active['lease_expires_at']}"
                )
            connection.execute(
                """
                INSERT INTO execution_workers(
                    worker_id, phone_id, adb_serial, status, pid, current_task_id,
                    current_attempt_id, started_at, heartbeat_at, lease_expires_at,
                    last_error, updated_at
                )
                VALUES(?, ?, ?, 'starting', ?, '', '', ?, ?, ?, '', ?)
                ON CONFLICT(worker_id) DO UPDATE SET
                    phone_id = excluded.phone_id,
                    adb_serial = excluded.adb_serial,
                    status = 'starting',
                    pid = excluded.pid,
                    current_task_id = '',
                    current_attempt_id = '',
                    started_at = excluded.started_at,
                    heartbeat_at = excluded.heartbeat_at,
                    lease_expires_at = excluded.lease_expires_at,
                    last_error = '',
                    updated_at = excluded.updated_at
                """,
                (worker_id, phone_id, adb_serial, pid, now, now, expires_at, now),
            )

    def heartbeat(
        self,
        *,
        worker_id: str,
        status: str,
        current_task_id: str = "",
        current_attempt_id: str = "",
        last_error: str = "",
        lease_seconds: int = 120,
    ) -> None:
        now = utc_now_text()
        with session(self.db_path) as connection:
            connection.execute(
                """
                UPDATE execution_workers
                SET status = ?,
                    current_task_id = ?,
                    current_attempt_id = ?,
                    heartbeat_at = ?,
                    lease_expires_at = ?,
                    last_error = ?,
                    updated_at = ?
                WHERE worker_id = ?
                """,
                (
                    status,
                    current_task_id,
                    current_attempt_id,
                    now,
                    lease_expires_at(lease_seconds),
                    last_error[-1000:],
                    now,
                    worker_id,
                ),
            )

    def finish(self, *, worker_id: str, status: str = "stopped", last_error: str = "") -> None:
        now = utc_now_text()
        with session(self.db_path) as connection:
            connection.execute(
                """
                UPDATE execution_workers
                SET status = ?,
                    current_task_id = '',
                    current_attempt_id = '',
                    heartbeat_at = ?,
                    lease_expires_at = '',
                    last_error = ?,
                    updated_at = ?
                WHERE worker_id = ?
                """,
                (status, now, last_error[-1000:], now, worker_id),
            )

    def list_workers(
        self,
        *,
        status: str = "",
        phone_id: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if phone_id:
            clauses.append("phone_id = ?")
            params.append(phone_id)
        where_sql = "WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(max(1, min(limit, 1000)))
        with session(self.db_path) as connection:
            rows = connection.execute(
                f"""
                SELECT *
                FROM execution_workers
                {where_sql}
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def recover_stale(self, *, limit: int = 50, reason: str = "worker lease expired") -> list[dict[str, Any]]:
        now = utc_now_text()
        recovered: list[dict[str, Any]] = []
        with session(self.db_path) as connection:
            workers = connection.execute(
                """
                SELECT *
                FROM execution_workers
                WHERE lease_expires_at != ''
                  AND lease_expires_at < ?
                  AND status IN ('starting', 'idle', 'running')
                ORDER BY lease_expires_at ASC
                LIMIT ?
                """,
                (now, max(1, min(limit, 500))),
            ).fetchall()
            for worker in workers:
                recovered.append(recover_worker(connection, worker, now=now, reason=reason))
        return recovered

    def recover_stale_for_phone(
        self,
        *,
        phone_id: str,
        limit: int = 50,
        reason: str = "worker lease expired",
    ) -> list[dict[str, Any]]:
        now = utc_now_text()
        recovered: list[dict[str, Any]] = []
        with session(self.db_path) as connection:
            workers = connection.execute(
                """
                SELECT *
                FROM execution_workers
                WHERE phone_id = ?
                  AND lease_expires_at != ''
                  AND lease_expires_at < ?
                  AND status IN ('starting', 'idle', 'running')
                ORDER BY lease_expires_at ASC
                LIMIT ?
                """,
                (phone_id, now, max(1, min(limit, 500))),
            ).fetchall()
            for worker in workers:
                recovered.append(recover_worker(connection, worker, now=now, reason=reason))
        return recovered

    def list_dead_process_workers(self, *, limit: int = 100) -> list[dict[str, Any]]:
        workers = self.list_active_process_workers(limit=max(1, min(limit, 500)))
        return [worker for worker in workers if not process_is_running(int(worker.get("pid") or 0))]

    def recover_dead_processes(self, *, limit: int = 50, reason: str = "worker process exited") -> list[dict[str, Any]]:
        now = utc_now_text()
        recovered: list[dict[str, Any]] = []
        with session(self.db_path) as connection:
            workers = connection.execute(
                """
                SELECT *
                FROM execution_workers
                WHERE status IN ('starting', 'idle', 'running')
                  AND pid > 0
                ORDER BY updated_at ASC
                LIMIT ?
                """,
                (max(1, min(limit * 3, 1000)),),
            ).fetchall()
            for worker in workers:
                if len(recovered) >= max(1, min(limit, 500)):
                    break
                if process_is_running(int(worker["pid"] or 0)):
                    continue
                recovered.append(recover_worker(connection, worker, now=now, reason=reason))
        return recovered

    def recover_dead_processes_for_phone(
        self,
        *,
        phone_id: str,
        limit: int = 50,
        reason: str = "worker process exited",
    ) -> list[dict[str, Any]]:
        now = utc_now_text()
        recovered: list[dict[str, Any]] = []
        with session(self.db_path) as connection:
            workers = connection.execute(
                """
                SELECT *
                FROM execution_workers
                WHERE phone_id = ?
                  AND status IN ('starting', 'idle', 'running')
                  AND pid > 0
                ORDER BY updated_at ASC
                LIMIT ?
                """,
                (phone_id, max(1, min(limit * 3, 1000))),
            ).fetchall()
            for worker in workers:
                if len(recovered) >= max(1, min(limit, 500)):
                    break
                if process_is_running(int(worker["pid"] or 0)):
                    continue
                recovered.append(recover_worker(connection, worker, now=now, reason=reason))
        return recovered

    def list_active_process_workers(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with session(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM execution_workers
                WHERE status IN ('starting', 'idle', 'running')
                  AND pid > 0
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (max(1, min(limit, 1000)),),
            ).fetchall()
        return [dict(row) for row in rows]


def utc_now_text() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def lease_expires_at(lease_seconds: int) -> str:
    seconds = max(30, int(lease_seconds or 120))
    return (datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=seconds)).isoformat()


def recover_worker(connection, worker: Any, *, now: str, reason: str) -> dict[str, Any]:
    worker_id = str(worker["worker_id"])
    tasks = connection.execute(
        """
        SELECT task_id, phone_id
        FROM release_tasks
        WHERE status = 'running' AND locked_by_worker = ?
        """,
        (worker_id,),
    ).fetchall()
    task_ids = [str(task["task_id"]) for task in tasks]
    for task in tasks:
        task_id = str(task["task_id"])
        phone_id = str(task["phone_id"])
        connection.execute(
            """
            UPDATE task_attempts
            SET ended_at = ?, result = 'stale_recovered', failure_reason = ?
            WHERE task_id = ? AND ended_at IS NULL
            """,
            (now, reason, task_id),
        )
        connection.execute(
            """
            UPDATE execution_logs
            SET ended_at = ?, result = 'stale_recovered', failure_reason = ?
            WHERE task_id = ? AND ended_at IS NULL
            """,
            (now, reason, task_id),
        )
        connection.execute(
            """
            UPDATE release_tasks
            SET status = 'pending',
                failure_reason = ?,
                locked_by_worker = '',
                lock_expires_at = NULL,
                updated_at = ?
            WHERE task_id = ?
            """,
            (reason, now, task_id),
        )
        connection.execute(
            """
            UPDATE phones
            SET current_status = 'online_idle', updated_at = ?
            WHERE phone_id = ? AND current_status = 'running'
            """,
            (now, phone_id),
        )
    connection.execute(
        """
        UPDATE execution_workers
        SET status = 'stale',
            current_task_id = '',
            current_attempt_id = '',
            heartbeat_at = ?,
            lease_expires_at = '',
            last_error = ?,
            updated_at = ?
        WHERE worker_id = ?
        """,
        (now, reason, now, worker_id),
    )
    return {
        "worker_id": worker_id,
        "phone_id": str(worker["phone_id"]),
        "adb_serial": str(worker["adb_serial"]),
        "task_ids": task_ids,
    }


def process_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        process_query_limited_information = 0x1000
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return ctypes.get_last_error() == 5
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True
