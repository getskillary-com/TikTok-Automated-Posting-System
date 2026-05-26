from __future__ import annotations

import argparse

from common import (
    add_step_args,
    load_or_create_state,
    load_step_config,
    make_adb,
    mark_step_done,
    require_video_path,
    write_trace,
)
from delete_gallery_videos import collect_delete_candidates, delete_candidates, scan_media_after_delete


STEP_NAME = "push_video"


def main() -> int:
    parser = argparse.ArgumentParser(description="Push the selected local video to the phone.")
    add_step_args(parser)
    args = parser.parse_args()

    config = load_step_config(args.config)
    state = load_or_create_state(args, config)
    video_path = require_video_path(state)
    adb = make_adb(config)
    media_store_config = config["pipeline"].get("media_store", {})
    pre_push_cleanup = cleanup_workflow_videos_before_push(
        adb=adb,
        config=config,
        state=state,
        media_store_config=media_store_config,
    )

    remote_path = adb.push_video(
        video_path,
        config["adb"]["remote_video_dir"],
        touch_after_push=bool(media_store_config.get("touch_after_push", True)),
        unique_remote_name=bool(media_store_config.get("unique_remote_name", True)),
    )
    print(f"{STEP_NAME}: pushed {video_path} -> {remote_path}")
    remote_exists = adb.remote_file_exists(remote_path)
    if not remote_exists:
        raise RuntimeError(f"{STEP_NAME}: pushed file is not visible on the phone filesystem: {remote_path}")

    fatal_message = ""
    if media_store_config.get("wait_for_index", True):
        wait_result = adb.wait_for_media_file(
            remote_path,
            timeout_seconds=float(media_store_config.get("index_timeout_seconds", 30)),
            poll_seconds=float(media_store_config.get("index_poll_seconds", 1)),
        )
        media_store_actions = []
        if wait_result["found"]:
            print(f"{STEP_NAME}: media indexed as {wait_result['display_name']}")
            if media_store_config.get("refresh_index_timestamps", True):
                timestamp_result = adb.update_media_timestamps(remote_path)
                media_store_actions.append({"action": "update_media_timestamps", **timestamp_result})
        else:
            if media_store_config.get("insert_on_missing", True):
                print(f"{STEP_NAME}: media index missing; registering MediaStore record")
                insert_result = adb.insert_media_store_record(remote_path)
                media_store_actions.append({"action": "insert_media_store_record", **insert_result})
                adb.scan_media_file(remote_path)
                adb.wait(float(media_store_config.get("after_insert_wait_seconds", 2.0)))
                wait_result = adb.wait_for_media_file(
                    remote_path,
                    timeout_seconds=float(media_store_config.get("post_insert_index_timeout_seconds", 30)),
                    poll_seconds=float(media_store_config.get("index_poll_seconds", 1)),
                )
                if wait_result["found"] and media_store_config.get("refresh_index_timestamps", True):
                    timestamp_result = adb.update_media_timestamps(remote_path)
                    media_store_actions.append({"action": "update_media_timestamps", **timestamp_result})
            if wait_result["found"]:
                print(f"{STEP_NAME}: media indexed as {wait_result['display_name']}")
            else:
                message = f"{STEP_NAME}: media index did not confirm {wait_result['display_name']}"
                if media_store_config.get("fail_on_index_timeout", False) or media_store_config.get(
                    "require_index_before_upload",
                    True,
                ):
                    fatal_message = (
                        message
                        + ". The file exists on the phone, but Android MediaStore does not expose it yet, "
                        "so TikTok Studio will keep showing old media."
                    )
                print(message)
    else:
        wait_seconds = float(media_store_config.get("after_scan_wait_seconds", 3.0))
        adb.wait(wait_seconds)
        wait_result = {"found": "skipped", "wait_seconds": wait_seconds}
        media_store_actions = []

    top_result = {"top_matches": "skipped"}
    if media_store_config.get("require_top_after_push", False):
        top_result = adb.wait_until_media_is_top(
            remote_path,
            remote_dir=config["adb"]["remote_video_dir"],
            timeout_seconds=float(media_store_config.get("top_timeout_seconds", 30)),
            poll_seconds=float(media_store_config.get("top_poll_seconds", 1)),
            limit=int(media_store_config.get("top_query_limit", 10)),
        )
        media_store_actions.append({"action": "wait_until_media_is_top", **top_result})
        if top_result.get("top_matches"):
            print(f"{STEP_NAME}: media is now first in MediaStore as {top_result.get('display_name')}")
        else:
            fatal_message = (
                f"{STEP_NAME}: pushed media is indexed but is not the newest MediaStore item. "
                "TikTok Studio's first media slot may still point to a different video."
            )

    trace = {
        "action": "push_video",
        "local_path": str(video_path),
        "remote_path": remote_path,
        "remote_exists": remote_exists,
        "touch_after_push": bool(media_store_config.get("touch_after_push", True)),
        "unique_remote_name": bool(media_store_config.get("unique_remote_name", True)),
        "media_index": wait_result,
        "media_top": top_result,
        "media_store_actions": media_store_actions,
        "pre_push_cleanup": pre_push_cleanup,
    }
    write_trace(state, STEP_NAME, trace)
    if fatal_message:
        raise RuntimeError(fatal_message)
    mark_step_done(state, STEP_NAME, remote_video_path=remote_path, last_decision=trace)
    return 0


