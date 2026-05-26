from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import dataclass

from mobile_phone_library.adb_manager import ADBManager, adb_process_output


XIAOMI_UNLOCK_PIN_ENV = "GROUP_CONTROL_XIAOMI_UNLOCK_PIN"
WAKE_KEYEVENT = "224"
ENTER_KEYEVENT = "66"

_ADB_WAKE_LOCK = threading.Lock()


@dataclass(frozen=True)
class IdleWakeResult:
    serial: str
    attempted: bool
    awake_before: bool
    awake_after: bool
    locked_before: bool
    locked_after: bool
    brand: str = ""
    manufacturer: str = ""
    model: str = ""
    actions: tuple[str, ...] = ()
    error: str = ""

    @property
    def success(self) -> bool:
        return self.awake_after and not self.locked_after and not self.error

    def failure_reason(self) -> str:
        if self.error:
            return self.error
        failed_actions = [action for action in self.actions if "_failed:" in action]
        if failed_actions:
            return failed_actions[-1]
        if not self.awake_after:
            return "screen is still not awake"
        if self.locked_after:
            return "screen is awake but still locked"
        return ""


def wake_idle_phone(serial: str, *, xiaomi_unlock_pin: str | None = None) -> IdleWakeResult:
    clean_serial = str(serial or "").strip()
    if not clean_serial:
        return IdleWakeResult(
            serial="",
            attempted=False,
            awake_before=False,
            awake_after=False,
            locked_before=False,
            locked_after=False,
            error="missing adb serial",
        )

    actions: list[str] = []
    unlock_pin = os.environ.get(XIAOMI_UNLOCK_PIN_ENV, "") if xiaomi_unlock_pin is None else xiaomi_unlock_pin
    with _ADB_WAKE_LOCK:
        adb = ADBManager()
        try:
            props = read_device_props(adb, clean_serial)
            awake_before = is_awake(read_power_state(adb, clean_serial))
            locked_before = is_locked(adb, clean_serial)

            if not awake_before:
                run_action(
                    adb,
                    clean_serial,
                    ["shell", "svc", "power", "stayon", "true"],
                    "svc_power_stayon",
                    actions,
                    timeout=10,
                    allow_nonzero=True,
                )
                time.sleep(0.7)

            if not is_awake(read_power_state(adb, clean_serial)):
                run_action(adb, clean_serial, ["shell", "input", "keyevent", WAKE_KEYEVENT], "wake_keyevent", actions, timeout=10)
                time.sleep(0.7)

            if not is_awake(read_power_state(adb, clean_serial)):
                start_home(adb, clean_serial, actions)
                time.sleep(0.8)

            if is_locked(adb, clean_serial):
                dismiss_keyguard(adb, clean_serial, actions)
                time.sleep(0.5)

            if is_locked(adb, clean_serial):
                if is_xiaomi_device(props) and unlock_pin:
                    verify_xiaomi_pin(adb, clean_serial, unlock_pin, actions)
                    dismiss_keyguard(adb, clean_serial, actions)
                    time.sleep(0.3)
                    unlock_xiaomi_with_pin(adb, clean_serial, unlock_pin, actions)
                else:
                    start_home(adb, clean_serial, actions)
                    time.sleep(0.5)
                    dismiss_keyguard(adb, clean_serial, actions)
                    run_action(adb, clean_serial, ["shell", "input", "keyevent", "82"], "menu_keyevent", actions, timeout=10)
                    time.sleep(0.5)

            awake_after = is_awake(read_power_state(adb, clean_serial))
            locked_after = is_locked(adb, clean_serial)
            if awake_after and not locked_after:
                start_home(adb, clean_serial, actions)

            return IdleWakeResult(
                serial=clean_serial,
                attempted=True,
                awake_before=awake_before,
                awake_after=awake_after,
                locked_before=locked_before,
                locked_after=locked_after,
                brand=props.get("brand", ""),
                manufacturer=props.get("manufacturer", ""),
                model=props.get("model", ""),
                actions=tuple(actions),
            )
        except Exception as exc:  # noqa: BLE001
            return IdleWakeResult(
                serial=clean_serial,
                attempted=True,
                awake_before=False,
                awake_after=False,
                locked_before=False,
                locked_after=False,
                actions=tuple(actions),
                error=str(exc),
            )


def read_device_props(adb: ADBManager, serial: str) -> dict[str, str]:
    return {
        "brand": clean_prop_value(adb.shell(serial, "getprop", "ro.product.brand", timeout=10)),
        "manufacturer": clean_prop_value(adb.shell(serial, "getprop", "ro.product.manufacturer", timeout=10)),
        "model": clean_prop_value(adb.shell(serial, "getprop", "ro.product.model", timeout=10)),
    }


