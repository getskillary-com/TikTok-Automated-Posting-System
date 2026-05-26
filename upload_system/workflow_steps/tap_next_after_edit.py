from __future__ import annotations

import argparse

from common import (
    add_step_args,
    capture_screen,
    load_or_create_state,
    load_step_config,
    make_adb,
    mark_step_done,
    publish_form_signal,
    tap_text_target,
    write_trace,
)


STEP_NAME = "tap_next_after_edit"


def main() -> int:
    parser = argparse.ArgumentParser(description="Find and tap Next/Continue on the edit screen.")
    add_step_args(parser)
    args = parser.parse_args()

    config = load_step_config(args.config)
    state = load_or_create_state(args, config)
    adb = make_adb(config)

    decision = tap_next_and_verify(adb=adb, state=state, config=config)
    mark_step_done(state, STEP_NAME, last_decision=decision)
    return 0


def tap_next_and_verify(*, adb: object, state: dict, config: dict) -> dict:
    before = capture_screen(
        adb=adb,
        state=state,
        step_name=f"{STEP_NAME}_before_verify",
        config=config,
    )
    before_signal = publish_form_signal(before)
    if before_signal["looks_like_publish_form"]:
        decision = {
            "action": "already_on_publish_form",
            "x": None,
            "y": None,
            "label": "",
            "matched_text": "",
            "confidence": 1.0,
            "source": "publish_form_signal",
            "is_final_publish": False,
            "before_signal": before_signal,
            "reason": "Publish form was already visible before tapping edit-screen Next.",
        }
        write_trace(state, STEP_NAME, decision)
        return decision

    attempts = []
    last_decision: dict | None = None
    verify_attempts = int(config["pipeline"].get("next_after_edit_verify_attempts", 3))
    verify_wait = float(config["pipeline"].get("next_after_edit_verify_wait_seconds", 2.0))
    for attempt in range(1, max(1, verify_attempts) + 1):
        step_name = STEP_NAME if attempt == 1 else f"{STEP_NAME}_attempt_{attempt}"
        tap_decision = tap_text_target(
            adb=adb,
            state=state,
            config=config,
            step_name=step_name,
            texts=config["pipeline"]["targets"]["next"],
            allow_fallback=config["pipeline"].get("fallbacks", {}).get("next_after_edit"),
        )
        adb.wait(verify_wait)
        after = capture_screen(
            adb=adb,
            state=state,
            step_name=f"{STEP_NAME}_after_attempt_{attempt}",
            config=config,
        )
        signal = publish_form_signal(after)
        attempt_record = {
            "attempt": attempt,
            "tap": tap_decision,
            "publish_form_signal": signal,
            "screenshot": str(after["screenshot_path"]),
        }
        attempts.append(attempt_record)
        last_decision = dict(tap_decision)
        last_decision["attempt"] = attempt
        last_decision["attempts"] = attempts
        last_decision["before_signal"] = before_signal
        last_decision["post_tap_publish_form_signal"] = signal
        if signal["looks_like_publish_form"]:
            last_decision["reason"] = "Edit-screen Next reached the publish form."
            write_trace(state, STEP_NAME, last_decision)
            return last_decision

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
    failure["attempts"] = attempts
    failure["before_signal"] = before_signal
    failure["reason"] = "Edit-screen Next did not reach the publish form after verified retries."
    write_trace(state, STEP_NAME, failure)
    raise RuntimeError(f"{STEP_NAME}: {failure['reason']}")


if __name__ == "__main__":
    raise SystemExit(main())
