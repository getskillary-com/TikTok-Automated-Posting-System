from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from mobile_phone_library.phone_repository import PhoneRepository
from shared.database import PROJECT_ROOT, init_database, session, utc_now
from shared.queue_rules import parse_datetime

from .worker_state import WorkerStateRepository


ACTIVE_WORKER_STATUSES = {"starting", "idle", "running"}
CLAIMABLE_OVERDUE_STATUSES = {"pending", "ready"}
OVERDUE_PUBLISH_MODES = {"scheduled", "timed"}
OVERDUE_PAUSE_REASON = "\u9884\u7ea6\u65f6\u95f4\u5df2\u8fc7\uff0c\u4efb\u52a1\u5c1a\u672a\u6267\u884c\uff1b\u8bf7\u91cd\u65b0\u8bbe\u7f6e\u672a\u6765\u9884\u7ea6\u65f6\u95f4\u540e\u91cd\u65b0\u5165\u961f\u3002"
OVERDUE_RESCHEDULE_REASON = "\u9884\u7ea6\u65f6\u95f4\u5df2\u8fc7\uff0csupervisor\u5df2\u81ea\u52a8\u987a\u5ef6\u5230\u672a\u6765\u65f6\u95f4\u5e76\u91cd\u65b0\u5165\u961f\u3002"


@dataclass(frozen=True)
class SupervisorOptions:
    account_type: str = "all"
    max_workers: int = 0
    dry_run: bool = False
    allow_publish: bool = False
    stop_before_final_publish: bool = False
    launch_workers: bool = True
    overdue_action: str = "reschedule"
    overdue_reschedule_minutes: int = 30
    interval_seconds: int = 60
    timezone: str = "Asia/Shanghai"
    poll_seconds: int = 5
    heartbeat_seconds: int = 10
    lease_seconds: int = 120
    post_run_cooldown_seconds: int = 90
    idle_wake_interval_seconds: int = 300
    stale_limit: int = 100


