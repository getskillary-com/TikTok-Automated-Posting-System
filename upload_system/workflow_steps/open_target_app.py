from __future__ import annotations

import argparse

from common import add_step_args, load_or_create_state, load_step_config, make_adb, mark_step_done, resolve_package


STEP_NAME = "open_target_app"


def main() -> int:
    parser = argparse.ArgumentParser(description="Find and open TikTok Studio/TikTok app.")
    add_step_args(parser)
    args = parser.parse_args()

    config = load_step_config(args.config)
    state = load_or_create_state(args, config)
    adb = make_adb(config)
    package = resolve_package(adb, config)
    launch_config = config["pipeline"].get("launch", {})
    permissions_config = config["pipeline"].get("permissions", {})

    permission_results = {}
    if permissions_config.get("grant_media_permissions_before_open", True):
        print(f"{STEP_NAME}: ensuring media permissions for {package}")
        permission_results = adb.grant_media_permissions(package)

    if launch_config.get("force_stop_before_open", True):
        print(f"{STEP_NAME}: resetting {package}")
        adb.shell("am", "force-stop", package, check=False, timeout=15)
        adb.wait(float(launch_config.get("after_force_stop_seconds", 1.0)))
    print(f"{STEP_NAME}: opening {package}")
    adb.open_app(package)
    adb.wait(float(config["pipeline"]["post_step_delay_seconds"]))
    foreground_package = adb.foreground_package()
    if foreground_package and foreground_package != package:
        raise RuntimeError(
            f"{STEP_NAME}: expected foreground package {package}, "
            f"but current foreground package is {foreground_package}."
        )
    mark_step_done(
        state,
        STEP_NAME,
        app_package=package,
        foreground_package=foreground_package,
        media_permission_results=permission_results,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

