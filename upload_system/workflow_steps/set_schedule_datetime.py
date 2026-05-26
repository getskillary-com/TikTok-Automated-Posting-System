from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from typing import Any

from common import (
    add_step_args,
    apply_schedule_lead_window,
    escape_adb_input_text,
    capture_screen,
    load_or_create_state,
    load_step_config,
    load_timezone,
    make_adb,
    mark_step_done,
    infer_native_schedule_initial_from_phone_clock,
    native_schedule_picker_signal,
    parse_native_schedule_screen,
    read_phone_clock,
    resolve_publish_schedule,
    save_state,
    tap_ratio,
    tap_text_target,
    write_trace,
)


STEP_NAME = "set_schedule_datetime"


def main() -> int:
    parser = argparse.ArgumentParser(description="Set TikTok Studio native scheduled publish date/time.")
    add_step_args(parser)
    args = parser.parse_args()

    config = load_step_config(args.config)
    state = load_or_create_state(args, config)
    adb = make_adb(config)
    schedule = resolve_publish_schedule(state, config)
    native_config = config["pipeline"].get("native_schedule", {})
    if native_config.get("strategy", "wheel") in {"wheel", "phone_clock_wheel"}:
        trace = run_wheel_strategy(adb=adb, state=state, config=config, schedule=schedule, native_config=native_config)
        verification_schedule = schedule_state_for_verified_page(schedule, trace)
        trace["page_verification_target"] = verification_schedule
        write_trace(state, STEP_NAME, trace)
        mark_step_done(
            state,
            STEP_NAME,
            last_decision=trace,
            schedule_configured=True,
            scheduled_at=verification_schedule["target_iso"],
            schedule_date=verification_schedule["date_iso"],
            schedule_time=verification_schedule["time_24"],
            schedule_timezone=verification_schedule["timezone"],
            schedule_auto_extensions=trace.get("auto_extensions", schedule.get("auto_extensions", [])),
        )
        return 0

    actions = native_config.get("actions", [])
    if not actions:
        raise RuntimeError(
            f"{STEP_NAME}: no native schedule actions are configured. "
            "Add pipeline.native_schedule.actions after capturing the schedule settings screen."
        )

    context = {"schedule": schedule}
    decisions: list[dict[str, Any]] = []
    for index, action in enumerate(actions, start=1):
        decision = run_action(
            adb=adb,
            state=state,
            config=config,
            action=action,
            index=index,
            context=context,
        )
        decisions.append(decision)

    trace = {
        "action": "set_native_schedule_datetime",
        "target_iso": schedule["target_iso"],
        "date_iso": schedule["date_iso"],
        "date_slash": schedule["date_slash"],
        "date_day_first": schedule["date_day_first"],
        "time_24": schedule["time_24"],
        "time_12": schedule["time_12"],
        "timezone": schedule["timezone"],
        "actions": decisions,
        "auto_extensions": schedule.get("auto_extensions", []),
    }
    write_trace(state, STEP_NAME, trace)
    mark_step_done(
        state,
        STEP_NAME,
        last_decision=trace,
        schedule_configured=True,
        scheduled_at=schedule["target_iso"],
        schedule_date=schedule["date_iso"],
        schedule_time=schedule["time_24"],
        schedule_timezone=schedule["timezone"],
        schedule_auto_extensions=schedule.get("auto_extensions", []),
    )
    return 0