class WorkerSupervisor:
    def __init__(self, options: SupervisorOptions, db_path: str | Path | None = None) -> None:
        self.options = options
        self.db_path = db_path
        init_database(db_path)
        self.phone_repository = PhoneRepository(db_path)
        self.worker_state = WorkerStateRepository(db_path)

    def run_forever(self) -> dict[str, Any]:
        cycles = 0
        last_outcome: dict[str, Any] = {}
        while True:
            last_outcome = self.run_once()
            cycles += 1
            time.sleep(max(5, int(self.options.interval_seconds or 60)))
        return {"status": "stopped", "cycles": cycles, "last_outcome": last_outcome}

    def run_once(self) -> dict[str, Any]:
        stale_workers = self.list_stale_workers(limit=self.options.stale_limit)
        recovered_workers = [] if self.options.dry_run else self.worker_state.recover_stale(
            limit=self.options.stale_limit,
            reason="supervisor recovered stale worker",
        )
        dead_workers = self.worker_state.list_dead_process_workers(limit=self.options.stale_limit)
        recovered_dead_workers = [] if self.options.dry_run else self.worker_state.recover_dead_processes(
            limit=self.options.stale_limit,
            reason="supervisor recovered dead worker process",
        )
        overdue_tasks = self.list_overdue_tasks()
        paused_tasks: list[dict[str, Any]] = []
        rescheduled_tasks: list[dict[str, Any]] = []
        if self.options.overdue_action == "pause" and overdue_tasks and not self.options.dry_run:
            paused_tasks = self.pause_overdue_tasks([str(task["task_id"]) for task in overdue_tasks])
        if self.options.overdue_action == "reschedule" and overdue_tasks and not self.options.dry_run:
            rescheduled_tasks = self.reschedule_overdue_tasks([str(task["task_id"]) for task in overdue_tasks])

        phones = self.select_target_phones()
        active_workers = self.list_active_workers()
        active_phone_ids = {str(worker["phone_id"]) for worker in active_workers}
        missing_phones = [phone for phone in phones if str(phone.get("phone_id") or "") not in active_phone_ids]
        launched_workers: list[dict[str, Any]] = []
        if self.options.launch_workers:
            for phone in missing_phones:
                launched_workers.append(self.launch_worker(phone))

        return {
            "status": "dry_run" if self.options.dry_run else "ok",
            "stale_workers": stale_workers,
            "recovered_workers": recovered_workers,
            "dead_workers": dead_workers,
            "recovered_dead_workers": recovered_dead_workers,
            "overdue_tasks": overdue_tasks,
            "paused_tasks": paused_tasks,
            "rescheduled_tasks": rescheduled_tasks,
            "target_phones": summarize_phones(phones),
            "active_workers": active_workers,
            "missing_worker_phones": summarize_phones(missing_phones),
            "launched_workers": launched_workers,
        }

    def select_target_phones(self) -> list[dict[str, Any]]:
        phones = self.phone_repository.list_phones(
            status="online_idle",
            limit=1000,
            include_disabled=False,
            include_removed=False,
        )
        selected: list[dict[str, Any]] = []
        for phone in phones:
            if self.options.account_type not in {"", "all"} and str(phone.get("account_type") or "") != self.options.account_type:
                continue
            selected.append(phone)
        if self.options.max_workers > 0:
            return selected[: self.options.max_workers]
        return selected

    def list_active_workers(self) -> list[dict[str, Any]]:
        now = utc_now()
        with session(self.db_path) as connection:
            rows = connection.execute(
                f"""
                SELECT *
                FROM execution_workers
                WHERE status IN ({placeholders(ACTIVE_WORKER_STATUSES)})
                  AND lease_expires_at != ''
                  AND lease_expires_at >= ?
                ORDER BY updated_at DESC
                """,
                (*sorted(ACTIVE_WORKER_STATUSES), now),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_stale_workers(self, *, limit: int = 100) -> list[dict[str, Any]]:
        now = utc_now()
        with session(self.db_path) as connection:
            rows = connection.execute(
                f"""
                SELECT *
                FROM execution_workers
                WHERE lease_expires_at != ''
                  AND lease_expires_at < ?
                  AND status IN ({placeholders(ACTIVE_WORKER_STATUSES)})
                ORDER BY lease_expires_at ASC
                LIMIT ?
                """,
                (now, *sorted(ACTIVE_WORKER_STATUSES), max(1, min(limit, 500))),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_overdue_tasks(self) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        with session(self.db_path) as connection:
            rows = connection.execute(
                f"""
                SELECT
                    t.task_id,
                    t.status,
                    t.phone_id,
                    t.scheduled_at,
                    t.publish_mode,
                    t.retry_count,
                    t.max_retries,
                    p.account_name AS phone_account,
                    p.account_type AS phone_account_type,
                    p.device_name AS phone_name
                FROM release_tasks t
                JOIN phones p ON p.phone_id = t.phone_id
                WHERE t.status IN ({placeholders(CLAIMABLE_OVERDUE_STATUSES)})
                  AND t.publish_mode IN ({placeholders(OVERDUE_PUBLISH_MODES)})
                ORDER BY t.scheduled_at ASC, t.created_at ASC
                """,
                (*sorted(CLAIMABLE_OVERDUE_STATUSES), *sorted(OVERDUE_PUBLISH_MODES)),
            ).fetchall()
        overdue: list[dict[str, Any]] = []
        for row in rows:
            task = dict(row)
            try:
                scheduled_at = parse_datetime(str(task.get("scheduled_at") or ""), timezone.utc)
            except Exception as exc:  # noqa: BLE001
                task["overdue_error"] = str(exc)
                continue
            if scheduled_at <= now:
                task["overdue_reason"] = OVERDUE_PAUSE_REASON if self.options.overdue_action == "pause" else OVERDUE_RESCHEDULE_REASON
                overdue.append(task)
        return overdue

    def pause_overdue_tasks(self, task_ids: list[str]) -> list[dict[str, Any]]:
        if not task_ids:
            return []
        unique_task_ids = sorted(set(task_ids))
        now = utc_now()
        marker = placeholders(set(unique_task_ids))
        params = [OVERDUE_PAUSE_REASON, now, *unique_task_ids]
        with session(self.db_path) as connection:
            connection.execute(
                f"""
                UPDATE release_tasks
                SET status = 'paused',
                    failure_reason = ?,
                    locked_by_worker = '',
                    lock_expires_at = NULL,
                    updated_at = ?
                WHERE task_id IN ({marker})
                  AND status IN ('pending', 'ready')
                """,
                params,
            )
            rows = connection.execute(
                f"""
                SELECT task_id, status, phone_id, scheduled_at, publish_mode, failure_reason
                FROM release_tasks
                WHERE task_id IN ({marker})
                ORDER BY scheduled_at ASC, created_at ASC
                """,
                unique_task_ids,
            ).fetchall()
        return [dict(row) for row in rows]

    def reschedule_overdue_tasks(self, task_ids: list[str]) -> list[dict[str, Any]]:
        if not task_ids:
            return []
        unique_task_ids = sorted(set(task_ids))
        now_dt = datetime.now(timezone.utc).replace(microsecond=0)
        new_scheduled_at = (now_dt + timedelta(minutes=max(1, int(self.options.overdue_reschedule_minutes or 30)))).isoformat()
        now = now_dt.isoformat()
        marker = placeholders(set(unique_task_ids))
        params = [new_scheduled_at, OVERDUE_RESCHEDULE_REASON, now, *unique_task_ids]
        with session(self.db_path) as connection:
            connection.execute(
                f"""
                UPDATE release_tasks
                SET scheduled_at = ?,
                    status = 'pending',
                    failure_reason = ?,
                    locked_by_worker = '',
                    lock_expires_at = NULL,
                    updated_at = ?
                WHERE task_id IN ({marker})
                  AND status IN ('pending', 'ready')
                """,
                params,
            )
            rows = connection.execute(
                f"""
                SELECT task_id, status, phone_id, scheduled_at, publish_mode, failure_reason
                FROM release_tasks
                WHERE task_id IN ({marker})
                ORDER BY scheduled_at ASC, created_at ASC
                """,
                unique_task_ids,
            ).fetchall()
        return [dict(row) for row in rows]

    def launch_worker(self, phone: dict[str, Any]) -> dict[str, Any]:
        command = build_worker_command(phone, self.options)
        summary = {
            "phone_id": str(phone.get("phone_id") or ""),
            "account_name": str(phone.get("account_name") or ""),
            "adb_serial": str(phone.get("adb_serial") or ""),
            "command": command,
            "pid": "",
            "stdout": "",
            "stderr": "",
        }
        if self.options.dry_run:
            return summary

        log_dir = PROJECT_ROOT / "run_log" / "supervisor_workers"
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        phone_id = str(phone.get("phone_id") or "phone")
        stdout_path = log_dir / f"{stamp}-{phone_id}.out.log"
        stderr_path = log_dir / f"{stamp}-{phone_id}.err.log"
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        with stdout_path.open("ab") as stdout, stderr_path.open("ab") as stderr:
            process = subprocess.Popen(  # noqa: S603
                command,
                cwd=str(PROJECT_ROOT),
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                creationflags=creationflags,
            )
        summary["pid"] = str(process.pid)
        summary["stdout"] = str(stdout_path)
        summary["stderr"] = str(stderr_path)
        return summary


def build_worker_command(phone: dict[str, Any], options: SupervisorOptions) -> list[str]:
    module_argv = [
        "execution_queue",
        "workers",
        "--phone-id",
        str(phone.get("phone_id") or ""),
        "--account-type",
        str(phone.get("account_type") or options.account_type or "marketing"),
        "--max-workers",
        "1",
        "--expected-worker-count",
        "1",
        "--disable-steady-state-gate",
        "--timezone",
        options.timezone,
        "--poll-seconds",
        str(max(1, int(options.poll_seconds or 5))),
        "--heartbeat-seconds",
        str(max(1, int(options.heartbeat_seconds or 10))),
        "--lease-seconds",
        str(max(30, int(options.lease_seconds or 120))),
        "--post-run-cooldown-seconds",
        str(max(0, int(options.post_run_cooldown_seconds or 0))),
        "--idle-wake-interval-seconds",
        str(max(1, int(options.idle_wake_interval_seconds or 300))),
    ]
    if options.allow_publish:
        module_argv.append("--allow-publish")
    if options.stop_before_final_publish:
        module_argv.append("--stop-before-final-publish")
    return build_execution_queue_command(module_argv)


def build_execution_queue_command(module_argv: list[str]) -> list[str]:
    command_args = module_argv[1:] if module_argv and module_argv[0] == "execution_queue" else module_argv
    return [sys.executable, "-u", str(PROJECT_ROOT / "execution_queue_launcher.py"), *command_args]


def summarize_phones(phones: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {
            "phone_id": str(phone.get("phone_id") or ""),
            "account_name": str(phone.get("account_name") or ""),
            "device_name": str(phone.get("device_name") or ""),
            "account_type": str(phone.get("account_type") or ""),
            "adb_serial": str(phone.get("adb_serial") or ""),
        }
        for phone in phones
    ]


def placeholders(values: set[str]) -> str:
    return ", ".join("?" for _ in values)
