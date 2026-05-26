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
    native_schedule_picker_signal,
    parse_native_schedule_screen,
    write_trace,
)


STEP_NAME = "wait_schedule_picker_open"


def main() -> int:
    parser = argparse.ArgumentParser(description="Wait until TikTok Studio's native schedule picker is open.")
    add_step_args(parser)
    args = parser.parse_args()

    config = load_step_config(args.config)
    state = load_or_create_state(args, config)
    adb = make_adb(config)
    native_config = config["pipeline"].get("native_schedule", {})
    timezone_name = state.get("schedule_timezone") or config["pipeline"]["schedule"]["timezone"]
    attempts = int(native_config.get("picker_open_wait_attempts", 8))
    wait_seconds = float(native_config.get("picker_open_wait_seconds", 0.75))

    last_trace: dict[str, Any] | None = None
    for attempt in range(1, attempts + 1):
        screen = capture_screen(
            adb=adb,
            state=state,
            step_name=f"{STEP_NAME}_{attempt:02d}",
            config=config,
        )
        signal = native_schedule_picker_signal(screen)
        trace = {
            "action": "wait_schedule_picker_open",
            "attempt": attempt,
            "picker": signal,
            "screenshot": str(screen["screenshot_path"]),
        }
        last_trace = trace
        if signal["is_open"]:
            if not state.get("native_schedule_initial"):
                initial = parse_native_schedule_screen(screen, config, str(timezone_name))
                if initial:
                    state["native_schedule_initial"] = initial
                    trace["native_schedule_initial"] = initial
            write_trace(state, STEP_NAME, trace)
            mark_step_done(state, STEP_NAME, schedule_picker_open=True, last_decision=trace)
            print(f"{STEP_NAME}: schedule picker is open")
            return 0
        adb.wait(wait_seconds)

    failure = last_trace or {"action": "wait_schedule_picker_open", "attempt": 0}
    failure["reason"] = "Schedule picker did not open after tapping the schedule row."
    write_trace(state, STEP_NAME, failure)
    raise RuntimeError(f"{STEP_NAME}: {failure['reason']}")


if __name__ == "__main__":
    raise SystemExit(main())