def run_wheel_strategy(
    *,
    adb: Any,
    state: dict[str, Any],
    config: dict[str, Any],
    schedule: dict[str, Any],
    native_config: dict[str, Any],
) -> dict[str, Any]:
    strategy = str(native_config.get("strategy", "wheel"))
    phone_clock_strategy = strategy == "phone_clock_wheel"
    wheel_config = native_config.get("wheel", {})
    initial = state.get("native_schedule_initial")
    if phone_clock_strategy and (not initial or initial.get("source") != "phone_clock"):
        phone_clock = read_phone_clock(adb, schedule["timezone"])
        schedule = apply_schedule_lead_window(
            schedule,
            now=datetime.fromisoformat(str(phone_clock["iso"])),
            schedule_config=config["pipeline"].get("schedule", {}),
            source="phone_clock",
        )
        if schedule.get("auto_extension"):
            state["scheduled_at"] = schedule["target_iso"]
            state["schedule_date"] = schedule["date_iso"]
            state["schedule_time"] = schedule["time_24"]
            state["schedule_timezone"] = schedule["timezone"]
            state["schedule_auto_extensions"] = schedule.get("auto_extensions", [])
            save_state(state)
        initial = infer_native_schedule_initial_from_phone_clock(phone_clock, wheel_config)
        state["native_schedule_initial"] = initial
        state["native_schedule_phone_clock"] = phone_clock
        save_state(state)
    elif not initial:
        recovery_screen = capture_screen(
            adb=adb,
            state=state,
            step_name=f"{STEP_NAME}_initial_recovery",
            config=config,
        )
        initial = parse_native_schedule_screen(recovery_screen, config, schedule["timezone"])
        if initial:
            state["native_schedule_initial"] = initial
            save_state(state)
        else:
            raise RuntimeError(
                f"{STEP_NAME}: could not read the current native schedule time before opening the picker. "
                "Re-run open_schedule_settings.py from the publish page and try again."
            )

    dry_run = bool(state.get("dry_run", False))
    verify_after_confirm = bool(wheel_config.get("verify_after_confirm", not phone_clock_strategy))
    max_attempts = 1 if dry_run or not verify_after_confirm else max(1, 1 + int(wheel_config.get("verification_retries", 1)))
    current = initial
    attempts: list[dict[str, Any]] = []

    for attempt_number in range(1, max_attempts + 1):
        attempt = apply_wheel_adjustment(
            adb=adb,
            state=state,
            config=config,
            schedule=schedule,
            wheel_config=wheel_config,
            current=current,
            attempt_number=attempt_number,
            dry_run=dry_run,
            verify_after_confirm=verify_after_confirm,
        )
        attempts.append(attempt)

        confirmed = attempt.get("confirmed")
        if dry_run or attempt.get("matches_target"):
            break

        if not confirmed:
            raise RuntimeError(
                f"{STEP_NAME}: native schedule verification failed. "
                f"Expected {schedule['date_iso']} {schedule['time_24']}, but no schedule time was readable after confirm."
            )

        if attempt_number >= max_attempts:
            raise RuntimeError(
                f"{STEP_NAME}: native schedule verification failed after {attempt_number} attempt(s). "
                f"Expected {schedule['date_iso']} {schedule['time_24']}, got {confirmed}."
            )

        current = confirmed
        reopen_schedule_picker(adb=adb, state=state, config=config, attempt_number=attempt_number)

    confirmed = attempts[-1].get("confirmed")
    return {
        "action": "set_native_schedule_datetime_phone_clock_wheel" if phone_clock_strategy else "set_native_schedule_datetime_wheel",
        "strategy": strategy,
        "initial": initial,
        "target_iso": schedule["target_iso"],
        "date_iso": schedule["date_iso"],
        "time_24": schedule["time_24"],
        "timezone": schedule["timezone"],
        "attempts": attempts,
        "confirmed": confirmed,
        "verification": "disabled_phone_clock_strategy" if not verify_after_confirm else "ui_or_ocr_parse",
        "dry_run": dry_run,
        "auto_extensions": schedule.get("auto_extensions", []),
    }


