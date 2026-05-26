from __future__ import annotations

import argparse

from common import add_step_args, load_or_create_state, load_step_config, make_adb, mark_step_done, tap_text_target


STEP_NAME = "tap_next_after_select"


def main() -> int:
    parser = argparse.ArgumentParser(description="Find and tap Next after selecting the video.")
    add_step_args(parser)
    args = parser.parse_args()

    config = load_step_config(args.config)
    state = load_or_create_state(args, config)
    adb = make_adb(config)

    decision = tap_text_target(
        adb=adb,
        state=state,
        config=config,
        step_name=STEP_NAME,
        texts=config["pipeline"]["targets"]["next"],
        allow_fallback=config["pipeline"].get("fallbacks", {}).get("next_after_select"),
    )
    mark_step_done(state, STEP_NAME, last_decision=decision)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
