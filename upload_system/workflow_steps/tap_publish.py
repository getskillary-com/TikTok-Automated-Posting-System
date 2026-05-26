from __future__ import annotations

import argparse

from common import add_step_args, load_or_create_state, load_step_config, make_adb, mark_step_done, tap_text_target


STEP_NAME = "tap_publish"


def main() -> int:
    parser = argparse.ArgumentParser(description="Find and tap Publish/Post.")
    add_step_args(parser)
    args = parser.parse_args()

    config = load_step_config(args.config)
    state = load_or_create_state(args, config)
    adb = make_adb(config)

    try:
        decision = tap_text_target(
            adb=adb,
            state=state,
            config=config,
            step_name=STEP_NAME,
            texts=config["pipeline"]["targets"]["publish"],
            final_publish=True,
        )
    except RuntimeError as exc:
        if "No configured OCR/UI target matched this step" not in str(exc):
            raise
        tap_text_target(
            adb=adb,
            state=state,
            config=config,
            step_name=f"{STEP_NAME}_recover_next",
            texts=config["pipeline"]["targets"]["next"],
        )
        decision = tap_text_target(
            adb=adb,
            state=state,
            config=config,
            step_name=f"{STEP_NAME}_retry",
            texts=config["pipeline"]["targets"]["publish"],
            final_publish=True,
        )
    mark_step_done(state, STEP_NAME, last_decision=decision)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
