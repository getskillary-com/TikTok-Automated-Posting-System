from __future__ import annotations

import argparse
from pathlib import Path

from shared.database import init_database

from .task_repository import ReleaseTaskRepository
from .task_service import ReleaseTaskService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage standardized release tasks.")
    parser.add_argument("--db", default="", help="SQLite database path. Defaults to storage/group_control_system.db.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init-db", help="Create or update database tables.")

    create_parser = subparsers.add_parser("create", help="Create one release task.")
    create_parser.add_argument("--video-id", required=True)
    create_parser.add_argument("--caption-id", default="")
    create_parser.add_argument("--phone-id", required=True)
    create_parser.add_argument("--scheduled-at", required=True)
    create_parser.add_argument("--publish-mode", choices=["scheduled", "immediate", "timed"], default="scheduled")
    create_parser.add_argument("--product-link", default="")
    create_parser.add_argument("--product-name", default="")
    create_parser.add_argument("--max-retries", type=int, default=3)
    create_parser.add_argument("--note", default="")
    create_parser.add_argument("--allow-reuse", action="store_true")
    create_parser.add_argument("--timezone", default="Asia/Shanghai")

    binding_parser = subparsers.add_parser("create-from-binding", help="Create a task from a caption/video binding.")
    binding_parser.add_argument("--binding-id", required=True)
    binding_parser.add_argument("--phone-id", required=True)
    binding_parser.add_argument("--scheduled-at", required=True)
    binding_parser.add_argument("--publish-mode", choices=["scheduled", "immediate", "timed"], default="scheduled")
    binding_parser.add_argument("--product-link", default="")
    binding_parser.add_argument("--product-name", default="")
    binding_parser.add_argument("--max-retries", type=int, default=3)
    binding_parser.add_argument("--note", default="")
    binding_parser.add_argument("--allow-reuse", action="store_true")
    binding_parser.add_argument("--timezone", default="Asia/Shanghai")

    import_parser = subparsers.add_parser("import-csv", help="Import release tasks from CSV.")
    import_parser.add_argument("path")
    import_parser.add_argument("--allow-reuse", action="store_true")
    import_parser.add_argument("--timezone", default="Asia/Shanghai")

    list_parser = subparsers.add_parser("list", help="List release tasks.")
    list_parser.add_argument("--status", default="")
    list_parser.add_argument("--phone-id", default="")
    list_parser.add_argument("--video-id", default="")
    list_parser.add_argument("--date-from", default="")
    list_parser.add_argument("--date-to", default="")
    list_parser.add_argument("--search", default="")
    list_parser.add_argument("--limit", type=int, default=50)

    next_parser = subparsers.add_parser("next", help="List due tasks for execution queue.")
    next_parser.add_argument("--phone-id", default="")
    next_parser.add_argument("--limit", type=int, default=10)
    next_parser.add_argument("--timezone", default="Asia/Shanghai")
    next_parser.add_argument("--preparation-window-minutes", type=int, default=60)
    next_parser.add_argument("--ignore-phone-status", action="store_true")
    next_parser.add_argument("--allow-overdue", action="store_true")

    show_parser = subparsers.add_parser("show", help="Show one release task.")
    show_parser.add_argument("task_id")

    status_parser = subparsers.add_parser("set-status", help="Change task status.")
    status_parser.add_argument("task_id")
    status_parser.add_argument("status")
    status_parser.add_argument("--failure-reason", default="")
    status_parser.add_argument("--run-dir", default="")

    reschedule_parser = subparsers.add_parser("reschedule", help="Change scheduled publish time.")
    reschedule_parser.add_argument("task_id")
    reschedule_parser.add_argument("scheduled_at")
    reschedule_parser.add_argument("--timezone", default="Asia/Shanghai")

    assign_parser = subparsers.add_parser("assign-phone", help="Assign task to another phone.")
    assign_parser.add_argument("task_id")
    assign_parser.add_argument("phone_id")

    cancel_parser = subparsers.add_parser("cancel", help="Cancel one task.")
    cancel_parser.add_argument("task_id")
    cancel_parser.add_argument("--reason", default="")

    requeue_parser = subparsers.add_parser("requeue", help="Move a failed/cancelled/paused task back to pending.")
    requeue_parser.add_argument("task_id")
    requeue_parser.add_argument("--reset-retry-count", action="store_true")
    requeue_parser.add_argument("--force-running", action="store_true", help="Also recover a task currently marked running.")
    requeue_parser.add_argument("--reason", default="")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    db_path = Path(args.db) if args.db else None

    if args.command == "init-db":
        path = init_database(db_path)
        print(f"database ready: {path}")
        return 0

    service = ReleaseTaskService(db_path)
    repository = ReleaseTaskRepository(db_path)

    if args.command == "create":
        result = service.create_task(
            video_id=args.video_id,
            caption_id=args.caption_id,
            phone_id=args.phone_id,
            scheduled_at=args.scheduled_at,
            publish_mode=args.publish_mode,
            product_link=args.product_link,
            product_name=args.product_name,
            max_retries=args.max_retries,
            note=args.note,
            allow_reuse=args.allow_reuse,
            timezone=args.timezone,
        )
        print_task_result(result.task_id, result.status, result.duplicate)
        return 0

    if args.command == "create-from-binding":
        result = service.create_from_binding(
            binding_id=args.binding_id,
            phone_id=args.phone_id,
            scheduled_at=args.scheduled_at,
            publish_mode=args.publish_mode,
            product_link=args.product_link,
            product_name=args.product_name,
            max_retries=args.max_retries,
            note=args.note,
            allow_reuse=args.allow_reuse,
            timezone=args.timezone,
        )
        print_task_result(result.task_id, result.status, result.duplicate)
        return 0

    if args.command == "import-csv":
        results = service.import_csv(args.path, allow_reuse=args.allow_reuse, timezone=args.timezone)
        created = sum(1 for result in results if not result.duplicate)
        duplicates = sum(1 for result in results if result.duplicate)
        print(f"import complete: created={created} duplicates={duplicates}")
        for result in results:
            print_task_result(result.task_id, result.status, result.duplicate)
        return 0

    if args.command == "list":
        tasks = repository.list_tasks(
            status=args.status,
            phone_id=args.phone_id,
            video_id=args.video_id,
            date_from=args.date_from,
            date_to=args.date_to,
            search=args.search,
            limit=args.limit,
        )
        print_task_table(tasks)
        return 0

    if args.command == "next":
        tasks = repository.next_due(
            phone_id=args.phone_id,
            limit=args.limit,
            timezone=args.timezone,
            preparation_window_minutes=args.preparation_window_minutes,
            require_phone_ready=not args.ignore_phone_status,
            allow_overdue=args.allow_overdue,
        )
        print_task_table(tasks)
        return 0

    if args.command == "show":
        task = repository.get(args.task_id)
        if not task:
            parser.error(f"Task not found: {args.task_id}")
        for key, value in task.items():
            print(f"{key}: {value}")
        return 0

    if args.command == "set-status":
        repository.update_status(
            args.task_id,
            args.status,
            failure_reason=args.failure_reason,
            run_dir=args.run_dir,
        )
        print(f"updated {args.task_id} -> {args.status}")
        return 0

    if args.command == "reschedule":
        repository.reschedule(args.task_id, args.scheduled_at, timezone=args.timezone)
        print(f"rescheduled {args.task_id} -> {args.scheduled_at}")
        return 0

    if args.command == "assign-phone":
        repository.assign_phone(args.task_id, args.phone_id)
        print(f"assigned {args.task_id} -> {args.phone_id}")
        return 0

    if args.command == "cancel":
        repository.update_status(args.task_id, "cancelled", failure_reason=args.reason)
        print(f"cancelled {args.task_id}")
        return 0

    if args.command == "requeue":
        result = repository.requeue(
            args.task_id,
            reset_retry_count=args.reset_retry_count,
            force_running=args.force_running,
            reason=args.reason,
        )
        print(
            f"requeued {result['task_id']}: "
            f"{result['old_status']} -> {result['status']} retry_count={result['retry_count']}"
        )
        return 0

    parser.error(f"Unknown command: {args.command}")
    return 2


def print_task_result(task_id: str, status: str, duplicate: bool) -> None:
    prefix = "duplicate" if duplicate else "created"
    print(f"{prefix}: {task_id} status={status}")


def print_task_table(tasks: list[dict[str, object]]) -> None:
    if not tasks:
        print("no tasks")
        return
    print("task_id        status      workflow   scheduled_at               phone            video")
    for task in tasks:
        phone = str(task.get("phone_name") or task.get("phone_id") or "")[:16]
        video = str(task.get("video_file_name") or task.get("video_id") or "")[:36]
        workflow = str(task.get("phone_account_type") or "marketing")[:10]
        print(
            f"{str(task['task_id']):<15} "
            f"{str(task['status']):<11} "
            f"{workflow:<10} "
            f"{str(task['scheduled_at']):<26} "
            f"{phone:<16} "
            f"{video}"
        )


if __name__ == "__main__":
    raise SystemExit(main())