def apply_wheel_adjustment(
    *,
    adb: Any,
    state: dict[str, Any],
    config: dict[str, Any],
    schedule: dict[str, Any],
    wheel_config: dict[str, Any],
    current: dict[str, Any],
    attempt_number: int,
    dry_run: bool,
    verify_after_confirm: bool,
) -> dict[str, Any]:
    picker_schedule = schedule_for_picker(schedule, current)
    target = picker_schedule["target"]
    deltas = compute_wheel_deltas(current=current, target=target)
    screen = capture_screen(
        adb=adb,
        state=state,
        step_name=f"{STEP_NAME}_wheel_before_{attempt_number}",
        config=config,
    )
    picker_signal = native_schedule_picker_signal(screen)
    if not picker_signal["is_open"]:
        failure = {
            "action": "manual_review",
            "reason": "Schedule picker is not open; refusing to apply wheel swipes on the current screen.",
            "attempt": attempt_number,
            "picker": picker_signal,
            "screenshot": str(screen["screenshot_path"]),
        }
        write_trace(state, f"{STEP_NAME}_picker_guard", failure)
        raise RuntimeError(f"{STEP_NAME}: {failure['reason']}")
    width, height = screen["screen_size"]
    moves = [
        ("date", deltas["date"], int(width * float(wheel_config.get("date_x_ratio", 0.214)))),
        ("hour", deltas["hour"], int(width * float(wheel_config.get("hour_x_ratio", 0.503)))),
        ("minute", deltas["minute"], int(width * float(wheel_config.get("minute_x_ratio", 0.789)))),
    ]

    performed: list[dict[str, Any]] = []
    for name, delta, x in moves:
        action = scroll_wheel(
            adb=adb,
            name=name,
            delta=delta,
            x=x,
            height=height,
            wheel_config=wheel_config,
            dry_run=dry_run,
        )
        performed.append(action)

    confirm_x = int(width * float(wheel_config.get("confirm_x_ratio", 0.66)))
    confirm_y = int(height * float(wheel_config.get("confirm_y_ratio", 0.942)))
    if dry_run:
        print(f"{STEP_NAME}: tap confirm [dry-run] ({confirm_x}, {confirm_y})")
        confirmed = None
    else:
        adb.shell("input", "tap", str(confirm_x), str(confirm_y), timeout=15)
        adb.wait(float(wheel_config.get("after_confirm_delay_seconds", 1.0)))
        if verify_after_confirm:
            after_screen = capture_screen(
                adb=adb,
                state=state,
                step_name=f"{STEP_NAME}_wheel_after_{attempt_number}",
                config=config,
            )
            confirmed = parse_native_schedule_screen(after_screen, config, picker_schedule["timezone"])
        else:
            confirmed = assumed_schedule_confirmation(picker_schedule)

    return {
        "attempt": attempt_number,
        "current": current,
        "picker_target": {
            "date_iso": picker_schedule["date_iso"],
            "time_24": picker_schedule["time_24"],
            "timezone": picker_schedule["timezone"],
            "target_iso": picker_schedule["target_iso"],
        },
        "deltas": deltas,
        "picker": picker_signal,
        "moves": performed,
        "confirm": {"x": confirm_x, "y": confirm_y},
        "confirmed": confirmed,
        "verify_after_confirm": verify_after_confirm,
        "matches_target": schedule_matches(confirmed, picker_schedule),
    }


def compute_wheel_deltas(*, current: dict[str, Any], target: Any) -> dict[str, int]:
    current_date = str(current["date_iso"])
    current_hour = int(current["hour"])
    current_minute = int(current["minute"])
    current_date_part = date.fromisoformat(current_date)
    return {
        "date": (target.date() - current_date_part).days,
        "hour": int(target.hour) - current_hour,
        "minute": int(target.minute) - current_minute,
    }


