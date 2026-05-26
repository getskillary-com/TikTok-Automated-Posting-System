from __future__ import annotations

import argparse

from common import (
    add_step_args,
    load_or_create_state,
    load_step_config,
    make_adb,
    mark_step_done,
    resolve_publish_schedule,
    wait_until_datetime,
    write_trace,
)


STEP_NAME = "wait_until_publish_time"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Wait until the configured publish time before tapping the final publish button."
    )
    add_step_args(parser)
    args = parser.parse_args()

    config = load_step_config(args.config)
    state = load_or_create_state(args, config)
    dry_run = bool(state.get("dry_run", False))
    schedule_config = config["pipeline"].get("schedule", {})
    schedule = resolve_publish_schedule(state, config)
    max_wait_seconds = float(schedule_config.get("max_wait_seconds", 86400))

    decision = {
        "action": "wait_until_publish_time",
        "timezone": schedule["timezone"],
        "target_iso": schedule["target_iso"],
        "date_iso": schedule["date_iso"],
        "time_24": schedule["time_24"],
        "time_12": schedule["time_12"],
        "seconds_until": schedule["seconds_until"],
        "max_wait_seconds": max_wait_seconds,
        "dry_run": dry_run,
    }
    write_trace(state, STEP_NAME, decision)

    if schedule["seconds_until"] > max_wait_seconds:
        raise RuntimeError(
            f"{STEP_NAME}: target time is {int(schedule['seconds_until'])}s away, "
            f"which exceeds pipeline.schedule.max_wait_seconds={int(max_wait_seconds)}."
        )

    print(
        f"{STEP_NAME}: target publish time is "
        f"{schedule['date_iso']} {schedule['time_24']} {schedule['timezone']}"
    )

    adb = None
    keep_awake_keyevent = str(schedule_config.get("keep_awake_keyevent", ""))
    if keep_awake_keyevent and not dry_run:
        adb = make_adb(config)

    wait_until_datetime(
        target=schedule["target"],
        timezone_name=schedule["timezone"],
        poll_seconds=float(schedule_config.get("poll_seconds", 30)),
        dry_run=dry_run,
        adb=adb,
        keep_awake_keyevent=keep_awake_keyevent,
    )

    mark_step_done(
        state,
        STEP_NAME,
        last_decision=decision,
        scheduled_at=schedule["target_iso"],
        schedule_date=schedule["date_iso"],
        schedule_time=schedule["time_24"],
        schedule_timezone=schedule["timezone"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
