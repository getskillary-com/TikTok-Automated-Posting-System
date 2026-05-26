from __future__ import annotations

import argparse
from pathlib import Path

from .config import load_config, write_example_config
from .runner import doctor, push_only, run_upload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m upload_system",
        description="ADB + vision automation scaffold for TikTok upload flows.",
    )
    parser.add_argument("--config", default="config.json", help="Path to config JSON.")

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("doctor", help="Check ADB, device, and candidate app packages.")

    init_parser = subparsers.add_parser("init-config", help="Write config.example.json or config.json.")
    init_parser.add_argument("--output", default="config.json")

    push_parser = subparsers.add_parser("push", help="Push a video to the phone media folder.")
    push_parser.add_argument("video")

    run_parser = subparsers.add_parser("run", help="Push a video, open the app, and run the AI tap loop.")
    run_parser.add_argument("video")
    run_parser.add_argument(
        "--allow-publish",
        action="store_true",
        help="Allow tapping the final Post/Publish button.",
    )
    dry_group = run_parser.add_mutually_exclusive_group()
    dry_group.add_argument("--dry-run", action="store_true", help="Print taps without executing them.")
    dry_group.add_argument("--no-dry-run", action="store_true", help="Execute taps even if config dry_run is true.")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "init-config":
        output = Path(args.output)
        if output.exists():
            parser.error(f"Refusing to overwrite existing file: {output}")
        write_example_config(output)
        print(f"Wrote {output}")
        return 0

    config = load_config(args.config if Path(args.config).exists() else None)

    if args.command == "doctor":
        return doctor(config)
    if args.command == "push":
        return push_only(config, args.video)
    if args.command == "run":
        dry_run = None
        if args.dry_run:
            dry_run = True
        elif args.no_dry_run:
            dry_run = False
        return run_upload(
            config,
            video=args.video,
            allow_publish=args.allow_publish,
            dry_run=dry_run,
        )

    parser.error(f"Unknown command: {args.command}")
    return 2

