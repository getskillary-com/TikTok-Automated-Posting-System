from __future__ import annotations

import argparse
from pathlib import Path

from shared.database import init_database, resolve_db_path

from .video_repository import VideoRepository
from .video_service import VideoLibraryService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage the local video library.")
    parser.add_argument("--db", default="", help="SQLite database path. Defaults to storage/group_control_system.db.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init-db", help="Create or update database tables.")

    import_parser = subparsers.add_parser("import-file", help="Import one video file.")
    import_parser.add_argument("path")
    import_parser.add_argument("--title", default="")
    import_parser.add_argument("--tags", default="", help="Comma-separated tags.")
    import_parser.add_argument("--batch", default="")
    import_parser.add_argument("--note", default="")

    scan_parser = subparsers.add_parser("scan-folder", help="Import all videos from a folder.")
    scan_parser.add_argument("folder")
    scan_parser.add_argument("--no-recursive", action="store_true")
    scan_parser.add_argument("--tags", default="", help="Comma-separated tags.")
    scan_parser.add_argument("--batch", default="")
    scan_parser.add_argument("--note", default="")

    list_parser = subparsers.add_parser("list", help="List videos.")
    list_parser.add_argument("--status", default="")
    list_parser.add_argument("--tag", default="")
    list_parser.add_argument("--batch", default="")
    list_parser.add_argument("--search", default="")
    list_parser.add_argument("--limit", type=int, default=50)

    show_parser = subparsers.add_parser("show", help="Show one video.")
    show_parser.add_argument("video_id")

    status_parser = subparsers.add_parser("set-status", help="Change a video status.")
    status_parser.add_argument("video_id")
    status_parser.add_argument("status")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    db_path = Path(args.db) if args.db else None

    if args.command == "init-db":
        path = init_database(db_path)
        print(f"database ready: {path}")
        return 0

    service = VideoLibraryService(db_path)
    repository = VideoRepository(db_path)

    if args.command == "import-file":
        result = service.import_file(
            args.path,
            title=args.title,
            tags=parse_tags(args.tags),
            batch_name=args.batch,
            note=args.note,
        )
        print_import_result(result.video_id, result.status, result.file_path)
        return 0

    if args.command == "scan-folder":
        results = service.scan_folder(
            args.folder,
            recursive=not args.no_recursive,
            tags=parse_tags(args.tags),
            batch_name=args.batch,
            note=args.note,
        )
        imported = sum(1 for result in results if not result.duplicate)
        duplicates = sum(1 for result in results if result.duplicate)
        print(f"scan complete: imported={imported} duplicates={duplicates} db={resolve_db_path(db_path)}")
        for result in results:
            print_import_result(result.video_id, result.status, result.file_path)
        return 0

    if args.command == "list":
        videos = repository.list_videos(
            status=args.status,
            tag=args.tag,
            batch_name=args.batch,
            search=args.search,
            limit=args.limit,
        )
        print_video_table(videos)
        return 0

    if args.command == "show":
        video = repository.get(args.video_id)
        if not video:
            parser.error(f"Video not found: {args.video_id}")
        for key, value in video.items():
            print(f"{key}: {value}")
        return 0

    if args.command == "set-status":
        repository.update_status(args.video_id, args.status)
        print(f"updated {args.video_id} -> {args.status}")
        return 0

    parser.error(f"Unknown command: {args.command}")
    return 2


def parse_tags(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def print_import_result(video_id: str, status: str, path: str) -> None:
    print(f"{status}: {video_id} {path}")


def print_video_table(videos: list[dict[str, object]]) -> None:
    if not videos:
        print("no videos")
        return
    print("video_id        status      size       used  file_name")
    for video in videos:
        size = int(video.get("file_size") or 0)
        used = int(video.get("used_count") or 0)
        print(
            f"{str(video['video_id']):<15} "
            f"{str(video['status']):<11} "
            f"{size:<10} "
            f"{used:<5} "
            f"{video['file_name']}"
        )


if __name__ == "__main__":
    raise SystemExit(main())
