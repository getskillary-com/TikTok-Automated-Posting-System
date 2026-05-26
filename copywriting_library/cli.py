from __future__ import annotations

import argparse
from pathlib import Path

from shared.database import init_database, resolve_db_path

from .caption_repository import CaptionRepository
from .caption_service import CaptionLibraryService, parse_tags_value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage the copywriting library.")
    parser.add_argument("--db", default="", help="SQLite database path. Defaults to storage/group_control_system.db.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init-db", help="Create or update database tables.")

    add_parser = subparsers.add_parser("add", help="Add one caption.")
    add_parser.add_argument("content")
    add_parser.add_argument("--tags", default="")
    add_parser.add_argument("--platform", default="")
    add_parser.add_argument("--account-scope", default="")
    add_parser.add_argument("--note", default="")

    import_parser = subparsers.add_parser("import-file", help="Import captions from txt, json, or csv.")
    import_parser.add_argument("path")
    import_parser.add_argument("--tags", default="")
    import_parser.add_argument("--platform", default="")
    import_parser.add_argument("--account-scope", default="")
    import_parser.add_argument("--note", default="")

    list_parser = subparsers.add_parser("list", help="List captions.")
    list_parser.add_argument("--status", default="")
    list_parser.add_argument("--tag", default="")
    list_parser.add_argument("--platform", default="")
    list_parser.add_argument("--search", default="")
    list_parser.add_argument("--limit", type=int, default=50)

    show_parser = subparsers.add_parser("show", help="Show one caption.")
    show_parser.add_argument("caption_id")

    status_parser = subparsers.add_parser("set-status", help="Change caption status.")
    status_parser.add_argument("caption_id")
    status_parser.add_argument("status")

    edit_parser = subparsers.add_parser("edit", help="Edit caption metadata.")
    edit_parser.add_argument("caption_id")
    edit_parser.add_argument("--content", default=None)
    edit_parser.add_argument("--tags", default=None)
    edit_parser.add_argument("--platform", default=None)
    edit_parser.add_argument("--account-scope", default=None)
    edit_parser.add_argument("--note", default=None)

    bind_parser = subparsers.add_parser("bind-video", help="Bind a caption to a video.")
    bind_parser.add_argument("caption_id")
    bind_parser.add_argument("video_id")
    bind_parser.add_argument("--note", default="")

    bindings_parser = subparsers.add_parser("bindings", help="List caption/video bindings.")
    bindings_parser.add_argument("--caption-id", default="")
    bindings_parser.add_argument("--video-id", default="")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    db_path = Path(args.db) if args.db else None

    if args.command == "init-db":
        path = init_database(db_path)
        print(f"database ready: {path}")
        return 0

    service = CaptionLibraryService(db_path)
    repository = CaptionRepository(db_path)

    if args.command == "add":
        result = service.add_caption(
            args.content,
            tags=parse_tags(args.tags),
            platform=args.platform,
            account_scope=args.account_scope,
            note=args.note,
        )
        print_caption_result(result.caption_id, result.status)
        return 0

    if args.command == "import-file":
        results = service.import_file(
            args.path,
            tags=parse_tags(args.tags),
            platform=args.platform,
            account_scope=args.account_scope,
            note=args.note,
        )
        imported = sum(1 for result in results if not result.duplicate)
        duplicates = sum(1 for result in results if result.duplicate)
        print(f"import complete: imported={imported} duplicates={duplicates} db={resolve_db_path(db_path)}")
        for result in results:
            print_caption_result(result.caption_id, result.status)
        return 0

    if args.command == "list":
        captions = repository.list_captions(
            status=args.status,
            tag=args.tag,
            platform=args.platform,
            search=args.search,
            limit=args.limit,
        )
        print_caption_table(captions)
        return 0

    if args.command == "show":
        caption = repository.get(args.caption_id)
        if not caption:
            parser.error(f"Caption not found: {args.caption_id}")
        for key, value in caption.items():
            print(f"{key}: {value}")
        return 0

    if args.command == "set-status":
        repository.update_status(args.caption_id, args.status)
        print(f"updated {args.caption_id} -> {args.status}")
        return 0

    if args.command == "edit":
        repository.update_caption(
            args.caption_id,
            content=args.content,
            tags=parse_tags(args.tags) if args.tags is not None else None,
            platform=args.platform,
            account_scope=args.account_scope,
            note=args.note,
        )
        print(f"updated {args.caption_id}")
        return 0

    if args.command == "bind-video":
        binding_id = repository.bind_video(args.caption_id, args.video_id, note=args.note)
        print(f"bound: {binding_id} {args.caption_id} -> {args.video_id}")
        return 0

    if args.command == "bindings":
        bindings = repository.list_video_bindings(caption_id=args.caption_id, video_id=args.video_id)
        print_binding_table(bindings)
        return 0

    parser.error(f"Unknown command: {args.command}")
    return 2


def parse_tags(value: str | None) -> list[str]:
    if value is None:
        return []
    return parse_tags_value(value)


def print_caption_result(caption_id: str, status: str) -> None:
    print(f"{status}: {caption_id}")


def print_caption_table(captions: list[dict[str, object]]) -> None:
    if not captions:
        print("no captions")
        return
    print("caption_id      status      used  preview")
    for caption in captions:
        preview = str(caption.get("content") or "").replace("\n", " ")[:60]
        used = int(caption.get("used_count") or 0)
        print(f"{str(caption['caption_id']):<15} {str(caption['status']):<11} {used:<5} {preview}")


def print_binding_table(bindings: list[dict[str, object]]) -> None:
    if not bindings:
        print("no bindings")
        return
    print("binding_id      caption_id      video_id        video")
    for binding in bindings:
        print(
            f"{str(binding['binding_id']):<15} "
            f"{str(binding['caption_id']):<15} "
            f"{str(binding['video_id']):<15} "
            f"{binding['file_name']}"
        )


if __name__ == "__main__":
    raise SystemExit(main())
