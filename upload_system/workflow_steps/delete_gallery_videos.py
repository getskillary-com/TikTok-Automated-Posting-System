from __future__ import annotations

import argparse
import fnmatch
import re
from pathlib import PurePosixPath
from typing import Any

from common import (
    add_step_args,
    load_or_create_state,
    load_step_config,
    make_adb,
    mark_step_done,
    write_trace,
)


STEP_NAME = "delete_gallery_videos"

DEFAULT_MEDIA_URIS = [
    "content://media/external_primary/video/media",
    "content://media/external/video/media",
]

DEFAULT_VIDEO_EXTENSIONS = [
    ".mp4",
    ".mov",
    ".m4v",
    ".webm",
    ".3gp",
    ".3gpp",
    ".mkv",
    ".avi",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Delete videos from the Android gallery after upload cleanup.")
    add_step_args(parser)
    args = parser.parse_args()

    config = load_step_config(args.config)
    state = load_or_create_state(args, config)
    adb = make_adb(config)
    cleanup_config = config["pipeline"].get("gallery_cleanup", {})
    dry_run = bool(state.get("dry_run", False))

    if not cleanup_config.get("enabled", False):
        decision = {"action": STEP_NAME, "status": "skipped", "reason": "gallery cleanup disabled"}
        write_trace(state, STEP_NAME, decision)
        mark_step_done(state, STEP_NAME, last_decision=decision)
        print(f"{STEP_NAME}: skipped; gallery cleanup disabled")
        return 0

    required_step = str(cleanup_config.get("require_completed_step", "minimize_and_cleanup") or "")
    if required_step and required_step not in state.get("completed_steps", []):
        raise RuntimeError(
            f"{STEP_NAME}: refusing to delete gallery videos before {required_step!r} completes."
        )

    scope = str(cleanup_config.get("scope", "remote_video_dir"))
    if scope == "all_gallery_videos" and not cleanup_config.get("confirm_delete_all_gallery_videos", False):
        raise RuntimeError(
            f"{STEP_NAME}: scope is all_gallery_videos, but confirm_delete_all_gallery_videos is not true."
        )

    remote_video_dir = str(config["adb"].get("remote_video_dir", "/sdcard/DCIM/Camera"))
    current_uploaded_video = str(state.get("remote_video_path") or "").strip()
    if scope == "current_uploaded_video" and not current_uploaded_video:
        decision = {
            "action": STEP_NAME,
            "status": "skipped",
            "reason": "state.remote_video_path is empty",
            "scope": scope,
            "remote_video_dir": remote_video_dir,
            "dry_run": dry_run,
        }
        write_trace(state, STEP_NAME, decision)
        mark_step_done(state, STEP_NAME, last_decision=decision, gallery_cleanup_deleted_videos=0)
        print(f"{STEP_NAME}: skipped; no current uploaded video path in state")
        return 0

    candidates = collect_delete_candidates(
        adb=adb,
        cleanup_config=cleanup_config,
        scope=scope,
        remote_video_dir=remote_video_dir,
        current_uploaded_video=current_uploaded_video,
    )
    max_delete_count = int(cleanup_config.get("max_delete_count", 0) or 0)
    if max_delete_count > 0 and len(candidates) > max_delete_count:
        raise RuntimeError(
            f"{STEP_NAME}: found {len(candidates)} videos, which exceeds max_delete_count={max_delete_count}."
        )

    print(f"{STEP_NAME}: found {len(candidates)} video(s) to delete in scope {scope!r}")
    delete_result = delete_candidates(
        adb=adb,
        candidates=candidates,
        cleanup_config=cleanup_config,
        dry_run=dry_run,
    )

    scan_outputs: list[dict[str, str]] = []
    if cleanup_config.get("scan_after_delete", True) and not dry_run:
        scan_outputs = scan_media_after_delete(adb)

    trace_limit = int(cleanup_config.get("trace_paths_limit", 200) or 200)
    decision = {
        "action": STEP_NAME,
        "status": "dry_run" if dry_run else "deleted",
        "scope": scope,
        "remote_video_dir": remote_video_dir,
        "current_uploaded_video": current_uploaded_video,
        "candidate_count": len(candidates),
        "file_delete_count": delete_result["file_delete_count"],
        "record_delete_count": delete_result["record_delete_count"],
        "paths": truncate_list([item["path"] for item in candidates if item.get("path")], trace_limit),
        "record_uris": truncate_list(delete_result["record_uris"], trace_limit),
        "errors": delete_result["errors"],
        "scan_outputs": scan_outputs,
        "dry_run": dry_run,
    }
    write_trace(state, STEP_NAME, decision)
    mark_step_done(
        state,
        STEP_NAME,
        last_decision=decision,
        gallery_cleanup_deleted_videos=delete_result["file_delete_count"],
    )
    return 0


def collect_delete_candidates(
    *,
    adb: Any,
    cleanup_config: dict[str, Any],
    scope: str,
    remote_video_dir: str,
    current_uploaded_video: str = "",
) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    seen_keys: set[str] = set()

    for uri in cleanup_config.get("media_store_uris", DEFAULT_MEDIA_URIS):
        output = adb.content_query(
            uri=str(uri),
            projection="_id:_display_name:_data:relative_path:mime_type",
            check=False,
            timeout=60,
        )
        for row in parse_content_rows(output):
            if not is_video_row(row):
                continue
            path = str(row.get("_data", "") or "")
            if not is_in_scope(
                path,
                row,
                scope=scope,
                remote_video_dir=remote_video_dir,
                current_uploaded_video=current_uploaded_video,
            ):
                continue
            key = normalize_storage_path(path) if path else f"{uri}/{row.get('_id', '')}"
            if key in seen_keys:
                continue
            seen_keys.add(key)
            item = {
                "source": "media_store",
                "uri": str(uri),
                "id": str(row.get("_id", "")),
                "path": path,
                "display_name": str(row.get("_display_name", "")),
                "mime_type": str(row.get("mime_type", "")),
                "relative_path": str(row.get("relative_path", "")),
            }
            if not candidate_matches_name_filters(item, cleanup_config):
                continue
            candidates.append(item)

    if cleanup_config.get("include_filesystem_scan", True):
        for path in list_filesystem_video_paths(
            adb,
            cleanup_config,
            scope,
            remote_video_dir,
            current_uploaded_video=current_uploaded_video,
        ):
            key = normalize_storage_path(path)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            item = {
                "source": "filesystem",
                "uri": "",
                "id": "",
                "path": path,
                "display_name": PurePosixPath(path).name,
                "mime_type": "",
                "relative_path": "",
            }
            if not candidate_matches_name_filters(item, cleanup_config):
                continue
            candidates.append(item)

    return candidates


def delete_candidates(
    *,
    adb: Any,
    candidates: list[dict[str, str]],
    cleanup_config: dict[str, Any],
    dry_run: bool,
) -> dict[str, Any]:
    delete_files = bool(cleanup_config.get("delete_files", True))
    delete_media_store_records = bool(cleanup_config.get("delete_media_store_records", True))
    deleted_paths: set[str] = set()
    deleted_records: set[str] = set()
    record_uris: list[str] = []
    errors: list[str] = []

    for item in candidates:
        path = item.get("path", "")
        normalized_path = normalize_storage_path(path)
        if delete_files and path and path_is_safe_gallery_path(path) and normalized_path not in deleted_paths:
            deleted_paths.add(normalized_path)
            if not dry_run:
                output = adb.shell("rm", "-f", path, check=False, timeout=30).strip()
                if output:
                    errors.append(f"rm {path}: {output}")

        row_id = item.get("id", "")
        uri = item.get("uri", "")
        record_uri = f"{uri}/{row_id}" if uri and row_id else ""
        if delete_media_store_records and record_uri and record_uri not in deleted_records:
            deleted_records.add(record_uri)
            record_uris.append(record_uri)
            if not dry_run:
                output = adb.content_delete(uri=record_uri, check=False, timeout=30).strip()
                if output and not re.search(r"Deleted\s+[1-9]\d*", output, flags=re.IGNORECASE):
                    errors.append(f"content delete {record_uri}: {output}")

    return {
        "file_delete_count": len(deleted_paths),
        "record_delete_count": len(deleted_records),
        "record_uris": record_uris,
        "errors": errors,
    }


def list_filesystem_video_paths(
    adb: Any,
    cleanup_config: dict[str, Any],
    scope: str,
    remote_video_dir: str,
    *,
    current_uploaded_video: str = "",
) -> list[str]:
    extensions = tuple(str(ext).casefold() for ext in cleanup_config.get("video_extensions", DEFAULT_VIDEO_EXTENSIONS))
    if scope == "current_uploaded_video":
        path = current_uploaded_video.strip()
        if not path or not path.casefold().endswith(extensions) or not path_is_safe_gallery_path(path):
            return []
        return [path] if adb.remote_file_exists(path) else []

    roots = cleanup_config.get("filesystem_scan_roots", [])
    if scope == "remote_video_dir":
        roots = [remote_video_dir]
    elif not roots:
        roots = ["/sdcard/DCIM", "/sdcard/Movies", "/sdcard/Pictures"]

    paths: list[str] = []
    seen: set[str] = set()
    for root in roots:
        root_text = str(root)
        output = adb.shell("find", root_text, "-type", "f", check=False, timeout=120)
        for raw_line in output.splitlines():
            path = raw_line.strip()
            if not path or path.startswith("find:"):
                continue
            if not path.casefold().endswith(extensions):
                continue
            if not path_is_safe_gallery_path(path):
                continue
            if not is_in_scope(
                path,
                {},
                scope=scope,
                remote_video_dir=remote_video_dir,
                current_uploaded_video=current_uploaded_video,
            ):
                continue
            normalized = normalize_storage_path(path)
            if normalized in seen:
                continue
            seen.add(normalized)
            paths.append(path)
    return paths


def parse_content_rows(output: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line in output.splitlines():
        line = line.strip()
        if not line.startswith("Row:"):
            continue
        rows.append(parse_content_row(line))
    return rows


def parse_content_row(line: str) -> dict[str, str]:
    _, _, body = line.partition(" ")
    body = re.sub(r"^\d+\s+", "", body)
    fields: dict[str, str] = {}
    pattern = re.compile(
        r"(?P<key>_id|_display_name|_data|relative_path|mime_type)="
        r"(?P<value>.*?)(?=,\s(?:_id|_display_name|_data|relative_path|mime_type)=|$)"
    )
    for match in pattern.finditer(body):
        value = match.group("value").strip()
        fields[match.group("key")] = "" if value == "null" else value
    return fields


def is_video_row(row: dict[str, str]) -> bool:
    mime_type = str(row.get("mime_type", "")).casefold()
    path = str(row.get("_data", "")).casefold()
    name = str(row.get("_display_name", "")).casefold()
    if mime_type.startswith("video/"):
        return True
    return any((path or name).endswith(ext) for ext in DEFAULT_VIDEO_EXTENSIONS)


def candidate_matches_name_filters(item: dict[str, str], cleanup_config: dict[str, Any]) -> bool:
    patterns = [
        str(pattern)
        for pattern in cleanup_config.get("display_name_patterns", cleanup_config.get("name_patterns", []))
        if str(pattern).strip()
    ]
    regexes = [
        re.compile(str(pattern))
        for pattern in cleanup_config.get("filename_regexes", [])
        if str(pattern).strip()
    ]
    if not patterns and not regexes:
        return True

    names = [
        str(item.get("display_name", "") or ""),
        PurePosixPath(str(item.get("path", "") or "")).name,
    ]
    for name in (name for name in names if name):
        if any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns):
            return True
        if any(regex.search(name) for regex in regexes):
            return True
    return False


def is_in_scope(
    path: str,
    row: dict[str, str],
    *,
    scope: str,
    remote_video_dir: str,
    current_uploaded_video: str = "",
) -> bool:
    if scope == "all_gallery_videos":
        return True
    if scope == "remote_video_dir":
        if path:
            return path_is_under(path, remote_video_dir)
        relative_path = str(row.get("relative_path", "") or "")
        return relative_path.strip("/").casefold() == media_relative_dir(remote_video_dir).strip("/").casefold()
    if scope == "current_uploaded_video":
        current_path = current_uploaded_video.strip()
        if not current_path:
            return False
        normalized_current = normalize_storage_path(current_path).rstrip("/")
        if path:
            return normalize_storage_path(path).rstrip("/") == normalized_current
        display_name = str(row.get("_display_name", "") or "")
        if not display_name or display_name != PurePosixPath(current_path).name:
            return False
        relative_path = str(row.get("relative_path", "") or "").strip("/")
        if not relative_path:
            return True
        return relative_path.casefold() == media_relative_parent_dir(current_path).strip("/").casefold()
    raise ValueError(f"Unsupported gallery_cleanup scope: {scope}")


def path_is_under(path: str, parent: str) -> bool:
    normalized_path = normalize_storage_path(path).rstrip("/")
    normalized_parent = normalize_storage_path(parent).rstrip("/")
    return normalized_path == normalized_parent or normalized_path.startswith(normalized_parent + "/")


def normalize_storage_path(path: str) -> str:
    if path.startswith("/sdcard/"):
        return "/storage/emulated/0/" + path.removeprefix("/sdcard/")
    return path


def media_relative_dir(path: str) -> str:
    normalized = normalize_storage_path(path)
    prefix = "/storage/emulated/0/"
    if not normalized.startswith(prefix):
        return ""
    relative = normalized.removeprefix(prefix).strip("/")
    if relative and not relative.endswith("/"):
        relative += "/"
    return relative


def media_relative_parent_dir(path: str) -> str:
    return media_relative_dir(str(PurePosixPath(normalize_storage_path(path)).parent))


def path_is_safe_gallery_path(path: str) -> bool:
    normalized = normalize_storage_path(path)
    return normalized.startswith("/storage/emulated/0/")


def scan_media_after_delete(adb: Any) -> list[dict[str, str]]:
    outputs: list[dict[str, str]] = []
    for volume in ("external_primary", "external"):
        output = adb.shell("cmd", "media", "scan-volume", volume, check=False, timeout=120).strip()
        outputs.append({"command": f"cmd media scan-volume {volume}", "output": output})
    return outputs


def truncate_list(values: list[str], limit: int) -> list[str]:
    if limit <= 0 or len(values) <= limit:
        return values
    remaining = len(values) - limit
    return values[:limit] + [f"... {remaining} more"]


if __name__ == "__main__":
    raise SystemExit(main())
