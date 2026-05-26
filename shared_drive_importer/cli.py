from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from shared.database import init_database

from .importer import DEFAULT_SHARE_ROOT, SharedDriveImporter, result_to_json


DEFAULT_TASK_NAME = "GroupControlSharedDriveImporter"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
LAUNCHER_PATH = PROJECT_ROOT / "shared_drive_importer_launcher.py"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import shared-drive Excel batches into the group control queue.")
    parser.add_argument("--db", default="", help="SQLite database path. Defaults to storage/group_control_system.db.")
    parser.add_argument("--share-root", default=str(DEFAULT_SHARE_ROOT), help="Shared drive root folder.")
    parser.add_argument("--timezone", default="America/Sao_Paulo", help="Timezone for naive scheduled_at values.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("create-template", help="Create shared-drive folders and Excel templates.")

    scan_parser = subparsers.add_parser("scan-once", help="Scan inbox batches once.")
    scan_parser.add_argument("--export-status", action="store_true", help="Export status.xlsx after scanning.")

    export_parser = subparsers.add_parser("export-status", help="Export status.xlsx from database state.")
    export_parser.add_argument("--batch-id", default="")

    subparsers.add_parser("doctor", help="Check layout, dependency, and database readiness.")

    install_parser = subparsers.add_parser("install-scheduled-task", help="Print or register the Windows scheduled task command.")
    install_parser.add_argument("--task-name", default=DEFAULT_TASK_NAME)
    install_parser.add_argument("--register", action="store_true", help="Actually call schtasks.exe /Create. Requires system-level approval.")
    install_parser.add_argument("--python", default=sys.executable)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    db_path = Path(args.db) if args.db else None
    share_root = Path(args.share_root)
    importer = SharedDriveImporter(share_root=share_root, db_path=db_path, timezone=args.timezone)

    if args.command == "create-template":
        paths = importer.create_templates()
        print(result_to_json(paths))
        return 0

    if args.command == "scan-once":
        result = importer.scan_once(export_status=args.export_status)
        print(result_to_json(result))
        return 0

    if args.command == "export-status":
        paths = importer.export_status(batch_id=args.batch_id)
        print(result_to_json({"exported": [str(path) for path in paths]}))
        return 0

    if args.command == "doctor":
        init_database(db_path)
        print(result_to_json(importer.doctor()))
        return 0

    if args.command == "install-scheduled-task":
        action_command = scheduled_task_action(
            python_path=args.python,
            share_root=share_root,
            db_path=db_path,
            timezone=args.timezone,
        )
        schtasks_command = [
            "schtasks.exe",
            "/Create",
            "/TN",
            args.task_name,
            "/SC",
            "MINUTE",
            "/MO",
            "1",
            "/TR",
            action_command,
            "/F",
        ]
        print("action:")
        print(action_command)
        print("schtasks:")
        print(subprocess.list2cmdline(schtasks_command))
        if args.register:
            result = subprocess.run(schtasks_command, check=False, text=True, capture_output=True)
            if result.stdout:
                print(result.stdout.strip())
            if result.stderr:
                print(result.stderr.strip(), file=sys.stderr)
            return result.returncode
        return 0

    parser.error(f"Unknown command: {args.command}")
    return 2


def scheduled_task_action(*, python_path: str, share_root: Path, db_path: Path | None, timezone: str) -> str:
    command = [
        python_path,
        str(LAUNCHER_PATH),
        "--share-root",
        str(share_root.resolve()),
        "--timezone",
        timezone,
    ]
    if db_path is not None:
        command.extend(["--db", str(db_path.resolve())])
    command.extend(["scan-once", "--export-status"])
    return subprocess.list2cmdline(command)
