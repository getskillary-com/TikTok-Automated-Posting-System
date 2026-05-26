from __future__ import annotations

import argparse
import re
import time
from typing import Any

from common import (
    add_step_args,
    capture_screen,
    collect_text_candidates,
    load_or_create_state,
    load_step_config,
    make_adb,
    mark_step_done,
    resolve_package,
    write_trace,
)


STEP_NAME = "minimize_and_cleanup"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Wait for publish/upload to finish, minimize the app, and clear it from background."
    )
    add_step_args(parser)
    args = parser.parse_args()

    config = load_step_config(args.config)
    state = load_or_create_state(args, config)
    adb = make_adb(config)
    cleanup_config = config["pipeline"].get("cleanup", {})
    package = state.get("app_package") or resolve_package(adb, config)
    dry_run = bool(state.get("dry_run", False))

    wait_result = wait_until_publish_idle(
        adb=adb,
        state=state,
        config=config,
        cleanup_config=cleanup_config,
        package=package,
        dry_run=dry_run,
    )

    decision: dict[str, Any] = {
        "action": "minimize_and_cleanup",
        "package": package,
        "wait_result": wait_result,
        "home_keyevent": str(cleanup_config.get("home_keyevent", "3")),
        "force_stop_app": bool(cleanup_config.get("force_stop_app", False)),
        "dry_run": dry_run,
    }

    print(f"{STEP_NAME}: minimizing {package}")
    if not dry_run:
        adb.shell("input", "keyevent", decision["home_keyevent"], timeout=15)
        adb.wait(float(cleanup_config.get("after_home_delay_seconds", 1.0)))
    else:
        print(f"{STEP_NAME}: [dry-run] would press HOME")

    if decision["force_stop_app"]:
        print(f"{STEP_NAME}: clearing background for {package}")
        if not dry_run:
            adb.shell("am", "force-stop", str(package), timeout=15)
        else:
            print(f"{STEP_NAME}: [dry-run] would force-stop {package}")
    else:
        print(f"{STEP_NAME}: force-stop disabled by config")

    write_trace(state, STEP_NAME, decision)
    mark_step_done(state, STEP_NAME, last_decision=decision, cleanup_package=package)
    return 0


def wait_until_publish_idle(
    *,
    adb: Any,
    state: dict[str, Any],
    config: dict[str, Any],
    cleanup_config: dict[str, Any],
    package: str,
    dry_run: bool,
) -> dict[str, Any]:
    if not cleanup_config.get("wait_for_publish_complete", True):
        return {"status": "skipped", "reason": "wait_for_publish_complete is disabled"}

    max_wait_seconds = float(cleanup_config.get("max_wait_seconds", 900))
    poll_seconds = float(cleanup_config.get("poll_seconds", 10))
    initial_wait_seconds = float(cleanup_config.get("initial_wait_seconds", 60))
    minimum_wait_seconds = float(cleanup_config.get("minimum_wait_seconds", initial_wait_seconds))
    idle_confirmations_required = max(1, int(cleanup_config.get("idle_confirmations", 3)))
    post_idle_wait_seconds = float(cleanup_config.get("post_idle_wait_seconds", 0))
    start = time.monotonic()
    deadline = start + max_wait_seconds
    attempts = 0
    idle_confirmations = 0
    last_indicator = ""
    last_visible_text = ""
    checks: list[dict[str, Any]] = []

    if dry_run:
        max_wait_seconds = min(max_wait_seconds, poll_seconds)
        initial_wait_seconds = 0
        minimum_wait_seconds = 0
        post_idle_wait_seconds = 0
        deadline = time.monotonic() + max_wait_seconds

    if initial_wait_seconds > 0:
        print(
            f"{STEP_NAME}: keeping app open for initial upload window "
            f"({initial_wait_seconds:g}s)"
        )
        adb.wait(initial_wait_seconds)

    while True:
        attempts += 1
        screen = capture_screen(
            adb=adb,
            state=state,
            step_name=f"{STEP_NAME}_{attempts:02d}",
            config=config,
        )
        visible_text = visible_text_from_nodes(screen["nodes"])
        in_progress, indicator = looks_in_progress(visible_text, cleanup_config)
        indicator_source = "ui"
        if not in_progress and should_use_cleanup_ocr(screen, package, cleanup_config):
            try:
                candidates, _ = collect_text_candidates(
                    screenshot_path=screen["screenshot_path"],
                    nodes=screen["nodes"],
                    config=config,
                )
                visible_text = visible_progress_text_from_candidates(
                    candidates,
                    screen_size=screen.get("screen_size"),
                    cleanup_config=cleanup_config,
                )
                in_progress, indicator = looks_in_progress(visible_text, cleanup_config)
                indicator_source = "ocr" if in_progress else "ui+ocr"
            except Exception as exc:
                indicator_source = "ui"
                visible_text = f"{visible_text}\n[cleanup OCR unavailable: {exc}]"
        last_indicator = indicator
        last_visible_text = visible_text
        elapsed = time.monotonic() - start
        checks.append(
            {
                "attempt": attempts,
                "elapsed_seconds": round(elapsed, 1),
                "in_progress": in_progress,
                "indicator": indicator,
                "indicator_source": indicator_source,
                "idle_confirmations": idle_confirmations,
            }
        )

        if not in_progress and elapsed >= minimum_wait_seconds:
            idle_confirmations += 1
            print(
                f"{STEP_NAME}: idle confirmation "
                f"{idle_confirmations}/{idle_confirmations_required}"
            )
            if idle_confirmations < idle_confirmations_required:
                remaining = deadline - time.monotonic()
                if remaining > 0:
                    adb.wait(min(poll_seconds, max(0.0, remaining)))
                continue
            if post_idle_wait_seconds > 0:
                print(
                    f"{STEP_NAME}: extra post-idle hold "
                    f"({post_idle_wait_seconds:g}s)"
                )
                adb.wait(post_idle_wait_seconds)
            return {
                "status": "idle",
                "checks": attempts,
                "indicator": "",
                "idle_confirmations": idle_confirmations,
                "minimum_wait_seconds": minimum_wait_seconds,
                "post_idle_wait_seconds": post_idle_wait_seconds,
                "check_log": checks,
                "visible_text": visible_text,
            }
        if not in_progress:
            idle_confirmations = 0
            wait_reason = (
                f"minimum wait not reached ({elapsed:.0f}/{minimum_wait_seconds:g}s)"
                if elapsed < minimum_wait_seconds
                else "waiting for repeated idle confirmations"
            )
            print(f"{STEP_NAME}: no progress indicator, but {wait_reason}")
        else:
            idle_confirmations = 0

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            message = (
                f"{STEP_NAME}: still sees publish/upload progress "
                f"({indicator!r}) after {int(max_wait_seconds)} seconds."
            )
            if cleanup_config.get("fail_on_timeout", True) and not dry_run:
                raise RuntimeError(message)
            print(message)
            return {
                "status": "timeout",
                "checks": attempts,
                "indicator": last_indicator,
                "idle_confirmations": idle_confirmations,
                "minimum_wait_seconds": minimum_wait_seconds,
                "check_log": checks,
                "visible_text": last_visible_text,
            }

        if in_progress:
            print(
                f"{STEP_NAME}: still publishing/uploading "
                f"({indicator!r} from {indicator_source}); waiting {poll_seconds:g}s"
            )
        adb.wait(min(poll_seconds, max(0.0, remaining)))


