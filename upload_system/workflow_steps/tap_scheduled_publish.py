from __future__ import annotations

import argparse

from common import (
    add_step_args,
    capture_screen,
    load_or_create_state,
    load_step_config,
    make_adb,
    mark_step_done,
    scheduled_publish_page_signal,
    tap_text_target,
    write_trace,
)


STEP_NAME = "tap_scheduled_publish"
SCHEDULED_PUBLISH_TEXTS = [
    "Schedule",
    "Schedule post",
    "Schedule video",
    "\u9884\u7ea6\u53d1\u5e03",
    "\u5b9a\u65f6\u53d1\u5e03",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Tap the final scheduled publish button.")
    add_step_args(parser)
    args = parser.parse_args()

    config = load_step_config(args.config)
    state = load_or_create_state(args, config)
    if not state.get("schedule_configured", False):
        raise RuntimeError(f"{STEP_NAME}: schedule is not configured, refusing to tap the final publish button.")
    if not state.get("schedule_configured_verified", False):
        raise RuntimeError(f"{STEP_NAME}: schedule was not verified, refusing to tap the final publish button.")

    adb = make_adb(config)
    screen = capture_screen(adb=adb, state=state, step_name=f"{STEP_NAME}_preflight", config=config)
    publish_state = scheduled_publish_page_signal(
        screen,
        target_date_iso=str(state.get("schedule_date") or ""),
        target_time_24=str(state.get("schedule_time") or ""),
    )
    preflight = {
        "action": "scheduled_publish_preflight",
        "publish_state": publish_state,
        "screenshot": str(screen["screenshot_path"]),
    }
    write_trace(state, f"{STEP_NAME}_preflight", preflight)
    if not publish_state["has_scheduled_final_button"]:
        raise RuntimeError(f"{STEP_NAME}: final button is not in scheduled-publish state.")
    if publish_state["has_direct_publish_button"]:
        raise RuntimeError(f"{STEP_NAME}: final button is still direct publish; refusing to tap.")

    decision = tap_text_target(
        adb=adb,
        state=state,
        config=config,
        step_name=STEP_NAME,
        texts=config["pipeline"]["targets"].get("schedule_publish_confirmed", SCHEDULED_PUBLISH_TEXTS),
        allow_fallback=config["pipeline"].get("fallbacks", {}).get("schedule_publish"),
        final_publish=True,
    )
    mark_step_done(state, STEP_NAME, last_decision=decision)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
