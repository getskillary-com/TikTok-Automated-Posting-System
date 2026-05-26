from __future__ import annotations

import argparse
from pathlib import Path

from shared.database import init_database

from .phone_repository import PhoneRepository
from .phone_service import PhoneLibraryService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage phones and bundled ADB for the group control system.")
    parser.add_argument("--db", default="", help="SQLite database path. Defaults to storage/group_control_system.db.")
    parser.add_argument("--adb", default="", help="Custom adb.exe path. Defaults to bundled platform-tools/adb.exe.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init-db", help="Create or update database tables.")
    subparsers.add_parser("adb-status", help="Inspect bundled ADB and connected devices.")

    devices_parser = subparsers.add_parser("devices", help="List ADB devices.")
    devices_parser.add_argument("--register", action="store_true", help="Register detected devices into phone library.")
    devices_parser.add_argument("--account", default="")
    devices_parser.add_argument("--account-type", choices=["marketing", "showcase"], default="")
    devices_parser.add_argument("--app-package", default="")
    devices_parser.add_argument("--remote-video-dir", default="/sdcard/DCIM/Camera")

    sync_parser = subparsers.add_parser("safe-sync", help="Safely sync ADB devices without changing running phones.")
    sync_parser.add_argument("--no-register-new", action="store_true", help="Only update already registered serials.")
    sync_parser.add_argument("--mark-missing", action="store_true", help="Mark missing non-running USB phones offline.")
    sync_parser.add_argument("--account", default="")
    sync_parser.add_argument("--account-type", choices=["marketing", "showcase"], default="")
    sync_parser.add_argument("--app-package", default="")
    sync_parser.add_argument("--remote-video-dir", default="/sdcard/DCIM/Camera")

    add_parser = subparsers.add_parser("add", help="Add or update one phone manually.")
    add_parser.add_argument("--name", default="", help="Display name. Defaults to the detected device name.")
    add_parser.add_argument("--serial", required=True)
    add_parser.add_argument("--account", default="")
    add_parser.add_argument("--account-type", choices=["marketing", "showcase"], default="marketing")
    add_parser.add_argument("--connection-mode", choices=["usb", "wireless"], default="usb")
    add_parser.add_argument("--app-package", default="")
    add_parser.add_argument("--remote-video-dir", default="/sdcard/DCIM/Camera")
    add_parser.add_argument("--note", default="")

    pair_parser = subparsers.add_parser("pair-wireless", help="Pair and connect a wireless debugging phone.")
    pair_parser.add_argument("--host", required=True)
    pair_parser.add_argument("--pair-port", required=True)
    pair_parser.add_argument("--pair-code", required=True)
    pair_parser.add_argument("--connect-port", required=True)
    pair_parser.add_argument("--name", default="")
    pair_parser.add_argument("--account", default="")
    pair_parser.add_argument("--account-type", choices=["marketing", "showcase"], default="marketing")
    pair_parser.add_argument("--app-package", default="")
    pair_parser.add_argument("--remote-video-dir", default="/sdcard/DCIM/Camera")

    connect_parser = subparsers.add_parser("connect-wireless", help="Connect an already paired wireless debugging phone.")
    connect_parser.add_argument("--host", required=True)
    connect_parser.add_argument("--connect-port", required=True)

    list_parser = subparsers.add_parser("list", help="List phones.")
    list_parser.add_argument("--status", default="")
    list_parser.add_argument("--pairing-status", default="")
    list_parser.add_argument("--connection-mode", default="")
    list_parser.add_argument("--search", default="")
    list_parser.add_argument("--limit", type=int, default=50)

    show_parser = subparsers.add_parser("show", help="Show one phone.")
    show_parser.add_argument("phone_id")

    edit_parser = subparsers.add_parser("edit", help="Edit phone metadata.")
    edit_parser.add_argument("phone_id")
    edit_parser.add_argument("--name", default=None)
    edit_parser.add_argument("--account", default=None)
    edit_parser.add_argument("--account-type", choices=["marketing", "showcase"], default=None)
    edit_parser.add_argument("--app-package", default=None)
    edit_parser.add_argument("--remote-video-dir", default=None)
    edit_parser.add_argument("--note", default=None)

    status_parser = subparsers.add_parser("set-status", help="Change phone status.")
    status_parser.add_argument("phone_id")
    status_parser.add_argument("status")

    health_parser = subparsers.add_parser("health", help="Run health check for one phone.")
    health_parser.add_argument("phone_id")

    subparsers.add_parser("health-all", help="Run health checks for all enabled phones.")

    checks_parser = subparsers.add_parser("checks", help="List health check history.")
    checks_parser.add_argument("phone_id")
    checks_parser.add_argument("--limit", type=int, default=20)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    db_path = Path(args.db) if args.db else None
    adb_path = Path(args.adb) if args.adb else None

    if args.command == "init-db":
        path = init_database(db_path)
        print(f"database ready: {path}")
        return 0

    service = PhoneLibraryService(db_path, adb_path)
    repository = PhoneRepository(db_path)

    if args.command == "adb-status":
        inspection = service.inspect_adb()
        print_adb_status(inspection)
        return 0

    if args.command == "devices":
        rows = service.refresh_devices(
            register=args.register,
            account_name=args.account,
            account_type=args.account_type,
            app_package=args.app_package,
            remote_video_dir=args.remote_video_dir,
        )
        print_device_table(rows)
        return 0

    if args.command == "safe-sync":
        result = service.safe_sync_devices(
            register_new=not args.no_register_new,
            mark_missing=args.mark_missing,
            account_name=args.account,
            account_type=args.account_type,
            app_package=args.app_package,
            remote_video_dir=args.remote_video_dir,
        )
        print_safe_sync_result(result)
        return 0

    if args.command == "add":
        phone_id = service.add_phone(
            device_name=args.name,
            adb_serial=args.serial,
            account_name=args.account,
            account_type=args.account_type,
            connection_mode=args.connection_mode,
            app_package=args.app_package,
            remote_video_dir=args.remote_video_dir,
            note=args.note,
        )
        print(f"saved: {phone_id}")
        return 0

    if args.command == "pair-wireless":
        result = service.pair_wireless(
            host=args.host,
            pair_port=args.pair_port,
            pair_code=args.pair_code,
            connect_port=args.connect_port,
            device_name=args.name,
            account_name=args.account,
            account_type=args.account_type,
            app_package=args.app_package,
            remote_video_dir=args.remote_video_dir,
        )
        print(f"paired: {result['phone_id']} {result['serial']}")
        print(result["pair_output"])
        print(result["connect_output"])
        return 0

    if args.command == "connect-wireless":
        result = service.connect_wireless(host=args.host, connect_port=args.connect_port)
        print(f"connected: {result['phone_id']} {result['serial']}")
        print(result["output"])
        return 0

    if args.command == "list":
        phones = repository.list_phones(
            status=args.status,
            pairing_status=args.pairing_status,
            connection_mode=args.connection_mode,
            search=args.search,
            limit=args.limit,
        )
        print_phone_table(phones)
        return 0

    if args.command == "show":
        phone = repository.get(args.phone_id)
        if not phone:
            parser.error(f"Phone not found: {args.phone_id}")
        for key, value in phone.items():
            print(f"{key}: {value}")
        return 0

    if args.command == "edit":
        repository.update_phone(
            args.phone_id,
            device_name=args.name,
            account_name=args.account,
            account_type=args.account_type,
            app_package=args.app_package,
            remote_video_dir=args.remote_video_dir,
            note=args.note,
        )
        print(f"updated {args.phone_id}")
        return 0

    if args.command == "set-status":
        repository.update_status(args.phone_id, args.status)
        print(f"updated {args.phone_id} -> {args.status}")
        return 0

    if args.command == "health":
        phone, result, check_id = service.health_check(args.phone_id)
        print_health_result(str(phone["phone_id"]), result, check_id)
        return 0

    if args.command == "health-all":
        reports = service.health_check_all()
        if not reports:
            print("no phones")
            return 0
        for report in reports:
            phone = report["phone"]
            result = report["result"]
            print_health_result(str(phone["phone_id"]), result, str(report["check_id"]))
        return 0

    if args.command == "checks":
        checks = repository.list_health_checks(args.phone_id, limit=args.limit)
        print_check_table(checks)
        return 0

    parser.error(f"Unknown command: {args.command}")
    return 2