def schedule_for_picker(schedule: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    phone_timezone = str(current.get("phone_timezone") or current.get("timezone") or "")
    if not phone_timezone:
        return schedule
    target = schedule["target"].astimezone(load_timezone(phone_timezone))
    picker_schedule = dict(schedule)
    picker_schedule.update(
        {
            "timezone": phone_timezone,
            "target": target,
            "target_iso": target.isoformat(),
            "date_iso": target.strftime("%Y-%m-%d"),
            "time_24": target.strftime("%H:%M"),
        }
    )
    if phone_timezone != str(schedule.get("timezone") or ""):
        picker_schedule["source_timezone"] = schedule.get("timezone")
    return picker_schedule


def schedule_state_for_verified_page(schedule: dict[str, Any], trace: dict[str, Any]) -> dict[str, str]:
    attempts = trace.get("attempts") or []
    if attempts:
        picker_target = attempts[-1].get("picker_target") or {}
        confirmed = trace.get("confirmed")
        if picker_target and schedule_matches(confirmed, picker_target):
            return {
                "target_iso": str(picker_target.get("target_iso") or schedule["target_iso"]),
                "date_iso": str(picker_target.get("date_iso") or schedule["date_iso"]),
                "time_24": str(picker_target.get("time_24") or schedule["time_24"]),
                "timezone": str(picker_target.get("timezone") or schedule["timezone"]),
            }
    return {
        "target_iso": str(schedule["target_iso"]),
        "date_iso": str(schedule["date_iso"]),
        "time_24": str(schedule["time_24"]),
        "timezone": str(schedule["timezone"]),
    }


def assumed_schedule_confirmation(schedule: dict[str, Any]) -> dict[str, Any]:
    target = schedule["target"]
    return {
        "source": "assumed_no_ocr",
        "date_iso": schedule["date_iso"],
        "hour": int(target.hour),
        "minute": int(target.minute),
        "time_24": schedule["time_24"],
        "display": f"assumed:{schedule['date_iso']} {schedule['time_24']}",
    }


def schedule_matches(confirmed: dict[str, Any] | None, schedule: dict[str, Any]) -> bool:
    return bool(
        confirmed
        and confirmed.get("date_iso") == schedule["date_iso"]
        and confirmed.get("time_24") == schedule["time_24"]
    )


def reopen_schedule_picker(
    *,
    adb: Any,
    state: dict[str, Any],
    config: dict[str, Any],
    attempt_number: int,
) -> None:
    tap_text_target(
        adb=adb,
        state=state,
        config=config,
        step_name=f"{STEP_NAME}_retry_{attempt_number}_open",
        texts=config["pipeline"]["targets"]["schedule_entry"],
        allow_fallback=config["pipeline"].get("fallbacks", {}).get("schedule_entry"),
    )


def scroll_wheel(
    *,
    adb: Any,
    name: str,
    delta: int,
    x: int,
    height: int,
    wheel_config: dict[str, Any],
    dry_run: bool,
) -> dict[str, Any]:
    step_count = abs(delta)
    increase_start_y = int(height * float(wheel_config.get("increase_start_y_ratio", 0.774)))
    increase_end_y = int(height * float(wheel_config.get("increase_end_y_ratio", 0.734)))
    duration_ms = int(wheel_config.get("duration_ms", 150))
    pause_seconds = float(wheel_config.get("step_pause_seconds", 0.25))

    if delta > 0:
        direction = "increase_linear_no_wrap"
        start_y, end_y = increase_start_y, increase_end_y
    elif delta < 0:
        direction = "decrease_linear_no_wrap"
        start_y, end_y = increase_end_y, increase_start_y
    else:
        direction = "none"
        start_y, end_y = increase_start_y, increase_end_y

    for index in range(step_count):
        if dry_run:
            print(f"{STEP_NAME}: {name} wheel step {index + 1}/{step_count} [dry-run]")
        else:
            adb.shell(
                "input",
                "swipe",
                str(x),
                str(start_y),
                str(x),
                str(end_y),
                str(duration_ms),
                timeout=15,
            )
            adb.wait(pause_seconds)

    return {
        "name": name,
        "delta": delta,
        "direction": direction,
        "steps": step_count,
        "x": x,
        "start_y": start_y,
        "end_y": end_y,
        "duration_ms": duration_ms,
    }


def run_action(
    *,
    adb: Any,
    state: dict[str, Any],
    config: dict[str, Any],
    action: dict[str, Any],
    index: int,
    context: dict[str, Any],
) -> dict[str, Any]:
    action_type = str(action.get("type", "")).strip()
    step_name = f"{STEP_NAME}_{index:02d}_{action_type}"
    optional = bool(action.get("optional", False))
    dry_run = bool(state.get("dry_run", False))

    try:
        if action_type == "tap_text":
            target_key = str(action.get("target", ""))
            texts = action.get("texts") or config["pipeline"]["targets"].get(target_key, [])
            fallback = action.get("fallback") or config["pipeline"].get("fallbacks", {}).get(target_key)
            decision = tap_text_target(
                adb=adb,
                state=state,
                config=config,
                step_name=step_name,
                texts=list(texts),
                allow_fallback=fallback,
            )
            return {"type": action_type, "target": target_key, "decision": decision}

        if action_type == "tap_ratio":
            decision = tap_ratio(
                adb=adb,
                state=state,
                config=config,
                step_name=step_name,
                x_ratio=float(action["x_ratio"]),
                y_ratio=float(action["y_ratio"]),
                label=str(action.get("label", "schedule_ratio")),
            )
            return {"type": action_type, "decision": decision}

        if action_type == "input_text":
            value = format_template(str(action.get("value", "")), context)
            if dry_run:
                print(f"{step_name}: input_text [dry-run] {value!r}")
            else:
                adb.shell("input", "text", escape_adb_input_text(value), timeout=30)
            return {"type": action_type, "value": value}

        if action_type == "keyevent":
            keyevent = str(action["keyevent"])
            if dry_run:
                print(f"{step_name}: keyevent [dry-run] {keyevent}")
            else:
                adb.shell("input", "keyevent", keyevent, timeout=15)
            return {"type": action_type, "keyevent": keyevent}

        if action_type == "wait":
            seconds = float(action.get("seconds", 1.0))
            adb.wait(seconds)
            return {"type": action_type, "seconds": seconds}

        raise ValueError(f"Unsupported native schedule action type: {action_type!r}")
    except Exception as exc:
        if optional:
            print(f"{step_name}: optional action skipped: {exc}")
            return {"type": action_type, "optional": True, "skipped": True, "error": str(exc)}
        raise


def format_template(template: str, context: dict[str, Any]) -> str:
    schedule = context["schedule"]
    return template.format(**schedule)


if __name__ == "__main__":
    raise SystemExit(main())
