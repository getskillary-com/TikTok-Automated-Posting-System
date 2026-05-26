from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .adb import ADB, ADBError, summarize_ui_xml
from .ai import create_button_finder, png_size


def doctor(config: dict[str, Any]) -> int:
    adb = _make_adb(config)
    try:
        print(adb.version())
    except ADBError as exc:
        print(f"ADB not ready: {exc}")
        return 1

    devices = adb.devices()
    print(f"Connected devices: {devices or 'none'}")
    if not devices:
        print("Connect a phone, enable USB debugging, then run this again.")
        return 1

    adb_config = config["adb"]
    configured_package = str(adb_config.get("app_package", "") or "").strip()
    if configured_package:
        print(f"Configured app package: {configured_package}")
        for serial in devices:
            device_adb = ADB(adb_path=adb_config.get("path", "adb"), serial=serial)
            status = "installed" if package_exists(device_adb, configured_package) else "missing"
            print(f"  - {serial}: {status}")

    packages = detect_packages_for_devices(adb, config, devices)
    print("TikTok-like packages:")
    for package in packages:
        print(f"  - {package}")
    if not packages:
        print("No TikTok/TikTok Studio package was detected. Install it or set adb.app_package manually.")
    return 0


def push_only(config: dict[str, Any], video: str) -> int:
    adb = _make_adb(config)
    remote_path = adb.push_video(video, config["adb"]["remote_video_dir"])
    print(f"Pushed video: {remote_path}")
    return 0


def run_upload(
    config: dict[str, Any],
    *,
    video: str,
    allow_publish: bool,
    dry_run: bool | None,
) -> int:
    adb = _make_adb(config)
    package = _resolve_package(adb, config)
    automation = config["automation"]
    effective_dry_run = automation.get("dry_run", False) if dry_run is None else dry_run
    finder = create_button_finder(config)

    remote_path = adb.push_video(video, config["adb"]["remote_video_dir"])
    print(f"Pushed video: {remote_path}")
    print(f"Opening app: {package}")
    adb.open_app(package)

    screenshot_dir = Path(automation["screenshot_dir"])
    xml_dir = Path(automation["xml_dir"])
    trace_dir = Path(automation["trace_dir"])
    for directory in (screenshot_dir, xml_dir, trace_dir):
        directory.mkdir(parents=True, exist_ok=True)

    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    max_steps = int(automation["max_steps"])

    for step in range(1, max_steps + 1):
        adb.wait(float(automation["screen_settle_seconds"]))
        prefix = f"{run_id}-{step:02d}"
        screenshot_path = adb.screenshot(screenshot_dir / f"{prefix}.png")
        xml_text = adb.dump_ui_xml()
        (xml_dir / f"{prefix}.xml").write_text(xml_text, encoding="utf-8")
        nodes = summarize_ui_xml(xml_text)
        ui_summary = "\n".join(node.summary() for node in nodes)
        screen_size = png_size(screenshot_path)

        decision = finder.decide(
            screenshot_path=screenshot_path,
            screen_size=screen_size,
            ui_summary=ui_summary,
            ui_nodes=nodes,
            config=config,
            remote_video_path=remote_path,
            allow_publish=allow_publish,
            step_number=step,
        )
        (trace_dir / f"{prefix}.json").write_text(
            json.dumps(decision, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        print(_format_decision(step, decision, effective_dry_run))

        action = decision["action"]
        if action == "done":
            print("Flow completed.")
            return 0
        if action == "manual_review":
            print("Stopped for manual review.")
            return 2
        if action == "wait":
            adb.wait(float(automation["tap_delay_seconds"]))
            continue

        x = int(decision["x"])
        y = int(decision["y"])
        _validate_coordinates(x, y, screen_size)
        if _looks_like_final_publish(decision, config) and not allow_publish:
            print("Stopped before final publish. Re-run with --allow-publish to allow this tap.")
            return 2
        if not effective_dry_run:
            adb.tap(x, y)
        adb.wait(float(automation["tap_delay_seconds"]))

    print(f"Stopped after max_steps={max_steps}. Check runs/traces for the last recognition decision.")
    return 3


def _make_adb(config: dict[str, Any]) -> ADB:
    adb_config = config["adb"]
    return ADB(adb_path=adb_config.get("path", "adb"), serial=adb_config.get("serial", ""))


def detect_packages_for_devices(adb: ADB, config: dict[str, Any], devices: list[str]) -> list[str]:
    adb_config = config["adb"]
    configured_serial = str(adb_config.get("serial", "") or "").strip()
    if configured_serial or len(devices) <= 1:
        return adb.detect_packages()
    packages: list[str] = []
    for serial in devices:
        device_adb = ADB(adb_path=adb_config.get("path", "adb"), serial=serial)
        for package in device_adb.detect_packages():
            packages.append(f"{serial}: {package}")
    return packages


def package_exists(adb: ADB, package: str) -> bool:
    output = adb.shell("pm", "path", package, check=False, timeout=15)
    return "package:" in output


def _resolve_package(adb: ADB, config: dict[str, Any]) -> str:
    configured = config["adb"].get("app_package", "").strip()
    if configured:
        return configured
    packages = adb.detect_packages()
    studio_packages = [
        package
        for package in packages
        if "studio" in package.lower() or "creator" in package.lower()
    ]
    if len(studio_packages) == 1:
        return studio_packages[0]
    if len(packages) == 1:
        return packages[0]
    if packages:
        raise ADBError(
            "Multiple possible packages detected. Set adb.app_package in config.json:\n"
            + "\n".join(f"  - {package}" for package in packages)
        )
    raise ADBError("No app package configured or detected. Run doctor and set adb.app_package.")


def _validate_coordinates(x: int, y: int, screen_size: tuple[int, int]) -> None:
    width, height = screen_size
    if x < 0 or y < 0 or x >= width or y >= height:
        raise ValueError(f"Recognition returned out-of-screen tap: ({x}, {y}) for {width}x{height}")


def _looks_like_final_publish(decision: dict[str, Any], config: dict[str, Any]) -> bool:
    if decision.get("is_final_publish"):
        return True
    label = str(decision.get("label", "")).lower()
    keywords = config["automation"].get("final_publish_keywords", [])
    return any(str(keyword).lower() in label for keyword in keywords)


def _format_decision(step: int, decision: dict[str, Any], dry_run: bool) -> str:
    suffix = " [dry-run]" if dry_run else ""
    if decision["action"] == "tap":
        return (
            f"Step {step}: tap ({decision['x']}, {decision['y']}) "
            f"{decision['label']!r} conf={decision['confidence']:.2f}{suffix} - {decision['reason']}"
        )
    return (
        f"Step {step}: {decision['action']} {decision['label']!r} "
        f"conf={decision['confidence']:.2f}{suffix} - {decision['reason']}"
    )
