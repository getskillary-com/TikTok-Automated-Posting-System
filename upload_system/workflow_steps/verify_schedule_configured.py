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
    scheduled_publish_page_signal,
    write_trace,
)


STEP_NAME = "verify_schedule_configured"


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify that TikTok Studio is set to scheduled publish.")
    add_step_args(parser)
    args = parser.parse_args()

    config = load_step_config(args.config)
    state = load_or_create_state(args, config)
    if not state.get("schedule_configured", False):
        raise RuntimeError(f"{STEP_NAME}: set_schedule_datetime has not marked the schedule as configured.")

    adb = make_adb(config)
    native_config = config["pipeline"].get("native_schedule", {})
    attempts = int(native_config.get("configured_wait_attempts", 6))
    wait_seconds = float(native_config.get("configured_wait_seconds", 0.75))
    require_target_text = bool(native_config.get("require_target_text_after_confirm", True))
    time_tolerance_minutes = int(native_config.get("configured_time_tolerance_minutes", 30))
    target_date = str(state.get("schedule_date") or "")
    target_time = str(state.get("schedule_time") or "")
    target_timezone = str(
        state.get("schedule_timezone")
        or config["pipeline"].get("schedule", {}).get("timezone")
        or ""
    )

    last_trace: dict[str, Any] | None = None
    for attempt in range(1, attempts + 1):
        screen = capture_screen(
            adb=adb,
            state=state,
            step_name=f"{STEP_NAME}_{attempt:02d}",
            config=config,
        )
        picker = native_schedule_picker_signal(screen)
        publish_state = scheduled_publish_page_signal(
            screen,
            target_date_iso=target_date,
            target_time_24=target_time,
            target_timezone=target_timezone,
            time_tolerance_minutes=time_tolerance_minutes,
        )
        trace = {
            "action": "verify_schedule_configured",
            "attempt": attempt,
            "picker": picker,
            "publish_state": publish_state,
            "require_target_text": require_target_text,
            "time_tolerance_minutes": time_tolerance_minutes,
            "target_date": target_date,
            "target_time": target_time,
            "target_timezone": target_timezone,
            "screenshot": str(screen["screenshot_path"]),
        }
        last_trace = trace
        target_ok = publish_state["has_target_text"] or not require_target_text
        if not picker["is_open"] and publish_state["has_scheduled_final_button"] and target_ok:
            write_trace(state, STEP_NAME, trace)
            mark_step_done(
                state,
                STEP_NAME,
                schedule_configured_verified=True,
                last_decision=trace,
            )
            if publish_state.get("time_tolerance", {}).get("matched") and not publish_state.get("has_exact_target_time"):
                actual = publish_state["time_tolerance"].get("actual", {})
                diff = publish_state["time_tolerance"].get("diff_minutes")
                print(
                    f"{STEP_NAME}: scheduled publish state verified "
                    f"with {diff:.1f} minute tolerance; actual {actual.get('date_iso')} {actual.get('time_24')}"
                )
            else:
                print(f"{STEP_NAME}: scheduled publish state verified")
            return 0
        adb.wait(wait_seconds)

    failure = last_trace or {"action": "verify_schedule_configured", "attempt": 0}
    failure["reason"] = failure_reason(failure, require_target_text=require_target_text)
    write_trace(state, STEP_NAME, failure)
    raise RuntimeError(f"{STEP_NAME}: {failure['reason']}")


def failure_reason(trace: dict[str, Any], *, require_target_text: bool) -> str:
    picker = trace.get("picker", {})
    publish_state = trace.get("publish_state", {})
    if picker.get("is_open"):
        return "Schedule picker is still open after confirming the wheel."
    if publish_state.get("has_direct_publish_button"):
        return "Final button is still direct publish, so scheduling is not enabled."
    if not publish_state.get("has_scheduled_final_button"):
        return "Could not find a scheduled-publish final button."
    if require_target_text and not publish_state.get("has_target_text"):
        tolerance = publish_state.get("time_tolerance", {})
        if tolerance.get("enabled"):
            actual = tolerance.get("actual")
            if actual:
                return (
                    "Scheduled publish page time is outside the accepted tolerance: "
                    f"expected {tolerance.get('target')}, actual {actual}, "
                    f"diff_minutes={tolerance.get('diff_minutes')}."
                )
        return "Scheduled publish page does not show the expected target date/time."
    return "Scheduled publish state could not be verified."


if __name__ == "__main__":
    raise SystemExit(main())