def print_adb_status(inspection: object) -> None:
    print(f"source: {inspection.source}")
    print(f"adb_path: {inspection.adb_path}")
    print(f"platform_tools_status: {inspection.platform_tools_status}")
    print(f"service_status: {inspection.service_status}")
    print(f"server_port: {getattr(inspection, 'server_port', '')}")
    print(f"version: {first_line(inspection.version)}")
    print(f"required_files: {inspection.required_files}")
    if inspection.error:
        print(f"error: {inspection.error}")
    print_device_table([
        {"phone_id": "", "serial": device.serial, "state": device.state, "detail": device.detail, "device_name": ""}
        for device in inspection.devices
    ])


def print_device_table(rows: list[dict[str, object]]) -> None:
    if not rows:
        print("no adb devices")
        return
    print("phone_id        device_name                  serial                    state          detail")
    for row in rows:
        print(
            f"{str(row.get('phone_id') or ''):<15} "
            f"{str(row.get('device_name') or '')[:28]:<28} "
            f"{str(row.get('serial') or ''):<25} "
            f"{str(row.get('state') or ''):<14} "
            f"{str(row.get('detail') or '')[:80]}"
        )


def print_safe_sync_result(result: dict[str, object]) -> None:
    print(
        "detected={detected} online={online} created={created} "
        "protected_running={protected_running} skipped_removed={skipped_removed} marked_offline={marked_offline}".format(**result)
    )
    rows = result.get("devices") or []
    if not isinstance(rows, list) or not rows:
        return
    print("phone_id        device_name                  serial                    state          action              status")
    for row in rows:
        if not isinstance(row, dict):
            continue
        print(
            f"{str(row.get('phone_id') or ''):<15} "
            f"{str(row.get('device_name') or '')[:28]:<28} "
            f"{str(row.get('serial') or ''):<25} "
            f"{str(row.get('state') or ''):<14} "
            f"{str(row.get('action') or ''):<19} "
            f"{str(row.get('status') or '')}"
        )