def clean_prop_value(output: str) -> str:
    for line in str(output or "").splitlines():
        clean_line = line.strip()
        if clean_line and not clean_line.startswith("* daemon"):
            return clean_line
    return ""


def is_xiaomi_device(props: dict[str, str]) -> bool:
    text = " ".join(str(value or "") for value in props.values()).casefold()
    return any(name in text for name in ("xiaomi", "redmi", "poco"))


def read_power_state(adb: ADBManager, serial: str) -> str:
    return adb.shell(serial, "dumpsys", "power", timeout=15)


def is_awake(output: str) -> bool:
    normalized = re.sub(r"\s+", "", str(output or "")).casefold()
    if "mwakefulness=asleep" in normalized or "mwakefulness=dozing" in normalized:
        return False
    if "mwakefulness=awake" in normalized or "mwakefulness=waking" in normalized:
        return True
    return "displaypower:state=on" in normalized or "mholdingdisplaysuspendblocker=true" in normalized


def is_locked(adb: ADBManager, serial: str) -> bool:
    outputs: list[str] = []
    for args in (("dumpsys", "window"), ("dumpsys", "trust")):
        try:
            outputs.append(adb.shell(serial, *args, timeout=15))
        except Exception:  # noqa: BLE001
            continue
    normalized = re.sub(r"\s+", "", "\n".join(outputs)).casefold()
    true_markers = (
        "mshowinglockscreen=true",
        "mkeyguardshowing=true",
        "iskeyguardshowing=true",
        "isdreaminglockscreen=true",
        "isdevicelocked=true",
        "deviceislocked=true",
    )
    false_markers = (
        "mshowinglockscreen=false",
        "mkeyguardshowing=false",
        "iskeyguardshowing=false",
        "isdevicelocked=false",
        "deviceislocked=false",
    )
    if any(marker in normalized for marker in true_markers):
        return True
    if any(marker in normalized for marker in false_markers):
        return False
    return False


def unlock_xiaomi_with_pin(adb: ADBManager, serial: str, pin: str, actions: list[str]) -> None:
    width, height = read_screen_size(adb, serial)
    x = width // 2
    run_action(
        adb,
        serial,
        ["shell", "input", "swipe", str(x), str(int(height * 0.82)), str(x), str(int(height * 0.34)), "260"],
        "swipe_unlock",
        actions,
        timeout=10,
    )
    time.sleep(0.5)
    run_action(adb, serial, ["shell", "input", "text", pin], "enter_pin", actions, timeout=10)
    time.sleep(0.2)
    run_action(adb, serial, ["shell", "input", "keyevent", ENTER_KEYEVENT], "enter_keyevent", actions, timeout=10)
    time.sleep(0.8)
    dismiss_keyguard(adb, serial, actions)
    time.sleep(0.3)


def verify_xiaomi_pin(adb: ADBManager, serial: str, pin: str, actions: list[str]) -> None:
    run_action(adb, serial, ["shell", "cmd", "lock_settings", "verify", "--old", pin], "verify_pin", actions, timeout=15)


def read_screen_size(adb: ADBManager, serial: str) -> tuple[int, int]:
    output = adb.shell(serial, "wm", "size", timeout=10)
    match = re.search(r"(\d+)x(\d+)", output)
    if not match:
        return 1080, 2400
    return int(match.group(1)), int(match.group(2))


def dismiss_keyguard(adb: ADBManager, serial: str, actions: list[str]) -> None:
    run_action(adb, serial, ["shell", "wm", "dismiss-keyguard"], "dismiss_keyguard", actions, timeout=10)


def start_home(adb: ADBManager, serial: str, actions: list[str]) -> None:
    run_action(
        adb,
        serial,
        ["shell", "am", "start", "-W", "-a", "android.intent.action.MAIN", "-c", "android.intent.category.HOME"],
        "start_home",
        actions,
        timeout=20,
    )


def run_action(
    adb: ADBManager,
    serial: str,
    args: list[str],
    name: str,
    actions: list[str],
    *,
    timeout: int,
    allow_nonzero: bool = False,
) -> None:
    result = adb.run(args, serial=serial, timeout=timeout, retry_on_protocol_fault=True)
    output = adb_process_output(result)
    if result.returncode == 0 or allow_nonzero:
        actions.append(name)
        return
    clean_output = output.replace("\r", " ").replace("\n", " ").strip()
    actions.append(f"{name}_failed:{clean_output[:160]}")
