from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from execution_queue.idle_wake import is_awake, is_locked, read_power_state, wake_idle_phone
from mobile_phone_library.adb_manager import ADBManager


WAKE_KEYEVENT = "224"
MENU_KEYEVENT = "82"


class DevicePreflightError(RuntimeError):
    def __init__(self, message: str, result: dict[str, Any]) -> None:
        super().__init__(message)
        self.result = result


@dataclass(frozen=True)
class DevicePreflightOptions:
    serial: str
    adb_path: Path
    run_dir: Path
    phase: str
    app_package: str = ""
    remote_video_dir: str = "/sdcard/DCIM/Camera"
    expected_width: int = 0
    expected_height: int = 0
    skip_wake: bool = False
    startup: bool = False
    check_media_dir: bool = True
    check_input_injection: bool = True
    check_screen_size: bool = True
    check_package: bool = True
    require_ready: bool = True


def require_device_ready(options: DevicePreflightOptions) -> dict[str, Any]:
    result = run_device_preflight(options)
    if options.require_ready and not result.get("ready"):
        reason = str(result.get("reason") or "device preflight failed")
        raise DevicePreflightError(f"{options.phase}: {reason}", result)
    return result


def run_device_preflight(options: DevicePreflightOptions) -> dict[str, Any]:
    serial = str(options.serial or "").strip()
    result: dict[str, Any] = {
        "phase": options.phase,
        "serial": serial,
        "ready": False,
        "startup": options.startup,
        "expected_screen": expected_screen_text(options),
    }
    if not serial:
        result["reason"] = "missing adb serial"
        write_preflight_trace(options.run_dir, options.phase, result)
        return result

    adb = ADBManager(options.adb_path)
    try:
        adb.start_server()
    except Exception as exc:  # noqa: BLE001
        result["adb_start_server_error"] = str(exc)

    try:
        online = {device.serial: device.state for device in adb.devices()}
        result["device_state"] = online.get(serial, "missing")
        if online.get(serial) != "device":
            result["reason"] = "adb device is not online/authorized"
            write_preflight_trace(options.run_dir, options.phase, result)
            return result

        awake_before = is_awake(read_power_state(adb, serial))
        locked_before = is_locked(adb, serial)
        result["awake_before"] = awake_before
        result["locked_before"] = locked_before

        stayon = adb.run(["shell", "svc", "power", "stayon", "true"], serial=serial, timeout=10)
        result["stayon_enabled"] = stayon.returncode == 0

        if (not awake_before or locked_before) and options.skip_wake:
            result["reason"] = "screen is not awake/unlocked and wake was skipped"
            write_preflight_trace(options.run_dir, options.phase, result)
            return result

        if not awake_before or locked_before:
            if options.startup:
                wake_result = wake_idle_phone(serial)
            else:
                wake_result = wake_screen_in_place(adb, serial)
            result["wake"] = {
                "success": wake_result.success,
                "awake_after": wake_result.awake_after,
                "locked_after": wake_result.locked_after,
                "actions": list(wake_result.actions),
                "error": wake_result.error,
            }

        width, height = read_screen_size(adb, serial)
        result["screen"] = f"{width}x{height}" if width and height else ""
        result["screen_size_ok"] = screen_size_ok(options, width, height)

        if options.check_package:
            result["package_exists"] = adb.package_exists(serial, options.app_package)
        else:
            result["package_exists"] = True

        result["screenshot_ok"] = adb.screenshot_ok(serial)

        if options.check_media_dir:
            result["media_dir_writable"] = adb.media_dir_writable(serial, options.remote_video_dir)
        else:
            result["media_dir_writable"] = True

        if options.check_input_injection:
            injection = check_input_injection(adb, serial)
            result["input_injection_ok"] = injection["ok"]
            if injection["output"]:
                result["input_injection_output"] = injection["output"]
        else:
            result["input_injection_ok"] = True

        result["awake_after"] = is_awake(read_power_state(adb, serial))
        result["locked_after"] = is_locked(adb, serial)
        result["foreground_package"] = safe_foreground_package(adb, serial)

        result["ready"] = bool(
            result["screen_size_ok"]
            and result["package_exists"]
            and result["screenshot_ok"]
            and result["media_dir_writable"]
            and result["input_injection_ok"]
            and result["awake_after"]
            and not result["locked_after"]
        )
        if not result["ready"]:
            result["reason"] = preflight_failure_reason(result)
        write_preflight_trace(options.run_dir, options.phase, result)
        return result
    except Exception as exc:  # noqa: BLE001
        result["reason"] = str(exc)
        write_preflight_trace(options.run_dir, options.phase, result)
        return result