def visible_text_from_nodes(nodes: list[Any]) -> str:
    parts: list[str] = []
    for node in nodes:
        if node.text:
            parts.append(str(node.text))
        if node.description:
            parts.append(str(node.description))
    return "\n".join(parts)


def looks_in_progress(visible_text: str, cleanup_config: dict[str, Any]) -> tuple[bool, str]:
    lowered = visible_text.casefold()
    for keyword in cleanup_config.get("progress_keywords", []):
        keyword_text = str(keyword)
        if keyword_text and keyword_text.casefold() in lowered:
            return True, keyword_text

    for match in re.finditer(r"(\d{1,3})\s*%", visible_text):
        value = int(match.group(1))
        if value < 100:
            return True, match.group(0)

    return False, ""


def visible_progress_text_from_candidates(
    candidates: list[Any],
    *,
    screen_size: Any,
    cleanup_config: dict[str, Any],
) -> str:
    _, height = normalized_screen_size(screen_size)
    ignore_top_ratio = float(cleanup_config.get("ocr_status_bar_ignore_top_ratio", 0.08))
    status_bar_bottom = int(height * ignore_top_ratio) if height else 0
    filtered: list[str] = []

    for candidate in candidates:
        if (
            getattr(candidate, "source", "") == "ocr"
            and status_bar_bottom > 0
            and int(getattr(candidate, "bottom", 0) or 0) <= status_bar_bottom
        ):
            continue
        filtered.append(str(getattr(candidate, "text", "") or ""))
    return "\n".join(text for text in filtered if text.strip())


def normalized_screen_size(screen_size: Any) -> tuple[int, int]:
    if isinstance(screen_size, (list, tuple)) and len(screen_size) >= 2:
        try:
            return int(screen_size[0]), int(screen_size[1])
        except (TypeError, ValueError):
            return 0, 0
    return 0, 0


def should_use_cleanup_ocr(screen: dict[str, Any], package: str, cleanup_config: dict[str, Any]) -> bool:
    if not cleanup_config.get("use_ocr_when_idle", True):
        return False
    if not package:
        return True
    package_names = set(re.findall(r'package="([^"]+)"', str(screen.get("xml_text") or "")))
    return not package_names or package in package_names


if __name__ == "__main__":
    raise SystemExit(main())
