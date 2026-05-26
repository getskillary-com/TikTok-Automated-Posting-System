from __future__ import annotations

import argparse

from common import (
    add_step_args,
    capture_screen,
    load_or_create_state,
    load_step_config,
    make_adb,
    mark_step_done,
    infer_native_schedule_initial_from_phone_clock,
    native_schedule_picker_signal,
    parse_native_schedule_display,
    parse_native_schedule_screen,
    publish_form_signal,
    read_phone_clock,
    tap_ratio,
    tap_text_target,
    normalize_text,
    visible_text_from_nodes,
    write_trace,
)


STEP_NAME = "open_schedule_settings"


def main() -> int:
    parser = argparse.ArgumentParser(description="Open TikTok Studio native scheduling settings.")
    add_step_args(parser)
    args = parser.parse_args()

    config = load_step_config(args.config)
    state = load_or_create_state(args, config)
    adb = make_adb(config)
    timezone_name = state.get("schedule_timezone") or config["pipeline"]["schedule"]["timezone"]
    native_config = config["pipeline"].get("native_schedule", {})
    wheel_config = native_config.get("wheel", {})
    use_phone_clock = str(native_config.get("strategy", "")) == "phone_clock_wheel"

    schedule_entry_fallback = config["pipeline"].get("fallbacks", {}).get("schedule_entry")
    preflight = schedule_entry_preflight(adb=adb, state=state, config=config)
    decision, phone_clock_before_open = open_schedule_picker(
        adb=adb,
        state=state,
        config=config,
        preflight=preflight,
        schedule_entry_fallback=schedule_entry_fallback,
        native_config=native_config,
        wheel_config=wheel_config,
        use_phone_clock=use_phone_clock,
        timezone_name=str(timezone_name),
    )
    decision["preflight"] = preflight
    if use_phone_clock:
        assert phone_clock_before_open is not None
        initial_schedule = infer_native_schedule_initial_from_phone_clock(phone_clock_before_open, wheel_config)
        decision["phone_clock_before_open"] = phone_clock_before_open
        decision["native_schedule_initial_source"] = "phone_clock"
    else:
        initial_schedule = parse_native_schedule_display(decision.get("recognized_text", ""), str(timezone_name))
    if initial_schedule is None and not use_phone_clock:
        screen = capture_screen(
            adb=adb,
            state=state,
            step_name=f"{STEP_NAME}_after_tap",
            config=config,
        )
        initial_schedule = parse_native_schedule_screen(screen, config, str(timezone_name))
    decision["native_schedule_initial"] = initial_schedule
    write_trace(state, STEP_NAME, decision)
    mark_step_done(state, STEP_NAME, last_decision=decision, native_schedule_initial=initial_schedule)
    return 0


def open_schedule_picker(
    *,
    adb: object,
    state: dict,
    config: dict,
    preflight: dict,
    schedule_entry_fallback: dict | None,
    native_config: dict,
    wheel_config: dict,
    use_phone_clock: bool,
    timezone_name: str,
) -> tuple[dict, dict | None]:
    prefer_fallback = bool(native_config.get("prefer_schedule_entry_fallback") and schedule_entry_fallback)
    max_attempts = max(1, int(native_config.get("schedule_entry_open_attempts", 2)))
    attempts: list[dict] = []
    last_decision: dict | None = None
    last_phone_clock: dict | None = None

    for attempt in range(1, max_attempts + 1):
        step_name = STEP_NAME if attempt == 1 else f"{STEP_NAME}_attempt_{attempt}"
        phone_clock = guarded_phone_clock(adb, timezone_name, wheel_config) if use_phone_clock else None
        decision = tap_schedule_entry(
            adb=adb,
            state=state,
            config=config,
            step_name=step_name,
            schedule_entry_fallback=schedule_entry_fallback,
            prefer_fallback=prefer_fallback,
        )
        after_screen = capture_screen(
            adb=adb,
            state=state,
            step_name=f"{STEP_NAME}_after_tap_{attempt:02d}",
            config=config,
        )
        picker = native_schedule_picker_signal(after_screen)
        form = publish_form_signal(after_screen)
        attempt_record = {
            "attempt": attempt,
            "tap": decision,
            "picker": picker,
            "publish_form_signal": form,
            "screenshot": str(after_screen["screenshot_path"]),
        }
        attempts.append(attempt_record)
        last_decision = dict(decision)
        last_decision["open_attempts"] = attempts
        last_decision["preflight"] = preflight
        if phone_clock:
            last_decision["phone_clock_before_open"] = phone_clock
        if picker["is_open"]:
            last_decision["reason"] = "Schedule picker opened after tapping the schedule row."
            write_trace(state, STEP_NAME, last_decision)
            return last_decision, phone_clock
        if not form["looks_like_publish_form"]:
            last_decision["reason"] = "Schedule row tap left the publish form without opening the picker."
            write_trace(state, STEP_NAME, last_decision)
            raise RuntimeError(f"{STEP_NAME}: {last_decision['reason']}")
        last_phone_clock = phone_clock

    failure = last_decision or {
        "action": "manual_review",
        "x": None,
        "y": None,
        "label": "",
        "matched_text": "",
        "confidence": 0.0,
        "source": "",
        "is_final_publish": False,
    }
    failure["open_attempts"] = attempts
    failure["preflight"] = preflight
    failure["reason"] = "Schedule picker did not open after verified schedule-row taps."
    write_trace(state, STEP_NAME, failure)
    raise RuntimeError(f"{STEP_NAME}: {failure['reason']}")