@dataclass(frozen=True)
class InPlaceWakeResult:
    serial: str
    awake_after: bool
    locked_after: bool
    actions: tuple[str, ...]
    error: str = ""

    @property
    def success(self) -> bool:
        return self.awake_after and not self.locked_after and not self.error


def wake_screen_in_place(adb: ADBManager, serial: str) -> InPlaceWakeResult:
    actions: list[str] = []
    error = ""
    try:
        run_guard_action(adb, serial, ["shell", "input", "keyevent", WAKE_KEYEVENT], "wake_keyevent", actions)
        time.sleep(0.8)
        run_guard_action(adb, serial, ["shell", "wm", "dismiss-keyguard"], "dismiss_keyguard", actions, allow_nonzero=True)
        time.sleep(0.4)
        if is_locked(adb, serial):
            run_guard_action(adb, serial, ["shell", "input", "keyevent", MENU_KEYEVENT], "menu_keyevent", actions, allow_nonzero=True)
            time.sleep(0.4)
            run_guard_action(adb, serial, ["shell", "wm", "dismiss-keyguard"], "dismiss_keyguard_retry", actions, allow_nonzero=True)
            time.sleep(0.4)
    except Exception as exc:  # noqa: BLE001
        error = str(exc)
    return InPlaceWakeResult(
        serial=serial,
        awake_after=is_awake(read_power_state(adb, serial)),
        locked_after=is_locked(adb, serial),
        actions=tuple(actions),
        error=error,
    )


def run_guard_action(
    adb: ADBManager,
    serial: str,
    args: list[str],
    name: str,
    actions: list[str],
    *,
    allow_nonzero: bool = False,
) -> None:
    result = adb.run(args, serial=serial, timeout=15, retry_on_protocol_fault=True)
    output = (result.stdout + result.stderr).strip()
    if result.returncode == 0 or allow_nonzero:
        actions.append(name)
    else:
        actions.append(f"{name}_failed:{compact_output(output)}")


def read_screen_size(adb: ADBManager, serial: str) -> tuple[int, int]:
    output = adb.shell(serial, "wm", "size", timeout=10)
    match = re.search(r"(\d+)x(\d+)", output)
    if not match:
        return 0, 0
    return int(match.group(1)), int(match.group(2))


def check_input_injection(adb: ADBManager, serial: str) -> dict[str, Any]:
    result = adb.run(["shell", "input", "keyevent", "0"], serial=serial, timeout=10)
    output = (result.stdout + result.stderr).strip()
    normalized = output.casefold()
    ok = result.returncode == 0 and "securityexception" not in normalized and "inject_events" not in normalized
    return {"ok": ok, "output": compact_output(output)}


def safe_foreground_package(adb: ADBManager, serial: str) -> str:
    try:
        return adb.foreground_package(serial)
    except Exception:  # noqa: BLE001
        return ""


def expected_screen_text(options: DevicePreflightOptions) -> str:
    if options.expected_width and options.expected_height:
        return f"{options.expected_width}x{options.expected_height}"
    return ""


def screen_size_ok(options: DevicePreflightOptions, width: int, height: int) -> bool:
    if not options.check_screen_size:
        return True
    if not options.expected_width or not options.expected_height:
        return True
    return width == options.expected_width and height == options.expected_height


def preflight_failure_reason(result: dict[str, Any]) -> str:
    if not result.get("awake_after"):
        return "screen is not awake"
    if result.get("locked_after"):
        return "screen is still locked/keyguard is visible"
    if not result.get("screen_size_ok"):
        return "screen size does not match profile"
    if not result.get("package_exists"):
        return "target app package is not installed"
    if not result.get("screenshot_ok"):
        return "screenshot capture failed"
    if not result.get("media_dir_writable"):
        return "remote media directory is not writable"
    if not result.get("input_injection_ok"):
        return "adb input injection is blocked"
    return "one or more preflight checks failed"


def write_preflight_trace(run_dir: Path, phase: str, result: dict[str, Any]) -> None:
    trace_dir = Path(run_dir) / "traces"
    trace_dir.mkdir(parents=True, exist_ok=True)
    safe_phase = re.sub(r"[^A-Za-z0-9_.-]+", "_", phase.strip() or "device_preflight")
    (trace_dir / f"device_preflight_{safe_phase}.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def compact_output(output: str, limit: int = 220) -> str:
    return re.sub(r"\s+", " ", str(output or "")).strip()[:limit]
