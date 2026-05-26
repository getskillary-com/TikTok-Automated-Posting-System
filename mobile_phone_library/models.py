from __future__ import annotations

from dataclasses import dataclass, field


CONNECTION_MODES = {"usb", "wireless"}
PAIRING_STATUSES = {"unpaired", "pairing", "paired", "authorization_failed"}
AUTHORIZATION_STATUSES = {"unknown", "authorized", "unauthorized", "offline"}
PHONE_STATUSES = {"online_idle", "running", "offline", "error", "disabled", "removed"}
ACCOUNT_TYPES = {"marketing", "showcase"}


@dataclass(frozen=True)
class ADBDevice:
    serial: str
    state: str
    detail: str = ""


@dataclass(frozen=True)
class ADBToolInspection:
    source: str
    adb_path: str
    version: str
    service_status: str
    platform_tools_status: str
    required_files: dict[str, bool]
    server_port: str = ""
    devices: list[ADBDevice] = field(default_factory=list)
    error: str = ""


@dataclass(frozen=True)
class PhoneHealthResult:
    serial: str
    adb_online: bool
    authorized: bool
    screenshot_ok: bool
    app_detected: bool
    media_dir_writable: bool
    current_foreground_package: str = ""
    details: dict[str, str] = field(default_factory=dict)
