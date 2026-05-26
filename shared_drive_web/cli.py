from __future__ import annotations

import argparse
from pathlib import Path

from shared_drive_importer.importer import DEFAULT_SHARE_ROOT

from .server import serve


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Serve a LAN web upload portal for shared-drive batches.")
    parser.add_argument("--db", default="", help="SQLite database path. Defaults to storage/group_control_system.db.")
    parser.add_argument("--share-root", default=str(DEFAULT_SHARE_ROOT), help="Shared drive root folder.")
    parser.add_argument("--timezone", default="America/Sao_Paulo", help="Timezone for naive scheduled_at values.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host. Use 0.0.0.0 for LAN access.")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--token", default="", help="Optional upload/status access token.")
    parser.add_argument("--max-request-mb", type=int, default=2048)
    parser.add_argument("--disable-phone-sync", action="store_true", help="Disable periodic safe ADB phone synchronization.")
    parser.add_argument("--phone-sync-seconds", type=int, default=60, help="Lightweight phone sync interval.")
    parser.add_argument("--phone-full-sync-seconds", type=int, default=300, help="Full phone registration sync interval.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    db_path = Path(args.db) if args.db else None
    serve(
        host=args.host,
        port=args.port,
        share_root=Path(args.share_root),
        db_path=db_path,
        timezone=args.timezone,
        access_token=args.token,
        max_request_mb=args.max_request_mb,
        phone_sync_enabled=not args.disable_phone_sync,
        phone_sync_seconds=args.phone_sync_seconds,
        phone_full_sync_seconds=args.phone_full_sync_seconds,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
