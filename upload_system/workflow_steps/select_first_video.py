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


STEP_NAME = "select_first_video"


def main() -> int:
    parser = argparse.ArgumentParser(description="Select the first video in the media picker.")
    add_step_args(parser)
    args = parser.parse_args()

    config = load_step_config(args.config)
    state = load_or_create_state(args, config)
    adb = make_adb(config)

    decision = select_first_media(adb=adb, state=state, config=config)
    mark_step_done(state, STEP_NAME, last_decision=decision)
    return 0


def select_first_media(*, adb: Any, state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    picker_config = config["pipeline"].get("media_picker", {})
    dry_run = bool(state.get("dry_run", False))
    album_actions: list[dict[str, Any]] = []
    screen, candidate = wait_for_first_media_candidate(
        adb=adb,
        state=state,
        config=config,
        suffix="initial",
    )

    if candidate is not None and picker_config.get("prefer_video_album", True):
        screen, changed_album, album_actions = choose_preferred_album(
            adb=adb,
            state=state,
            config=config,
            screen=screen,
            picker_config=picker_config,
            dry_run=dry_run,
        )
        if changed_album:
            candidate = first_media_candidate(screen["nodes"], picker_config)
            if candidate is None:
                screen, candidate = wait_for_first_media_candidate(
                    adb=adb,
                    state=state,
                    config=config,
                    suffix="after_preferred_album",
                )

    if candidate is None:
        open_fallback = picker_config.get("open_fallback")
        if open_fallback:
            width, height = screen["screen_size"]
            x = int(width * float(open_fallback["x_ratio"]))
            y = int(height * float(open_fallback["y_ratio"]))
            print(f"{STEP_NAME}: media picker not visible yet; tapping configured picker entry ({x}, {y})")
            if not dry_run:
                adb.tap(x, y)
            adb.wait(float(picker_config.get("after_open_fallback_seconds", 2.0)))
            screen, candidate = wait_for_first_media_candidate(
                adb=adb,
                state=state,
                config=config,
                suffix="after_open_fallback",
            )

    if candidate is None:
        raise RuntimeError(
            f"{STEP_NAME}: media picker was not detected. "
            "Check the latest screenshot under this run's screenshots folder."
        )

    tap_target = selection_target_for_media(screen["nodes"], candidate, picker_config)
    decision = {
        "action": "tap",
        "x": tap_target["x"],
        "y": tap_target["y"],
        "label": "first_media_selection",
        "matched_text": "",
        "confidence": 0.8,
        "source": tap_target["source"],
        "is_final_publish": False,
        "bounds": tap_target["bounds"],
        "media_bounds": candidate.bounds,
        "reason": "Selected the first media item using its selection control when available.",
        "album_actions": album_actions,
        "dry_run": dry_run,
    }
    write_trace(state, STEP_NAME, decision)
    print(f"{STEP_NAME}: tap ({decision['x']}, {decision['y']}) first media selection via ui_grid")
    if not dry_run:
        adb.tap(int(decision["x"]), int(decision["y"]))
    adb.wait(float(config["pipeline"]["post_step_delay_seconds"]))
    return decision


def choose_preferred_album(
    *,
    adb: Any,
    state: dict[str, Any],
    config: dict[str, Any],
    screen: dict[str, Any],
    picker_config: dict[str, Any],
    dry_run: bool,
) -> tuple[dict[str, Any], bool, list[dict[str, Any]]]:
    target_terms = text_terms(picker_config.get("preferred_album_terms", ["Videos", "Video", "\u89c6\u9891"]))
    if not target_terms:
        return screen, False, []

    actions: list[dict[str, Any]] = []
    header_target = find_text_node(
        [node for node in screen["nodes"] if is_header_node(node, screen["screen_size"])],
        target_terms,
        require_text=True,
    )
    if header_target and header_target.center_x is not None and header_target.center_y is not None:
        label = header_target.text or header_target.description or "preferred_album"
        actions.append(
            {
                "action": "tap",
                "x": header_target.center_x,
                "y": header_target.center_y,
                "label": label,
                "source": "header_preferred_album",
                "reason": "Tapped the visible video tab/filter before choosing the first media item.",
            }
        )
        suffix = " [dry-run]" if dry_run else ""
        print(
            f"{STEP_NAME}: tap ({header_target.center_x}, {header_target.center_y}) "
            f"preferred album {label!r} via header{suffix}"
        )
        if dry_run:
            return screen, False, actions
        adb.tap(int(header_target.center_x), int(header_target.center_y))
        adb.wait(float(picker_config.get("after_album_select_seconds", 1.5)))
        updated_screen = capture_screen(adb=adb, state=state, step_name=f"{STEP_NAME}_preferred_album", config=config)
        return updated_screen, True, actions

    dropdown = album_dropdown_target(screen["nodes"], screen["screen_size"], picker_config)
    if dropdown is None:
        actions.append(
            {
                "action": "skip_preferred_album",
                "source": "ui_header",
                "reason": "Could not find the media picker album dropdown.",
            }
        )
        return screen, False, actions

    actions.append(
        {
            "action": "tap",
            "x": dropdown["x"],
            "y": dropdown["y"],
            "label": dropdown["label"],
            "source": dropdown["source"],
            "reason": "Opened the album dropdown before selecting media.",
        }
    )
    suffix = " [dry-run]" if dry_run else ""
    print(f"{STEP_NAME}: tap ({dropdown['x']}, {dropdown['y']}) album dropdown via {dropdown['source']}{suffix}")
    if dry_run:
        return screen, False, actions

    adb.tap(int(dropdown["x"]), int(dropdown["y"]))
    adb.wait(float(picker_config.get("after_album_open_seconds", 1.0)))
    options_screen = capture_screen(adb=adb, state=state, step_name=f"{STEP_NAME}_album_options", config=config)
    option = find_text_node(options_screen["nodes"], target_terms, require_text=True)
    if option is None or option.center_x is None or option.center_y is None:
        actions.append(
            {
                "action": "skip_preferred_album",
                "source": "album_options",
                "reason": "Could not find a Videos/video album option after opening the dropdown.",
            }
        )
        adb.shell("input", "keyevent", "4", check=False, timeout=15)
        adb.wait(float(picker_config.get("after_album_select_seconds", 1.5)))
        restored_screen = capture_screen(
            adb=adb,
            state=state,
            step_name=f"{STEP_NAME}_album_options_closed",
            config=config,
        )
        return restored_screen, False, actions

    label = option.text or option.description or "preferred_album"
    actions.append(
        {
            "action": "tap",
            "x": option.center_x,
            "y": option.center_y,
            "label": label,
            "source": "album_option",
            "reason": "Selected the preferred video album/filter before choosing the first media item.",
        }
    )
    print(f"{STEP_NAME}: tap ({option.center_x}, {option.center_y}) preferred album {label!r}")
    adb.tap(int(option.center_x), int(option.center_y))
    adb.wait(float(picker_config.get("after_album_select_seconds", 1.5)))
    updated_screen = capture_screen(adb=adb, state=state, step_name=f"{STEP_NAME}_preferred_album", config=config)
    return updated_screen, True, actions


def wait_for_first_media_candidate(
    *,
    adb: Any,
    state: dict[str, Any],
    config: dict[str, Any],
    suffix: str,
) -> tuple[dict[str, Any], UINode | None]:
    picker_config = config["pipeline"].get("media_picker", {})
    attempts = int(picker_config.get("wait_attempts", 6))
    wait_seconds = float(picker_config.get("wait_seconds", 1.0))

    last_screen: dict[str, Any] | None = None
    for attempt in range(1, attempts + 1):
        screen = capture_screen(
            adb=adb,
            state=state,
            step_name=f"{STEP_NAME}_{suffix}_{attempt:02d}",
            config=config,
        )
        last_screen = screen
        candidate = first_media_candidate(screen["nodes"], picker_config)
        if candidate:
            return screen, candidate
        adb.wait(wait_seconds)

    if last_screen is None:
        raise RuntimeError(f"{STEP_NAME}: screen capture failed before media selection.")
    return last_screen, None


def first_media_candidate(nodes: list[UINode], picker_config: dict[str, Any]) -> UINode | None:
    grid_bounds = [
        parse_bounds(node.bounds)
        for node in nodes
        if "GridView" in node.class_name or node.resource_id.endswith(":id/flr")
    ]
    grid_bounds = [bounds for bounds in grid_bounds if bounds]
    if not grid_bounds:
        return None

    min_size = int(picker_config.get("thumbnail_min_size", 120))
    candidates: list[tuple[int, int, UINode]] = []
    for node in nodes:
        bounds = parse_bounds(node.bounds)
        if not bounds or node.clickable != "true" or node.center_x is None or node.center_y is None:
            continue
        left, top, right, bottom = bounds
        width = right - left
        height = bottom - top
        if width < min_size or height < min_size:
            continue
        if any(point_inside_grid(node.center_x, node.center_y, grid) for grid in grid_bounds):
            candidates.append((top, left, node))

    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[0][2]


def album_dropdown_target(
    nodes: list[UINode],
    screen_size: tuple[int, int],
    picker_config: dict[str, Any],
) -> dict[str, Any] | None:
    terms = text_terms(
        picker_config.get(
            "album_dropdown_terms",
            ["All", "Recent", "Recents", "Videos", "Photos", "\u5168\u90e8", "\u6700\u8fd1", "\u89c6\u9891", "\u7167\u7247", "\u76f8\u518c"],
        )
    )
    node = find_text_node(
        [candidate for candidate in nodes if is_header_node(candidate, screen_size)],
        terms,
    )
    if node and node.center_x is not None and node.center_y is not None:
        return {
            "x": node.center_x,
            "y": node.center_y,
            "label": node.text or node.description or "album_dropdown",
            "source": "ui_header_text",
        }

    width, height = screen_size
    return {
        "x": int(width * float(picker_config.get("album_dropdown_x_ratio", 0.5))),
        "y": int(height * float(picker_config.get("album_dropdown_y_ratio", 0.065))),
        "label": "album_dropdown",
        "source": "configured_album_dropdown",
    }


def header_has_term(nodes: list[UINode], terms: list[str], screen_size: tuple[int, int]) -> bool:
    return find_text_node([node for node in nodes if is_header_node(node, screen_size)], terms) is not None


def find_text_node(nodes: list[UINode], terms: list[str], *, require_text: bool = False) -> UINode | None:
    matches: list[tuple[int, int, UINode]] = []
    for node in nodes:
        if node.center_x is None or node.center_y is None:
            continue
        text = normalize_text(node.text)
        if require_text and not text:
            continue
        description = normalize_text(node.description)
        if not any(term and (term in text or term in description) for term in terms):
            continue
        clickable_bonus = 0 if node.clickable == "true" else 1
        matches.append((clickable_bonus, node.center_y, node))
    if not matches:
        return None
    matches.sort(key=lambda item: (item[0], item[1]))
    return matches[0][2]


def is_header_node(node: UINode, screen_size: tuple[int, int]) -> bool:
    if node.center_x is None or node.center_y is None:
        return False
    width, height = screen_size
    return width * 0.15 <= node.center_x <= width * 0.85 and node.center_y <= height * 0.14


def text_terms(values: Any) -> list[str]:
    if not isinstance(values, list):
        values = [values]
    return [normalize_text(str(value)) for value in values if str(value).strip()]


def selection_target_for_media(nodes: list[UINode], media: UINode, picker_config: dict[str, Any]) -> dict[str, Any]:
    control = selection_control_for_media(nodes, media, picker_config)
    if control and control.center_x is not None and control.center_y is not None:
        return {
            "x": control.center_x,
            "y": control.center_y,
            "bounds": control.bounds,
            "source": "ui_selection_control",
        }

    media_bounds = parse_bounds(media.bounds)
    if not media_bounds:
        if media.center_x is None or media.center_y is None:
            raise RuntimeError(f"{STEP_NAME}: first media item has no tappable center.")
        return {
            "x": media.center_x,
            "y": media.center_y,
            "bounds": media.bounds,
            "source": "ui_grid_center",
        }

    left, top, right, bottom = media_bounds
    x_ratio = float(picker_config.get("selection_x_ratio", 0.865))
    y_ratio = float(picker_config.get("selection_y_ratio", 0.135))
    return {
        "x": int(left + (right - left) * x_ratio),
        "y": int(top + (bottom - top) * y_ratio),
        "bounds": media.bounds,
        "source": "inferred_selection_control",
    }


def selection_control_for_media(nodes: list[UINode], media: UINode, picker_config: dict[str, Any]) -> UINode | None:
    media_bounds = parse_bounds(media.bounds)
    if not media_bounds:
        return None
    left, top, right, bottom = media_bounds
    media_width = right - left
    media_height = bottom - top
    max_size = int(picker_config.get("selection_control_max_size", 180))

    controls: list[tuple[int, int, UINode]] = []
    for node in nodes:
        bounds = parse_bounds(node.bounds)
        if not bounds or node.clickable != "true" or node.center_x is None or node.center_y is None:
            continue
        node_left, node_top, node_right, node_bottom = bounds
        width = node_right - node_left
        height = node_bottom - node_top
        if width > max_size or height > max_size:
            continue
        if not (left <= node.center_x <= right and top <= node.center_y <= bottom):
            continue
        if node.center_x < left + media_width * 0.55:
            continue
        if node.center_y > top + media_height * 0.45:
            continue
        score = 0
        if "Button" in node.class_name:
            score += 3
        if node.resource_id:
            score += 1
        score += int(node.center_x - left)
        score -= int(node.center_y - top)
        controls.append((-score, node_top, node))

    if not controls:
        return None
    controls.sort(key=lambda item: (item[0], item[1]))
    return controls[0][2]


def point_inside_grid(x: int, y: int, bounds: tuple[int, int, int, int]) -> bool:
    left, top, right, bottom = bounds
    return left <= x <= right and top <= y <= bottom


if __name__ == "__main__":
    raise SystemExit(main())

