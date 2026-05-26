from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from shared.database import PROJECT_ROOT, init_database, session, utc_now

from .models import ExecutionClaim, PipelineRunPlan, PipelineRunResult, QueueCandidate
from .pipeline_adapter import command_to_text
from .supervisor import SupervisorOptions, WorkerSupervisor
from .queue_service import ExecutionQueueService
from .worker_manager import WorkerManager, WorkerManagerOptions
from .worker_state import WorkerStateRepository


try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run release tasks through the upload system.")
    parser.add_argument("--db", default="", help="SQLite database path. Defaults to storage/group_control_system.db.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init-db", help="Create or update database tables.")

    preview_parser = subparsers.add_parser("preview", help="Preview tasks that can be claimed by the queue.")
    add_queue_filters(preview_parser)
    preview_parser.add_argument("--limit", type=int, default=20)

    claim_parser = subparsers.add_parser("claim-next", help="Claim one task and mark it running. Mainly for debugging.")
    add_queue_filters(claim_parser)

    run_parser = subparsers.add_parser("run-once", help="Claim one task and run the upload pipeline.")
    add_queue_filters(run_parser)
    run_parser.add_argument("--dry-run", action="store_true", help="Only generate run files and command; do not call upload_system.")
    run_parser.add_argument("--pipeline-dry-run", action="store_true", help="Call upload_system with --dry-run. ADB is still required.")
    run_parser.add_argument("--allow-publish", action="store_true", help="Allow the final publish/schedule tap in upload_system.")
    run_parser.add_argument("--stop-before-final-publish", action="store_true", help="Run live steps until the final publish tap, then stop as debug_ready.")
    run_parser.add_argument("--timeout-seconds", type=int, default=7200)

    loop_parser = subparsers.add_parser("run-loop", help="Continuously poll and execute claimable tasks.")
    add_queue_filters(loop_parser)
    loop_parser.add_argument("--dry-run", action="store_true", help="Only generate one or more run plans; does not call upload_system.")
    loop_parser.add_argument("--pipeline-dry-run", action="store_true", help="Call upload_system with --dry-run. ADB is still required.")
    loop_parser.add_argument("--allow-publish", action="store_true", help="Allow the final publish/schedule tap in upload_system.")
    loop_parser.add_argument("--stop-before-final-publish", action="store_true", help="Run live steps until the final publish tap, then stop as debug_ready.")
    loop_parser.add_argument("--timeout-seconds", type=int, default=7200)
    loop_parser.add_argument("--poll-seconds", type=int, default=5)
    loop_parser.add_argument("--max-runs", type=int, default=0, help="Stop after this many task runs. 0 means keep polling.")
    loop_parser.add_argument("--stop-when-idle", action="store_true", help="Stop when no task is claimable.")

    workers_parser = subparsers.add_parser("workers", help="Run one serial worker per phone, with phones executing in parallel.")
    add_queue_filters(workers_parser)
    workers_parser.add_argument("--dry-run", action="store_true", help="Only generate run files and command; do not call upload_system.")
    workers_parser.add_argument("--pipeline-dry-run", action="store_true", help="Call upload_system with --dry-run. ADB is still required.")
    workers_parser.add_argument("--allow-publish", action="store_true", help="Allow the final publish/schedule tap in upload_system.")
    workers_parser.add_argument("--stop-before-final-publish", action="store_true", help="Run live steps until the final publish tap, then stop as debug_ready.")
    workers_parser.add_argument("--timeout-seconds", type=int, default=7200)
    workers_parser.add_argument("--poll-seconds", type=int, default=5)
    workers_parser.add_argument("--max-workers", type=int, default=5)
    workers_parser.add_argument("--stagger-seconds", type=int, default=25)
    workers_parser.add_argument("--max-runs-per-worker", type=int, default=0)
    workers_parser.add_argument("--stop-when-idle", action="store_true", help="Stop workers when their phone has no claimable task.")
    workers_parser.add_argument("--heartbeat-seconds", type=int, default=10)
    workers_parser.add_argument("--lease-seconds", type=int, default=120)
    workers_parser.add_argument("--idle-wake-interval-seconds", type=int, default=300)
    workers_parser.add_argument("--disable-idle-wake", action="store_true", help="Disable periodic wake/unlock while a worker is idle.")
    workers_parser.add_argument("--expected-worker-count", type=int, default=5)
    workers_parser.add_argument("--disable-steady-state-gate", action="store_true", help="Bypass the 5-phone steady-state readiness gate.")
    workers_parser.add_argument("--require-task-per-phone", action="store_true", help="Require at least one eligible task for every selected phone before starting.")
    workers_parser.add_argument(
        "--post-run-cooldown-seconds",
        type=int,
        default=90,
        help="Wait this many seconds between serial tasks on the same phone.",
    )
    workers_parser.add_argument(
        "--continue-on-failure",
        action="store_true",
        help="Keep claiming the next serial task on the same phone after a failed run.",
    )

    workers_status_parser = subparsers.add_parser("workers-status", help="Show persisted phone worker heartbeat state.")
    workers_status_parser.add_argument("--status", default="")
    workers_status_parser.add_argument("--phone-id", default="")
    workers_status_parser.add_argument("--limit", type=int, default=100)

    recover_parser = subparsers.add_parser("recover-stale", help="Release running tasks held by expired phone workers.")
    recover_parser.add_argument("--limit", type=int, default=50)
    recover_parser.add_argument("--reason", default="worker lease expired")

    stop_all_parser = subparsers.add_parser("stop-all", help="Stop supervisor/workers and remove active queue tasks.")
    stop_all_parser.add_argument("--reason", default="execution_queue stop-all requested")
    stop_all_parser.add_argument("--keep-tasks", action="store_true", help="Stop workers but keep pending/ready/running/paused tasks.")
    stop_all_parser.add_argument("--dry-run", action="store_true", help="Show what would be stopped without killing processes or changing DB rows.")

    supervisor_parser = subparsers.add_parser("supervisor", help="Keep marketing workers healthy and pause overdue scheduled tasks.")
    supervisor_parser.add_argument("--dry-run", action="store_true", help="Show planned recovery, overdue handling, and worker launches without changing state.")
    supervisor_parser.add_argument("--once", action="store_true", help="Run one supervisor cycle and exit.")
    supervisor_parser.add_argument("--interval-seconds", type=int, default=60)
    supervisor_parser.add_argument("--account-type", default="all")
    supervisor_parser.add_argument("--max-workers", type=int, default=0, help="Maximum target phones. 0 means all matching phones.")
    supervisor_parser.add_argument("--allow-publish", action="store_true", help="Allow launched workers to perform real publish/schedule taps.")
    supervisor_parser.add_argument("--stop-before-final-publish", action="store_true", help="Launch workers in pre-publish debug mode.")
    supervisor_parser.add_argument("--no-launch-workers", action="store_true", help="Only recover stale workers and handle overdue tasks.")
    supervisor_parser.add_argument("--overdue-action", choices=("pause", "reschedule", "report", "ignore"), default="reschedule")
    supervisor_parser.add_argument("--overdue-reschedule-minutes", type=int, default=30)
    supervisor_parser.add_argument("--timezone", default="Asia/Shanghai")
    supervisor_parser.add_argument("--poll-seconds", type=int, default=5)
    supervisor_parser.add_argument("--heartbeat-seconds", type=int, default=10)
    supervisor_parser.add_argument("--lease-seconds", type=int, default=120)
    supervisor_parser.add_argument("--post-run-cooldown-seconds", type=int, default=90)
    supervisor_parser.add_argument("--idle-wake-interval-seconds", type=int, default=300)
    supervisor_parser.add_argument("--stale-limit", type=int, default=100)
    return parser


