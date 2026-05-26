from __future__ import annotations

import argparse
import json
from pathlib import Path

from .api_server import serve
from .routes import public_routes
from .serializers import to_jsonable
from .service import ControlCenterService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Control center API for the group control system.")
    parser.add_argument("--db", default="", help="SQLite database path. Defaults to storage/group_control_system.db.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    health_parser = subparsers.add_parser("health", help="Show service health and module counts.")
    health_parser.add_argument("--json", action="store_true")

    routes_parser = subparsers.add_parser("routes", help="List local HTTP API routes.")
    routes_parser.add_argument("--json", action="store_true")

    serve_parser = subparsers.add_parser("serve", help="Start the local HTTP API server.")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8766)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    db_path = Path(args.db) if args.db else None

    if args.command == "health":
        data = ControlCenterService(db_path).health()
        print_data(data, as_json=args.json)
        return 0

    if args.command == "routes":
        data = public_routes()
        print_data(data, as_json=args.json)
        return 0

    if args.command == "serve":
        serve(args.host, args.port, db_path=db_path)
        return 0

    parser.error(f"Unknown command: {args.command}")
    return 2


def print_data(data: object, *, as_json: bool) -> None:
    clean = to_jsonable(data)
    if as_json:
        print(json.dumps(clean, ensure_ascii=False, indent=2))
        return
    if isinstance(clean, dict):
        for key, value in clean.items():
            print(f"{key}: {value}")
        return
    if isinstance(clean, list):
        for item in clean:
            if isinstance(item, dict):
                print("  ".join(f"{key}={value}" for key, value in item.items()))
            else:
                print(item)
        return
    print(clean)


if __name__ == "__main__":
    raise SystemExit(main())