def guarded_phone_clock(adb: object, timezone_name: str, wheel_config: dict) -> dict:
    phone_clock = read_phone_clock(adb, timezone_name)
    guard_seconds = int(wheel_config.get("clock_boundary_guard_seconds", 5))
    second = int(phone_clock.get("second") or 0)
    if guard_seconds > 0 and second >= 60 - guard_seconds:
        wait_seconds = (60 - second) + 1
        print(f"{STEP_NAME}: phone clock near minute boundary; waiting {wait_seconds}s before opening picker.")
        adb.wait(wait_seconds)
        phone_clock = read_phone_clock(adb, timezone_name)
    return phone_clock


def tap_schedule_entry(
    *,
    adb: object,
    state: dict,
    config: dict,
    step_name: str,
    schedule_entry_fallback: dict | None,
    prefer_fallback: bool,
) -> dict:
    if prefer_fallback and schedule_entry_fallback:
        decision = tap_ratio(
            adb=adb,
            state=state,
            config=config,
            step_name=step_name,
            x_ratio=float(schedule_entry_fallback["x_ratio"]),
            y_ratio=float(schedule_entry_fallback["y_ratio"]),
            label="schedule_entry",
        )
        decision["source"] = "profile_fallback"
        decision["reason"] = "Used calibrated schedule entry coordinate before UI text matching."
        return decision
    return tap_text_target(
        adb=adb,
        state=state,
        config=config,
        step_name=step_name,
        texts=config["pipeline"]["targets"]["schedule_entry"],
        allow_fallback=schedule_entry_fallback,
    )


def schedule_entry_preflight(*, adb: object, state: dict, config: dict) -> dict:
    screen = capture_screen(
        adb=adb,
        state=state,
        step_name=f"{STEP_NAME}_preflight",
        config=config,
    )
    visible_text = visible_text_from_nodes(screen["nodes"])
    normalized_visible = normalize_text(visible_text)
    targets = config["pipeline"]["targets"]["schedule_entry"]
    matched_terms = [
        target
        for target in targets
        if normalize_text(str(target)) and normalize_text(str(target)) in normalized_visible
    ]
    structural_publish_page = publish_form_signal(screen)
    schedule_entry_fallback = config["pipeline"].get("fallbacks", {}).get("schedule_entry")
    trace = {
        "action": "schedule_entry_preflight",
        "has_schedule_entry": bool(matched_terms),
        "matched_terms": matched_terms,
        "structural_publish_page": structural_publish_page,
        "has_schedule_entry_fallback": bool(schedule_entry_fallback),
        "screenshot": str(screen["screenshot_path"]),
    }
    write_trace(state, f"{STEP_NAME}_preflight", trace)
    if not matched_terms and not (schedule_entry_fallback and structural_publish_page["looks_like_publish_form"]):
        trace["reason"] = "Publish page schedule entry is not visible; refusing to tap the calibrated schedule coordinate."
        write_trace(state, f"{STEP_NAME}_preflight", trace)
        raise RuntimeError(f"{STEP_NAME}: {trace['reason']}")
    if not matched_terms:
        trace["reason"] = "Schedule text was not readable, but publish-page structure and calibrated fallback are present."
        trace["allowed_by_structural_fallback"] = True
        write_trace(state, f"{STEP_NAME}_preflight", trace)
    return trace

if __name__ == "__main__":
    raise SystemExit(main())
