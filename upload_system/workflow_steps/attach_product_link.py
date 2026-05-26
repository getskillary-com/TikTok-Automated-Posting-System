from __future__ import annotations

import argparse

from common import (
    add_step_args,
    load_or_create_state,
    load_step_config,
    make_adb,
    mark_step_done,
    paste_text,
    tap_text_target,
)


STEP_NAME = "attach_product_link"


def main() -> int:
    parser = argparse.ArgumentParser(description="Attach a TikTok Shop product before publishing.")
    add_step_args(parser)
    args = parser.parse_args()

    config = load_step_config(args.config)
    state = load_or_create_state(args, config)
    adb = make_adb(config)

    product_link = str(args.product_link or state.get("product_link") or "").strip()
    product_name = str(args.product_name or state.get("product_name") or "").strip()
    product_query = product_name or product_link
    if not product_query:
        raise ValueError("Showcase workflow requires --product-link or --product-name.")

    pipeline = config["pipeline"]
    targets = pipeline["targets"]
    fallbacks = pipeline.get("fallbacks", {})
    product_config = pipeline.get("product_linking", {})
    decisions = {}

    decisions["entry"] = tap_text_target(
        adb=adb,
        state=state,
        config=config,
        step_name=f"{STEP_NAME}_entry",
        texts=targets["product_entry"],
        allow_fallback=fallbacks.get("product_entry"),
    )
    adb.wait(float(product_config.get("after_entry_delay_seconds", pipeline["post_step_delay_seconds"])))

    decisions["search"] = tap_text_target(
        adb=adb,
        state=state,
        config=config,
        step_name=f"{STEP_NAME}_search",
        texts=targets["product_search_field"],
        allow_fallback=fallbacks.get("product_search_field"),
    )
    paste_text(adb, product_query, config, dry_run=bool(state.get("dry_run", False)))
    if product_config.get("hide_keyboard_after_paste", True) and not state.get("dry_run", False):
        adb.wait(0.5)
        adb.shell("input", "keyevent", "4", timeout=15)
    adb.wait(float(product_config.get("after_search_paste_delay_seconds", pipeline["post_step_delay_seconds"])))

    select_texts = list(targets["product_select"])
    if product_name:
        select_texts.insert(0, product_name)
    decisions["select"] = tap_text_target(
        adb=adb,
        state=state,
        config=config,
        step_name=f"{STEP_NAME}_select",
        texts=select_texts,
        allow_fallback=fallbacks.get("product_select"),
    )

    if product_config.get("confirm_after_select", True):
        decisions["confirm"] = tap_text_target(
            adb=adb,
            state=state,
            config=config,
            step_name=f"{STEP_NAME}_confirm",
            texts=targets["product_confirm"],
            allow_fallback=fallbacks.get("product_confirm"),
        )

    mark_step_done(
        state,
        STEP_NAME,
        product_link=product_link,
        product_name=product_name,
        product_query=product_query,
        product_link_decisions=decisions,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