def cleanup_workflow_videos_before_push(
    *,
    adb: object,
    config: dict[str, object],
    state: dict[str, object],
    media_store_config: dict[str, object],
) -> dict[str, object]:
    cleanup_config = media_store_config.get("pre_push_cleanup", {})
    if not isinstance(cleanup_config, dict) or not cleanup_config.get("enabled", False):
        return {"enabled": False, "status": "skipped"}

    merged_config = {
        "scope": "remote_video_dir",
        "display_name_patterns": [
            "20??????_??????_*.mp4",
            "20??????-??????_*.mp4",
            "gcs_*.mp4",
        ],
        "include_filesystem_scan": True,
        "delete_files": True,
        "delete_media_store_records": True,
        "scan_after_delete": True,
        "max_delete_count": 200,
        **cleanup_config,
    }
    remote_video_dir = str(config.get("adb", {}).get("remote_video_dir", "/sdcard/DCIM/Camera"))  # type: ignore[union-attr]
    candidates = collect_delete_candidates(
        adb=adb,
        cleanup_config=merged_config,
        scope=str(merged_config.get("scope") or "remote_video_dir"),
        remote_video_dir=remote_video_dir,
    )
    max_delete_count = int(merged_config.get("max_delete_count", 0) or 0)
    if max_delete_count > 0 and len(candidates) > max_delete_count:
        raise RuntimeError(
            f"{STEP_NAME}: pre-push cleanup found {len(candidates)} videos, "
            f"which exceeds max_delete_count={max_delete_count}."
        )

    dry_run = bool(state.get("dry_run", False))
    result = delete_candidates(
        adb=adb,
        candidates=candidates,
        cleanup_config=merged_config,
        dry_run=dry_run,
    )
    scan_outputs = []
    if merged_config.get("scan_after_delete", True) and not dry_run and candidates:
        scan_outputs = scan_media_after_delete(adb)
    print(
        f"{STEP_NAME}: pre-push cleanup removed "
        f"{result['file_delete_count']} file(s), {result['record_delete_count']} record(s)"
    )
    return {
        "enabled": True,
        "dry_run": dry_run,
        "candidate_count": len(candidates),
        "file_delete_count": result["file_delete_count"],
        "record_delete_count": result["record_delete_count"],
        "errors": result["errors"],
        "scan_outputs": scan_outputs,
        "paths": [item["path"] for item in candidates if item.get("path")],
    }


if __name__ == "__main__":
    raise SystemExit(main())
