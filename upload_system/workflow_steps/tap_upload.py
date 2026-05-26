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
    write_trace,
)
from upload_system.adb import UINode, parse_bounds


STEP_NAME = "tap_upload"


def main() -> int:
    parser = argparse.ArgumentParser(description="Find and tap the upload button, then verify the media picker opened.")
    add_step_args(parser)
    args = parser.parse_args()

    config = load_step_config(args.config)
    state = load_or_create_state(args, config)
    adb = make_adb(config)

    decision = tap_upload_and_verify(adb=adb, state=state, config=config)
    mark_step_done(state, STEP_NAME, last_decision=decision)
    return 0


def tap_upload_and_verify(*, adb: Any, state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    upload_config = config["pipeline"].get("upload", {})
    dry_run = bool(state.get("dry_run", False))
    before_delay = float(upload_config.get("before_tap_delay_seconds", 0.0))
    if before_delay:
        adb.wait(before_delay)

    screen = capture_screen(adb=adb, state=state, step_name=STEP_NAME, config=config)
    points = upload_tap_points(
        screen=screen,
        target_texts=config["pipeline"]["targets"]["upload"],
        upload_config=upload_config,
    )
    if not points:
        width, height = screen["screen_size"]
        points = [
            {
                "x": int(width * 0.5),
                "y": int(height * 0.226),
                "label": "upload_fallback",
                "source": "fallback",
                "bounds": "",
            }
        ]

    max_attempts = int(upload_config.get("tap_attempts", len(points)))
    max_attempts = max(1, min(max_attempts, len(points)))
    after_tap_delay = float(upload_config.get("after_tap_delay_seconds", 1.5))
    attempts: list[dict[str, Any]] = []

    for index, point in enumerate(points[:max_attempts], start=1):
        attempt = {
            "action": "tap",
            "x": point["x"],
            "y": point["y"],
            "label": point["label"],
            "source": point["source"],
            "bounds": point.get("bounds", ""),
            "attempt": index,
        }
        attempts.append(attempt)
        suffix = " [dry-run]" if dry_run else ""
        print(f"{STEP_NAME}: tap ({attempt['x']}, {attempt['y']}) {attempt['label']!r} via {attempt['source']}{suffix}")
        if dry_run:
            break

        adb.tap(int(attempt["x"]), int(attempt["y"]))
        adb.wait(after_tap_delay)
        verify_screen = wait_for_media_picker(adb=adb, state=state, config=config, suffix=f"after_tap_{index:02d}")
        if media_picker_visible(verify_screen["nodes"]):
            decision = {
                "action": "tap_upload",
                "result": "media_picker_visible",
                "attempts": attempts,
                "reason": "Upload tap opened the media picker.",
                "dry_run": dry_run,
            }
            write_trace(state, STEP_NAME, decision)
            return decision

    decision = {
        "action": "manual_review",
        "result": "media_picker_not_visible",
        "attempts": attempts,
        "reason": "Tapped upload, but the media picker did not appear.",
        "dry_run": dry_run,
    }
    write_trace(state, STEP_NAME, decision)
    raise RuntimeError(f"{STEP_NAME}: {decision['reason']}")


def upload_tap_points(
    *,
    screen: dict[str, Any],
    target_texts: list[str],
    upload_config: dict[str, Any],
) -> list[dict[str, Any]]:
    nodes = screen["nodes"]
    points: list[dict[str, Any]] = []
    for node in nodes:
        if node.center_x is None or node.center_y is None:
            continue
        label = node.text or node.description
        if not text_matches_any(label, target_texts):
            continue
        container = containing_clickable_node(nodes, node.center_x, node.center_y)
        if container and container.center_x is not None and container.center_y is not None:
            points.extend(container_points(container, upload_config))
        points.append(
            {
                "x": node.center_x,
                "y": node.center_y,
                "label": label or "upload_text",
                "source": "ui_text",
                "bounds": node.bounds,
            }
        )

    width, height = screen["screen_size"]
    for fallback in upload_config.get("fallback_points", []):
        points.append(
            {
                "x": int(width * float(fallback["x_ratio"])),
                "y": int(height * float(fallback["y_ratio"])),
                "label": str(fallback.get("label", "upload_fallback")),
                "source": "configured_fallback",
                "bounds": "",
            }
        )

    return dedupe_points(points)


def container_points(node: UINode, upload_config: dict[str, Any]) -> list[dict[str, Any]]:
    bounds = parse_bounds(node.bounds)
    if not bounds:
        return [
            {
                "x": node.center_x,
                "y": node.center_y,
                "label": "upload_container",
                "source": "ui_clickable_container",
                "bounds": node.bounds,
            }
        ]

    left, top, right, bottom = bounds
    x_ratios = upload_config.get("container_x_ratios", [0.5, 0.45, 0.55])
    y_ratios = upload_config.get("container_y_ratios", [0.5])
    points = []
    for y_ratio in y_ratios:
        for x_ratio in x_ratios:
            points.append(
                {
                    "x": int(left + (right - left) * float(x_ratio)),
                    "y": int(top + (bottom - top) * float(y_ratio)),
                    "label": "upload_container",
                    "source": "ui_clickable_container",
                    "bounds": node.bounds,
                }
            )
    return points


def containing_clickable_node(nodes: list[UINode], x: int, y: int) -> UINode | None:
    candidates: list[tuple[int, UINode]] = []
    for node in nodes:
        if node.clickable != "true" or node.center_x is None or node.center_y is None:
            continue
        bounds = parse_bounds(node.bounds)
        if not bounds:
            continue
        left, top, right, bottom = bounds
        if left <= x <= right and top <= y <= bottom:
            candidates.append(((right - left) * (bottom - top), node))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def wait_for_media_picker(
    *,
    adb: Any,
    state: dict[str, Any],
    config: dict[str, Any],
    suffix: str,
) -> dict[str, Any]:
    upload_config = config["pipeline"].get("upload", {})
    attempts = int(upload_config.get("verify_attempts", 3))
    wait_seconds = float(upload_config.get("verify_wait_seconds", 1.0))
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
        raise RuntimeError(f"{STEP_NAME}: screen capture failed while verifying upload.")
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
    return any(
        any(term in normalize_text(node.text) or term in normalize_text(node.description) for term in header_terms)
        for node in nodes
    )


def text_matches_any(value: str, targets: list[str]) -> bool:
    normalized = normalize_text(value)
    if not normalized:
        return False
    return any(normalize_text(str(target)) in normalized for target in targets if normalize_text(str(target)))


def dedupe_points(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[int, int]] = set()
    result: list[dict[str, Any]] = []
    for point in points:
        key = (int(point["x"]), int(point["y"]))
        if key in seen:
            continue
        seen.add(key)
        result.append(point)
    return result


if __name__ == "__main__":
    raise SystemExit(main())

