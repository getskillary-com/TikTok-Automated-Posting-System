from __future__ import annotations

import argparse

from common import (
    add_step_args,
    capture_screen,
    load_caption,
    load_or_create_state,
    load_step_config,
    make_adb,
    mark_step_done,
    paste_text,
    publish_form_signal,
    require_video_path,
    tap_text_target,
    write_trace,
)


STEP_NAME = "paste_caption"


def main() -> int:
    parser = argparse.ArgumentParser(description="Choose the video's preset caption and paste it.")
    add_step_args(parser)
    parser.add_argument("--caption-file", default="", help="Caption preset JSON path.")
    parser.add_argument("--caption-preset", default="", help="Named caption preset key.")
    parser.add_argument("--caption", default="", help="Direct caption text override.")
    args = parser.parse_args()

    config = load_step_config(args.config)
    state = load_or_create_state(args, config)
    adb = make_adb(config)
    video_path = require_video_path(state)
    caption_file = args.caption_file or state.get("caption_file") or config["pipeline"]["caption_file"]
    caption = load_caption(
        state=state,
        video_path=video_path,
        caption_file=caption_file,
        preset=args.caption_preset,
        direct_caption=args.caption or state.get("caption", ""),
    )
    caption, appended_trailing_space = caption_with_trailing_space(caption)

    decision = tap_text_target(
        adb=adb,
        state=state,
        config=config,
        step_name=STEP_NAME,
        texts=config["pipeline"]["targets"]["caption_field"],
        allow_fallback=config["pipeline"]["fallbacks"]["caption_field"],
    )
    decision["caption_trailing_space_appended"] = appended_trailing_space
    paste_text(adb, caption, config, dry_run=bool(state.get("dry_run", False)))
    paste_config = config["pipeline"]["paste"]
    paste_mode = str(paste_config.get("mode") or "adb_keyboard")
    hide_keyboard = should_hide_keyboard_after_paste(
        paste_config=paste_config,
        paste_mode=paste_mode,
        dry_run=bool(state.get("dry_run", False)),
    )
    decision["paste_mode"] = paste_mode
    decision["hide_keyboard_after_paste"] = bool(hide_keyboard)
    if hide_keyboard:
        adb.wait(0.5)
        adb.shell("input", "keyevent", "4", timeout=15)
        adb.wait(float(config["pipeline"]["post_step_delay_seconds"]))
    after_screen = capture_screen(
        adb=adb,
        state=state,
        step_name=f"{STEP_NAME}_after_paste",
        config=config,
    )
    after_signal = publish_form_signal(after_screen)
    decision["post_paste_publish_form_signal"] = after_signal
    decision["post_paste_screenshot"] = str(after_screen["screenshot_path"])
    after_screen, after_signal = select_first_keyword_suggestion_if_present(
        adb=adb,
        state=state,
        config=config,
        screen=after_screen,
        decision=decision,
    )
    if not after_signal["looks_like_publish_form"]:
        decision["reason"] = "Caption paste did not leave the workflow on the publish form."
        write_trace(state, STEP_NAME, decision)
        raise RuntimeError(f"{STEP_NAME}: {decision['reason']}")
    write_trace(state, STEP_NAME, decision)
    mark_step_done(
        state,
        STEP_NAME,
        last_decision=decision,
        caption=caption,
        caption_file=str(caption_file),
        caption_preset=args.caption_preset or state.get("caption_preset", ""),
    )
    return 0


def should_hide_keyboard_after_paste(*, paste_config: dict, paste_mode: str, dry_run: bool) -> bool:
    if dry_run or not paste_config.get("hide_keyboard_after_paste", True):
        return False
    if paste_mode == "adb_keyboard":
        return bool(paste_config.get("hide_keyboard_after_adb_keyboard", True))
    return True


def caption_with_trailing_space(caption: str) -> tuple[str, bool]:
    if caption and not caption[-1].isspace():
        return f"{caption} ", True
    return caption, False


def select_first_keyword_suggestion_if_present(
    *,
    adb,
    state: dict,
    config: dict,
    screen: dict,
    decision: dict,
) -> tuple[dict, dict]:
    paste_config = config["pipeline"]["paste"]
    if not paste_config.get("select_first_keyword_suggestion_after_paste", False):
        decision["keyword_suggestion"] = {
            "action": "skip",
            "reason": "Keyword suggestion handling is disabled.",
        }
        return screen, publish_form_signal(screen)

    suggestion = find_first_keyword_suggestion(screen, paste_config)
    if suggestion is None:
        decision["keyword_suggestion"] = {
            "action": "not_found",
            "reason": "No hashtag-style keyword suggestion was visible.",
        }
        return screen, publish_form_signal(screen)

    decision["keyword_suggestion"] = {
        "action": "tap",
        "x": suggestion.center_x,
        "y": suggestion.center_y,
        "label": suggestion.text,
        "source": "ui_hashtag_suggestion",
        "bounds": suggestion.bounds,
        "reason": "Selected the first visible hashtag suggestion after caption paste.",
    }
    if not state.get("dry_run", False):
        adb.tap(int(suggestion.center_x), int(suggestion.center_y))
    adb.wait(float(paste_config.get("keyword_suggestion_wait_seconds", config["pipeline"]["post_step_delay_seconds"])))
    updated_screen = capture_screen(
        adb=adb,
        state=state,
        step_name=f"{STEP_NAME}_after_keyword_suggestion",
        config=config,
    )
    updated_signal = publish_form_signal(updated_screen)
    decision["post_keyword_suggestion_publish_form_signal"] = updated_signal
    decision["post_keyword_suggestion_screenshot"] = str(updated_screen["screenshot_path"])
    return updated_screen, updated_signal


def find_first_keyword_suggestion(screen: dict, paste_config: dict):
    width, height = screen["screen_size"]
    min_y = int(height * float(paste_config.get("keyword_suggestion_min_y_ratio", 0.32)))
    max_y = int(height * float(paste_config.get("keyword_suggestion_max_y_ratio", 0.93)))
    max_x = int(width * 0.95)
    candidates = []
    for node in screen["nodes"]:
        if node.center_x is None or node.center_y is None:
            continue
        text = (node.text or "").strip()
        if not text.startswith("#"):
            continue
        if node.center_y < min_y or node.center_y > max_y or node.center_x > max_x:
            continue
        if "EditText" in node.class_name:
            continue
        candidates.append((node.center_y, node.center_x, node))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[0][2]


if __name__ == "__main__":
    raise SystemExit(main())