def print_phone_table(phones: list[dict[str, object]]) -> None:
    if not phones:
        print("no phones")
        return
    print("phone_id        device_name                  status       account_type  auth          pair           mode      serial")
    for phone in phones:
        print(
            f"{str(phone['phone_id']):<15} "
            f"{str(phone.get('device_name') or '')[:28]:<28} "
            f"{str(phone['current_status']):<12} "
            f"{str(phone.get('account_type') or 'marketing'):<13} "
            f"{str(phone['authorization_status']):<13} "
            f"{str(phone['pairing_status']):<14} "
            f"{str(phone['connection_mode']):<9} "
            f"{phone['adb_serial']}"
        )


def print_health_result(phone_id: str, result: object, check_id: str) -> None:
    print(
        f"{check_id} {phone_id} serial={result.serial} "
        f"adb_online={result.adb_online} authorized={result.authorized} "
        f"screenshot={result.screenshot_ok} app={result.app_detected} "
        f"media_dir={result.media_dir_writable} foreground={result.current_foreground_package}"
    )
    if result.details:
        print(f"details: {result.details}")


def print_check_table(checks: list[dict[str, object]]) -> None:
    if not checks:
        print("no checks")
        return
    print("check_id        checked_at                 online  auth  shot  app  media  foreground")
    for check in checks:
        print(
            f"{str(check['check_id']):<15} "
            f"{str(check['checked_at']):<26} "
            f"{int(check['adb_online']):<7} "
            f"{int(check['authorized']):<5} "
            f"{int(check['screenshot_ok']):<5} "
            f"{int(check['app_detected']):<4} "
            f"{int(check['media_dir_writable']):<6} "
            f"{check['current_foreground_package']}"
        )


def first_line(value: str) -> str:
    return str(value).splitlines()[0] if value else ""


if __name__ == "__main__":
    raise SystemExit(main())
