from __future__ import annotations

import argparse
from typing import Any

from common import (
    add_step_args,
    capture_screen,
    load_or_create_state,
    load_step_config,
    make_adb,
    mark_step_done,
    normalize_text,
    tap_text_target,
    write_trace,
)
from upload_system.adb import UINode, parse_bounds


STEP_NAME = "refresh_media_picker"


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh TikTok Studio media picker after opening upload.")
    add_step_args(parser)
    args = parser.parse_args()

    config = load_step_config(args.config)
    state = load_or_create_state(args, config)
    adb = make_adb(config)

    decision = refresh_media_picker(adb=adb, state=state, config=config)
    write_trace(state, STEP_NAME, decision)
    mark_step_done(state, STEP_NAME, last_decision=decision)
    return 0


def refresh_media_picker(*, adb: Any, state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    picker_config = config["pipeline"].get("media_picker", {})
    dry_run = bool(state.get("dry_run", False))

    screen = wait_for_media_picker(adb=adb, state=state, config=config, suffix="before_refresh")
    if not media_picker_visible(screen["nodes"]):
        decision = {
            "action": "manual_review",
            "source": "ui_xml",
            "reason": "Media picker did not appear after tapping upload.",
            "dry_run": dry_run,
        }
        write_trace(state, STEP_NAME, decision)
        raise RuntimeError(f"{STEP_NAME}: {decision['reason']}")

    actions: list[dict[str, Any]] = [
        {
            "action": "wait_for_media_picker",
            "source": "ui_xml",
            "reason": "Media picker is visible.",
        }
    ]

    if not picker_config.get("refresh_by_reopen", True):
        return {
            "action": "skip_refresh",
            "source": "config",
            "reason": "media_picker.refresh_by_reopen is disabled.",
            "actions": actions,
            "dry_run": dry_run,
        }

    attempts = int(picker_config.get("refresh_reopen_attempts", 1))
    for attempt in range(1, attempts + 1):
        close_decision = close_media_picker(
            adb=adb,
            screen=screen,
            picker_config=picker_config,
            dry_run=dry_run,
            attempt=attempt,
        )
        actions.append(close_decision)
        adb.wait(float(picker_config.get("after_close_seconds", 1.0)))

        if dry_run:
            actions.append(
                {
                    "action": "tap",
                    "label": "upload_reopen",
                    "source": "dry_run",
                    "reason": "Dry run: would tap upload again to reload the picker.",
                }
            )
            continue

        reopen_decision = tap_text_target(
            adb=adb,
            state=state,
            config=config,
            step_name=f"{STEP_NAME}_reopen_upload_{attempt:02d}",
            texts=config["pipeline"]["targets"]["upload"],
            allow_fallback=upload_reopen_fallback(config),
        )
        actions.append(reopen_decision)
        adb.wait(float(picker_config.get("after_reopen_seconds", 3.0)))

        screen = wait_for_media_picker(
            adb=adb,
            state=state,
            config=config,
            suffix=f"after_reopen_{attempt:02d}",
        )
        if not media_picker_visible(screen["nodes"]):
            decision = {
                "action": "manual_review",
                "source": "ui_xml",
                "reason": "Media picker did not reappear after reopening upload.",
                "actions": actions,
                "dry_run": dry_run,
            }
            write_trace(state, STEP_NAME, decision)
            raise RuntimeError(f"{STEP_NAME}: {decision['reason']}")

    return {
        "action": "refresh_picker",
        "source": "close_and_reopen",
        "reason": "Closed and reopened the media picker so TikTok Studio reloads the newest media list.",
        "actions": actions,
        "dry_run": dry_run,
    }


def upload_reopen_fallback(config: dict[str, Any]) -> dict[str, float] | None:
    fallback_points = config["pipeline"].get("upload", {}).get("fallback_points", [])
    if isinstance(fallback_points, list):
        for point in fallback_points:
            if isinstance(point, dict) and "x_ratio" in point and "y_ratio" in point:
                return {
                    "x_ratio": float(point["x_ratio"]),
                    "y_ratio": float(point["y_ratio"]),
                }
    return None


def wait_for_media_picker(
    *,
    adb: Any,
    state: dict[str, Any],
    config: dict[str, Any],
    suffix: str,
) -> dict[str, Any]:
    picker_config = config["pipeline"].get("media_picker", {})
    attempts = int(picker_config.get("refresh_wait_attempts", picker_config.get("wait_attempts", 6)))
    wait_seconds = float(picker_config.get("refresh_wait_seconds", picker_config.get("wait_seconds", 1.0)))

    last_screen: dict[str, Any] | None = None
    for attempt in range(1, attempts + 1):
        screen = capture_screen(
            adb=adb,
            state=state,
            step_name=f"{STEP_NAME}_{suffix}_{attempt:02d}",
            config=config,
        )
        last_screen = screen
        if media_picker_visible(screen["nodes"]):
            return screen
        adb.wait(wait_seconds)

    if last_screen is None:
        raise RuntimeError(f"{STEP_NAME}: screen capture failed while waiting for media picker.")
    return last_screen


def media_picker_visible(nodes: list[UINode]) -> bool:
    has_grid = any(
        "GridView" in node.class_name
        or "RecyclerView" in node.class_name
        or node.resource_id.endswith(":id/flr")
        or node.resource_id.endswith(":id/ewc")
        for node in nodes
    )
    if not has_grid:
        return False

    header_terms = ("all", "recent", "recents", "videos", "photos", "全部", "最近", "视频", "照片", "相册")
    has_header = any(
        any(term in normalize_text(node.text) or term in normalize_text(node.description) for term in header_terms)
        for node in nodes
    )
    return has_header or find_close_control(nodes) is not None


def close_media_picker(
    *,
    adb: Any,
    screen: dict[str, Any],
    picker_config: dict[str, Any],
    dry_run: bool,
    attempt: int,
) -> dict[str, Any]:
    control = find_close_control(screen["nodes"])
    if control and control.center_x is not None and control.center_y is not None:
        decision = {
            "action": "tap",
            "x": control.center_x,
            "y": control.center_y,
            "label": control.text or control.description or "close",
            "source": "ui_close_control",
            "bounds": control.bounds,
            "reason": "Tapped the media picker close control before reopening upload.",
            "attempt": attempt,
        }
    else:
        width, height = screen["screen_size"]
        decision = {
            "action": "tap",
            "x": int(width * float(picker_config.get("close_x_ratio", 0.065))),
            "y": int(height * float(picker_config.get("close_y_ratio", 0.055))),
            "label": "close",
            "source": "fallback_top_left",
            "bounds": "",
            "reason": "Tapped the configured top-left close area before reopening upload.",
            "attempt": attempt,
        }

    suffix = " [dry-run]" if dry_run else ""
    print(f"{STEP_NAME}: tap ({decision['x']}, {decision['y']}) close picker via {decision['source']}{suffix}")
    if not dry_run:
        adb.tap(int(decision["x"]), int(decision["y"]))
    return decision


def find_close_control(nodes: list[UINode]) -> UINode | None:
    exact_terms = ("close", "cancel", "back", "关闭", "取消", "返回")
    top_left_candidates: list[tuple[int, int, UINode]] = []
    for node in nodes:
        if node.center_x is None or node.center_y is None:
            continue
        text = normalize_text(node.text)
        description = normalize_text(node.description)
        if any(term in text or term in description for term in exact_terms):
            return node

        bounds = parse_bounds(node.bounds)
        if not bounds or node.clickable != "true":
            continue
        left, top, right, bottom = bounds
        width = right - left
        height = bottom - top
        if node.center_x <= 180 and node.center_y <= 220 and width <= 180 and height <= 180:
            top_left_candidates.append((node.center_y, node.center_x, node))

    if not top_left_candidates:
        return None
    top_left_candidates.sort(key=lambda item: (item[0], item[1]))
    return top_left_candidates[0][2]


if __name__ == "__main__":
    raise SystemExit(main())