def add_queue_filters(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--task-id", default="")
    parser.add_argument("--phone-id", default="")
    parser.add_argument("--adb-serial", default="")
    parser.add_argument("--account-type", default="")
    parser.add_argument("--timezone", default="Asia/Shanghai")
    parser.add_argument("--preparation-window-minutes", type=int, default=60)
    parser.add_argument("--ignore-phone-status", action="store_true", help="Allow claiming phones that are not online_idle, except running/disabled.")
    parser.add_argument("--allow-overdue", action="store_true", help="Allow scheduled/timed tasks whose target time is already past.")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    db_path = Path(args.db) if args.db else None

    if args.command == "init-db":
        path = init_database(db_path)
        print(f"database ready: {path}")
        return 0

    service = ExecutionQueueService(db_path)

    if args.command == "preview":
        candidates = service.preview(
            task_id=args.task_id,
            phone_id=args.phone_id,
            adb_serial=args.adb_serial,
            account_type=args.account_type,
            timezone=args.timezone,
            preparation_window_minutes=args.preparation_window_minutes,
            require_phone_ready=not args.ignore_phone_status,
            allow_overdue=args.allow_overdue,
            limit=args.limit,
        )
        print_candidates(candidates)
        return 0

    if args.command == "claim-next":
        claim = service.claim_next(
            task_id=args.task_id,
            phone_id=args.phone_id,
            adb_serial=args.adb_serial,
            account_type=args.account_type,
            timezone=args.timezone,
            preparation_window_minutes=args.preparation_window_minutes,
            require_phone_ready=not args.ignore_phone_status,
            allow_overdue=args.allow_overdue,
        )
        if claim is None:
            print("no claimable task")
            return 0
        print_claim(claim)
        print("注意：该命令只锁定任务，不执行上传。后续需要人工恢复或让执行器接管。")
        return 0

    if args.command == "run-once":
        outcome = service.run_once(
            task_id=args.task_id,
            phone_id=args.phone_id,
            adb_serial=args.adb_serial,
            account_type=args.account_type,
            dry_run=args.dry_run,
            pipeline_dry_run=args.pipeline_dry_run,
            allow_publish=args.allow_publish,
            stop_before_final_publish=args.stop_before_final_publish,
            timezone=args.timezone,
            preparation_window_minutes=args.preparation_window_minutes,
            require_phone_ready=not args.ignore_phone_status,
            allow_overdue=args.allow_overdue,
            timeout_seconds=args.timeout_seconds,
        )
        print_outcome(outcome)
        return 0 if outcome.get("status") not in {"failed"} else 1

    if args.command == "run-loop":
        outcome = service.run_loop(
            task_id=args.task_id,
            phone_id=args.phone_id,
            adb_serial=args.adb_serial,
            account_type=args.account_type,
            dry_run=args.dry_run,
            pipeline_dry_run=args.pipeline_dry_run,
            allow_publish=args.allow_publish,
            stop_before_final_publish=args.stop_before_final_publish,
            timezone=args.timezone,
            preparation_window_minutes=args.preparation_window_minutes,
            require_phone_ready=not args.ignore_phone_status,
            allow_overdue=args.allow_overdue,
            timeout_seconds=args.timeout_seconds,
            poll_seconds=args.poll_seconds,
            max_runs=args.max_runs,
            stop_when_idle=args.stop_when_idle,
        )
        print_loop_outcome(outcome)
        failed = any(item.get("status") == "failed" for item in outcome.get("outcomes", []))
        return 1 if failed else 0

    if args.command == "workers":
        manager = WorkerManager(
            WorkerManagerOptions(
                phone_ids=tuple(parse_csv(args.phone_id)),
                adb_serials=tuple(parse_csv(args.adb_serial)),
                account_type=args.account_type,
                max_workers=args.max_workers,
                stagger_seconds=args.stagger_seconds,
                dry_run=args.dry_run,
                pipeline_dry_run=args.pipeline_dry_run,
                allow_publish=args.allow_publish,
                stop_before_final_publish=args.stop_before_final_publish,
                timezone=args.timezone,
                preparation_window_minutes=args.preparation_window_minutes,
                require_phone_ready=not args.ignore_phone_status,
                allow_overdue=args.allow_overdue,
                timeout_seconds=args.timeout_seconds,
                poll_seconds=args.poll_seconds,
                max_runs_per_worker=args.max_runs_per_worker,
                stop_when_idle=args.stop_when_idle,
                heartbeat_seconds=args.heartbeat_seconds,
                lease_seconds=args.lease_seconds,
                post_run_cooldown_seconds=args.post_run_cooldown_seconds,
                stop_on_failure=not args.continue_on_failure,
                idle_wake_enabled=not args.disable_idle_wake,
                idle_wake_interval_seconds=args.idle_wake_interval_seconds,
                steady_state_gate_enabled=not args.disable_steady_state_gate,
                expected_worker_count=args.expected_worker_count,
                require_task_per_phone=args.require_task_per_phone,
            ),
            db_path,
        )
        outcome = manager.run_forever()
        print_workers_outcome(outcome)
        if outcome.get("status") == "blocked":
            return 1
        failed = any(worker_failed(item) for item in outcome.get("outcomes", []))
        return 1 if failed else 0

    if args.command == "workers-status":
        workers = WorkerStateRepository(db_path).list_workers(
            status=args.status,
            phone_id=args.phone_id,
            limit=args.limit,
        )
        print_worker_states(workers)
        return 0

    if args.command == "recover-stale":
        recovered = WorkerStateRepository(db_path).recover_stale(limit=args.limit, reason=args.reason)
        print_recovered_workers(recovered)
        return 0

    if args.command == "stop-all":
        outcome = stop_all(db_path, reason=args.reason, keep_tasks=args.keep_tasks, dry_run=args.dry_run)
        print_stop_all_outcome(outcome)
        return 0 if outcome.get("verified") else 1

    if args.command == "supervisor":
        if args.allow_publish and args.stop_before_final_publish:
            print("refusing mixed mode: use either --allow-publish or --stop-before-final-publish, not both.")
            return 2
        if not args.dry_run and not args.no_launch_workers and not args.allow_publish and not args.stop_before_final_publish:
            print("refusing to launch real workers without --allow-publish. Use --dry-run to preview, or --stop-before-final-publish for debug.")
            return 2
        supervisor = WorkerSupervisor(
            SupervisorOptions(
                account_type=args.account_type,
                max_workers=args.max_workers,
                dry_run=args.dry_run,
                allow_publish=args.allow_publish,
                stop_before_final_publish=args.stop_before_final_publish,
                launch_workers=not args.no_launch_workers,
                overdue_action=args.overdue_action,
                overdue_reschedule_minutes=args.overdue_reschedule_minutes,
                interval_seconds=args.interval_seconds,
                timezone=args.timezone,
                poll_seconds=args.poll_seconds,
                heartbeat_seconds=args.heartbeat_seconds,
                lease_seconds=args.lease_seconds,
                post_run_cooldown_seconds=args.post_run_cooldown_seconds,
                idle_wake_interval_seconds=args.idle_wake_interval_seconds,
                stale_limit=args.stale_limit,
            ),
            db_path,
        )
        if args.once:
            print_supervisor_outcome(supervisor.run_once())
            return 0
        write_supervisor_pid()
        try:
            while True:
                print_supervisor_outcome(supervisor.run_once())
                time.sleep(max(5, int(args.interval_seconds or 60)))
        except KeyboardInterrupt:
            print("supervisor stopped")
            return 0

    parser.error(f"Unknown command: {args.command}")
    return 2


def stop_all(
    db_path: Path | None,
    *,
    reason: str,
    keep_tasks: bool,
    dry_run: bool,
) -> dict[str, object]:
    resolved_db_path = init_database(db_path)
    now_stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup_path = resolved_db_path.with_name(f"{resolved_db_path.stem}.stop-all-{now_stamp}{resolved_db_path.suffix}")
    supervisor_pid = read_supervisor_pid()
    workers = active_worker_processes(db_path)
    worker_pids = [int(worker.get("pid") or 0) for worker in workers if int(worker.get("pid") or 0) > 0]
    pre_active_tasks, _pre_active_workers = queue_activity_counts(db_path)
    kill_targets = []
    if supervisor_pid:
        kill_targets.append({"kind": "supervisor", "pid": supervisor_pid})
    kill_targets.extend({"kind": "worker", "pid": pid} for pid in sorted(set(worker_pids)))

    killed: list[dict[str, object]] = []
    if not dry_run:
        shutil.copy2(resolved_db_path, backup_path)
        for target in kill_targets:
            killed.append(kill_process(int(target["pid"]), str(target["kind"])))
        cleanup_queue_state(db_path, reason=reason, keep_tasks=keep_tasks)

    active_tasks, active_workers = queue_activity_counts(db_path)
    return {
        "dry_run": dry_run,
        "db_path": str(resolved_db_path),
        "backup_path": "" if dry_run else str(backup_path),
        "supervisor_pid": supervisor_pid,
        "worker_pids": sorted(set(worker_pids)),
        "killed": killed,
        "removed_tasks": 0 if dry_run or keep_tasks else pre_active_tasks.get("before_cleanup", 0),
        "active_tasks": active_tasks,
        "active_workers": active_workers,
        "verified": dry_run or ((keep_tasks or active_tasks.get("candidate_tasks", 0) == 0) and active_workers.get("active_workers", 0) == 0),
    }


def cleanup_queue_state(db_path: Path | None, *, reason: str, keep_tasks: bool) -> None:
    now = utc_now()
    active_task_statuses = ("pending", "ready", "running", "paused")
    worker_statuses = ("starting", "idle", "running", "stale", "error")
    with session(db_path) as connection:
        connection.execute(
            f"""
            UPDATE task_attempts
            SET ended_at = COALESCE(ended_at, ?), result = 'terminated', failure_reason = ?
            WHERE ended_at IS NULL OR result = 'running'
            """,
            (now, reason),
        )
        connection.execute(
            f"""
            UPDATE execution_logs
            SET ended_at = COALESCE(ended_at, ?), result = 'terminated', failure_reason = ?
            WHERE ended_at IS NULL OR result = 'running'
            """,
            (now, reason),
        )
        connection.execute(
            f"""
            UPDATE execution_workers
            SET status = 'stopped',
                current_task_id = '',
                current_attempt_id = '',
                lease_expires_at = '',
                last_error = ?,
                updated_at = ?
            WHERE status IN ({placeholders(worker_statuses)})
            """,
            (reason, now, *worker_statuses),
        )
        if not keep_tasks:
            connection.execute(
                f"""
                UPDATE release_tasks
                SET status = 'removed',
                    failure_reason = ?,
                    locked_by_worker = '',
                    lock_expires_at = NULL,
                    updated_at = ?
                WHERE status IN ({placeholders(active_task_statuses)})
                """,
                (reason, now, *active_task_statuses),
            )
        connection.execute(
            "UPDATE phones SET current_status = 'online_idle', updated_at = ? WHERE current_status = 'running'",
            (now,),
        )


def active_worker_processes(db_path: Path | None) -> list[dict[str, object]]:
    with session(db_path) as connection:
        rows = connection.execute(
            """
            SELECT worker_id, phone_id, adb_serial, status, pid, current_task_id, current_attempt_id
            FROM execution_workers
            WHERE status IN ('starting', 'idle', 'running') AND pid > 0
            ORDER BY updated_at DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def queue_activity_counts(db_path: Path | None) -> tuple[dict[str, int], dict[str, int]]:
    with session(db_path) as connection:
        before_cleanup = connection.execute(
            "SELECT COUNT(*) AS value FROM release_tasks WHERE status IN ('pending', 'ready', 'running', 'paused')"
        ).fetchone()["value"]
        candidate_tasks = connection.execute(
            "SELECT COUNT(*) AS value FROM release_tasks WHERE status IN ('pending', 'ready')"
        ).fetchone()["value"]
        active_workers = connection.execute(
            "SELECT COUNT(*) AS value FROM execution_workers WHERE status IN ('starting', 'idle', 'running')"
        ).fetchone()["value"]
    return (
        {"before_cleanup": int(before_cleanup or 0), "candidate_tasks": int(candidate_tasks or 0)},
        {"active_workers": int(active_workers or 0)},
    )


def kill_process(pid: int, kind: str) -> dict[str, object]:
    if pid <= 0:
        return {"kind": kind, "pid": pid, "ok": False, "reason": "invalid_pid"}
    if pid == os.getpid():
        return {"kind": kind, "pid": pid, "ok": False, "reason": "refused_current_process"}
    try:
        if os.name == "nt":
            result = subprocess.run(
                ["taskkill", "/PID", str(pid), "/F"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            return {
                "kind": kind,
                "pid": pid,
                "ok": result.returncode == 0,
                "returncode": result.returncode,
                "stdout": result.stdout.strip()[-300:],
                "stderr": result.stderr.strip()[-300:],
            }
        os.kill(pid, 9)
        return {"kind": kind, "pid": pid, "ok": True}
    except Exception as exc:  # noqa: BLE001
        return {"kind": kind, "pid": pid, "ok": False, "reason": str(exc)}


def write_supervisor_pid() -> None:
    path = supervisor_pid_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(os.getpid()) + "\n", encoding="utf-8")


def read_supervisor_pid() -> int:
    path = supervisor_pid_path()
    if not path.exists():
        return 0
    try:
        return int(path.read_text(encoding="utf-8").strip() or "0")
    except ValueError:
        return 0


def supervisor_pid_path() -> Path:
    return PROJECT_ROOT / "run_log" / "supervisor" / "supervisor.pid"


def placeholders(values: tuple[str, ...]) -> str:
    return ", ".join("?" for _ in values)


def print_candidates(candidates: list[QueueCandidate]) -> None:
    if not candidates:
        print("no candidate tasks")
        return
    print("task_id        ok   status     phone_status  workflow    publish_mode  scheduled_at               reason")
    for candidate in candidates:
        task = candidate.task
        print(
            f"{str(task['task_id']):<15} "
            f"{str(candidate.eligible):<4} "
            f"{str(task['status']):<10} "
            f"{str(task.get('phone_status') or ''):<13} "
            f"{str(task.get('phone_account_type') or 'marketing'):<11} "
            f"{str(task.get('publish_mode') or ''):<13} "
            f"{str(task.get('scheduled_at') or ''):<26} "
            f"{candidate.reason}"
        )


def print_claim(claim: ExecutionClaim) -> None:
    task = claim.task
    print(f"claimed: task={claim.task_id} attempt={claim.attempt_id} log={claim.log_id}")
    print(f"phone: {claim.phone_id} {task.get('phone_name') or ''} {task.get('phone_adb_serial') or ''}")
    print(f"video: {task.get('video_id')} {task.get('video_file_name')}")


def print_outcome(outcome: dict[str, object]) -> None:
    status = str(outcome.get("status") or "")
    print(f"status: {status}")
    claim = outcome.get("claim")
    if isinstance(claim, ExecutionClaim):
        print_claim(claim)
    plan = outcome.get("plan")
    if isinstance(plan, PipelineRunPlan):
        print(f"run_dir: {plan.attempt_dir}")
        print(f"config: {plan.config_path}")
        print(f"state: {plan.state_path}")
        print(f"command: {command_to_text(plan.command)}")
    result = outcome.get("result")
    if isinstance(result, PipelineRunResult):
        print(f"returncode: {result.returncode}")
        print(f"stdout: {result.stdout_path}")
        print(f"stderr: {result.stderr_path}")
    if outcome.get("next_task_status"):
        print(f"next_task_status: {outcome['next_task_status']}")
    if outcome.get("retryable") is not None:
        print(f"retryable: {outcome['retryable']}")
    if outcome.get("phone_health_summary"):
        print(f"phone_health: {outcome['phone_health_summary']}")
    if outcome.get("error"):
        print(f"error: {outcome['error']}")
    if outcome.get("command_text"):
        print(f"command: {outcome['command_text']}")


def print_loop_outcome(outcome: dict[str, object]) -> None:
    print(f"status: {outcome.get('status')}")
    print(f"reason: {outcome.get('reason')}")
    print(f"runs: {outcome.get('runs')}")
    outcomes = outcome.get("outcomes")
    if not isinstance(outcomes, list):
        return
    for index, item in enumerate(outcomes, start=1):
        if not isinstance(item, dict):
            continue
        task = item.get("task_id", "")
        status = item.get("status", "")
        run_dir = item.get("run_dir", "")
        error = item.get("error", "")
        print(f"{index}. status={status} task={task} run_dir={run_dir}")
        if error:
            print(f"   error={error}")


def print_workers_outcome(outcome: dict[str, object]) -> None:
    print(f"status: {outcome.get('status')}")
    print(f"workers: {outcome.get('workers')}")
    readiness = outcome.get("readiness")
    if isinstance(readiness, dict):
        print_readiness(readiness)
    phones = outcome.get("phones")
    if isinstance(phones, list):
        for phone in phones:
            if isinstance(phone, dict):
                print(f"phone: {phone.get('phone_id')} {phone.get('adb_serial')} {phone.get('account_type')}")
    outcomes = outcome.get("outcomes")
    if not isinstance(outcomes, list):
        return
    for index, item in enumerate(outcomes, start=1):
        if not isinstance(item, dict):
            continue
        print(f"{index}. worker={item.get('worker_id')} phone={item.get('phone_id')} status={item.get('status')} reason={item.get('reason')} runs={item.get('runs')}")
        for run in item.get("outcomes", []):
            if isinstance(run, dict):
                print(f"   run status={run.get('status')} task={run.get('task_id', '')} error={run.get('error', '')}")


def print_readiness(readiness: dict[str, object]) -> None:
    print(
        "readiness: "
        f"ok={readiness.get('ok')} "
        f"selected={readiness.get('selected_workers')} "
        f"expected={readiness.get('expected_workers')}"
    )
    issues = readiness.get("issues")
    if isinstance(issues, list):
        for issue in issues:
            if isinstance(issue, dict):
                print(
                    "   issue "
                    f"code={issue.get('code')} "
                    f"phone={issue.get('phone_id', '')} "
                    f"adb={issue.get('adb_serial', '')} "
                    f"message={issue.get('message')}"
                )


def print_worker_states(workers: list[dict[str, object]]) -> None:
    if not workers:
        print("no workers")
        return
    print("worker_id                         status    phone_id        adb_serial       task_id         heartbeat_at")
    for worker in workers:
        print(
            f"{str(worker.get('worker_id') or ''):<33} "
            f"{str(worker.get('status') or ''):<9} "
            f"{str(worker.get('phone_id') or ''):<15} "
            f"{str(worker.get('adb_serial') or ''):<16} "
            f"{str(worker.get('current_task_id') or ''):<15} "
            f"{str(worker.get('heartbeat_at') or '')}"
        )


def print_recovered_workers(workers: list[dict[str, object]]) -> None:
    if not workers:
        print("no stale workers")
        return
    for worker in workers:
        task_ids = worker.get("task_ids") if isinstance(worker.get("task_ids"), list) else []
        print(
            f"recovered worker={worker.get('worker_id')} phone={worker.get('phone_id')} "
            f"adb={worker.get('adb_serial')} tasks={','.join(str(task_id) for task_id in task_ids)}"
        )


def print_stop_all_outcome(outcome: dict[str, object]) -> None:
    print(f"status: {'dry_run' if outcome.get('dry_run') else 'stopped'}")
    print(f"db: {outcome.get('db_path')}")
    if outcome.get("backup_path"):
        print(f"backup: {outcome.get('backup_path')}")
    print(f"supervisor_pid: {outcome.get('supervisor_pid')}")
    print(f"worker_pids: {','.join(str(pid) for pid in outcome.get('worker_pids', []))}")
    for killed in outcome.get("killed", []):
        if isinstance(killed, dict):
            print(f"killed {killed.get('kind')} pid={killed.get('pid')} ok={killed.get('ok')} reason={killed.get('reason', '')}")
    active_tasks = outcome.get("active_tasks")
    active_workers = outcome.get("active_workers")
    if isinstance(active_tasks, dict):
        print(f"candidate_tasks: {active_tasks.get('candidate_tasks')}")
    if isinstance(active_workers, dict):
        print(f"active_workers: {active_workers.get('active_workers')}")
    print(f"verified: {outcome.get('verified')}")


def print_supervisor_outcome(outcome: dict[str, object]) -> None:
    print(f"supervisor_status: {outcome.get('status')}")
    for key in ("stale_workers", "recovered_workers", "dead_workers", "recovered_dead_workers", "overdue_tasks", "paused_tasks", "rescheduled_tasks", "target_phones", "active_workers", "missing_worker_phones", "launched_workers"):
        value = outcome.get(key)
        if isinstance(value, list):
            print(f"{key}: {len(value)}")
    for task in outcome.get("overdue_tasks", []):
        if isinstance(task, dict):
            print(
                "overdue "
                f"task={task.get('task_id')} "
                f"status={task.get('status')} "
                f"account={task.get('phone_account') or task.get('phone_name') or task.get('phone_id')} "
                f"scheduled_at={task.get('scheduled_at')}"
            )
    for task in outcome.get("paused_tasks", []):
        if isinstance(task, dict):
            print(
                "paused "
                f"task={task.get('task_id')} "
                f"scheduled_at={task.get('scheduled_at')} "
                f"reason={task.get('failure_reason')}"
            )
    for task in outcome.get("rescheduled_tasks", []):
        if isinstance(task, dict):
            print(
                "rescheduled "
                f"task={task.get('task_id')} "
                f"scheduled_at={task.get('scheduled_at')} "
                f"reason={task.get('failure_reason')}"
            )
    for phone in outcome.get("missing_worker_phones", []):
        if isinstance(phone, dict):
            print(
                "missing_worker "
                f"phone={phone.get('phone_id')} "
                f"account={phone.get('account_name') or phone.get('device_name')} "
                f"adb={phone.get('adb_serial')}"
            )
    for worker in outcome.get("launched_workers", []):
        if isinstance(worker, dict):
            command = worker.get("command")
            command_text = command_to_text(command) if isinstance(command, list) else ""
            print(
                "launched "
                f"phone={worker.get('phone_id')} "
                f"account={worker.get('account_name')} "
                f"pid={worker.get('pid')} "
                f"stdout={worker.get('stdout')}"
            )
            if command_text:
                print(f"   command={command_text}")


def parse_csv(value: str) -> list[str]:
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


def worker_failed(item: object) -> bool:
    if not isinstance(item, dict):
        return False
    if item.get("status") == "error":
        return True
    outcomes = item.get("outcomes")
    return isinstance(outcomes, list) and any(isinstance(run, dict) and run.get("status") == "failed" for run in outcomes)


if __name__ == "__main__":
    raise SystemExit(main())
