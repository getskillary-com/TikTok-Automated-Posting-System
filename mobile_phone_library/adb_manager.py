from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

from .models import ADBDevice, ADBToolInspection, PhoneHealthResult
from shared.windows_process import no_window_subprocess_kwargs


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BUNDLED_PLATFORM_TOOLS = PROJECT_ROOT / "platform-tools"
BUNDLED_ADB = BUNDLED_PLATFORM_TOOLS / "adb.exe"
ADB_HOME = PROJECT_ROOT / "storage" / "android_home"
REQUIRED_WINDOWS_FILES = ("adb.exe", "AdbWinApi.dll", "AdbWinUsbApi.dll")
ADB_PROTOCOL_FAULT_MARKERS = ("protocol fault", "couldn't read status message")
DEFAULT_ADB_SERVER_PORT = "5037"


class ADBManagerError(RuntimeError):
    pass


class ADBManager:
    def __init__(self, adb_path: str | Path | None = None) -> None:
        self.adb_path = str(resolve_adb_path(adb_path))

    def run(
        self,
        args: list[str],
        *,
        serial: str = "",
        check: bool = False,
        timeout: int = 30,
        retry_on_protocol_fault: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        command = [self.adb_path, "-P", adb_server_port()]
        if serial:
            command.extend(["-s", serial])
        command.extend(args)
        result = self._run_command(command, timeout=timeout)
        if retry_on_protocol_fault and is_protocol_fault(adb_process_output(result)):
            self.restart_server_quietly()
            result = self._run_command(command, timeout=timeout)
        if check and result.returncode != 0:
            raise ADBManagerError((result.stderr or result.stdout).strip())
        return result

    def _run_command(self, command: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                env=adb_env(),
                **no_window_subprocess_kwargs(),
            )
        except FileNotFoundError as exc:
            raise ADBManagerError(f"ADB executable not found: {self.adb_path}") from exc
        except subprocess.TimeoutExpired as exc:
            raise ADBManagerError(f"ADB command timed out: {' '.join(command)}") from exc

    def restart_server_quietly(self) -> None:
        if os.environ.get("GROUP_CONTROL_ADB_DISABLE_KILL_SERVER") == "1":
            try:
                self._run_command([self.adb_path, "-P", adb_server_port(), "start-server"], timeout=30)
            except ADBManagerError:
                pass
            return
        for args, timeout in ((["kill-server"], 15), (["start-server"], 30)):
            try:
                self._run_command([self.adb_path, "-P", adb_server_port(), *args], timeout=timeout)
            except ADBManagerError:
                pass

    def version(self) -> str:
        result = self.run(["version"], timeout=20)
        if result.returncode != 0:
            return ""
        return result.stdout.strip()

    def start_server(self) -> str:
        return self.run(["start-server"], timeout=30).stdout.strip()

    def kill_server(self) -> str:
        return self.run(["kill-server"], timeout=30).stdout.strip()

    def devices(self) -> list[ADBDevice]:
        result = self.run(["devices", "-l"], timeout=30)
        return parse_adb_devices(result.stdout)

    def pair(self, host: str, pair_port: str | int, pair_code: str) -> str:
        target = f"{host}:{pair_port}"
        return self.pair_target(target, pair_code)

    def pair_target(self, target: str, pair_code: str) -> str:
        result = self.run(["pair", target, str(pair_code)], timeout=45, retry_on_protocol_fault=True)
        output = adb_process_output(result)
        if result.returncode != 0 or "failed" in output.casefold() or is_protocol_fault(output):
            raise ADBManagerError(describe_adb_failure("配对", target, output))
        return output

    def connect(self, host: str, connect_port: str | int) -> str:
        target = f"{host}:{connect_port}"
        return self.connect_target(target)

    def connect_target(self, target: str) -> str:
        result = self.run(["connect", target], timeout=30, retry_on_protocol_fault=True)
        output = adb_process_output(result)
        if result.returncode != 0 or "failed" in output.casefold() or is_protocol_fault(output):
            raise ADBManagerError(describe_adb_failure("连接", target, output))
        return output

    def disconnect(self, serial: str) -> str:
        return self.run(["disconnect", serial], timeout=30).stdout.strip()

    def mdns_services(self) -> list[dict[str, str]]:
        result = self.run(["mdns", "services"], timeout=30, retry_on_protocol_fault=True)
        output = adb_process_output(result)
        if result.returncode != 0:
            raise ADBManagerError(describe_adb_failure("发现无线调试服务", "mDNS", output))
        return parse_mdns_services(output)

    def shell(self, serial: str, *args: str, timeout: int = 30) -> str:
        result = self.run(["shell", *args], serial=serial, timeout=timeout)
        return (result.stdout + result.stderr).strip()

    def shell_command(self, serial: str, command: str, timeout: int = 30) -> str:
        return self.shell(serial, command, timeout=timeout)

    def get_device_name(self, serial: str) -> str:
        model = self.shell(serial, "getprop", "ro.product.model", timeout=15).strip()
        brand = self.shell(serial, "getprop", "ro.product.brand", timeout=15).strip()
        name = " ".join(part for part in (brand, model) if part).strip()
        return name or serial

    def foreground_package(self, serial: str) -> str:
        outputs = [
            self.shell(serial, "dumpsys", "window", "windows", timeout=15),
            self.shell(serial, "dumpsys", "activity", "activities", timeout=15),
        ]
        patterns = [
            r"mCurrentFocus=.*?\s([A-Za-z0-9_.]+)/",
            r"mFocusedApp=.*?\s([A-Za-z0-9_.]+)/",
            r"topResumedActivity=.*?\s([A-Za-z0-9_.]+)/",
            r"mResumedActivity=.*?\s([A-Za-z0-9_.]+)/",
        ]
        for output in outputs:
            for pattern in patterns:
                match = re.search(pattern, output)
                if match:
                    return match.group(1)
        return ""

    def package_exists(self, serial: str, package: str) -> bool:
        if not package:
            return False
        output = self.shell(serial, "pm", "path", package, timeout=15)
        return "package:" in output

    def screenshot_ok(self, serial: str) -> bool:
        remote_path = "/sdcard/group_control_health.png"
        output = self.shell(serial, "screencap", "-p", remote_path, timeout=45)
        self.shell(serial, "rm", "-f", remote_path, timeout=15)
        return "error" not in output.casefold()

    def media_dir_writable(self, serial: str, remote_dir: str) -> bool:
        test_path = remote_dir.rstrip("/") + "/.group_control_write_test"
        command = (
            "mkdir -p " + shlex.quote(remote_dir) + " && "
            "printf ok > " + shlex.quote(test_path) + " && "
            "rm -f " + shlex.quote(test_path)
        )
        output = self.shell_command(serial, command, timeout=30)
        return output.strip() == ""

    def health_check(self, serial: str, *, app_package: str = "", remote_video_dir: str = "/sdcard/DCIM/Camera") -> PhoneHealthResult:
        devices = {device.serial: device for device in self.devices()}
        device = devices.get(serial)
        adb_online = device is not None and device.state in {"device", "unauthorized", "offline"}
        authorized = device is not None and device.state == "device"
        details: dict[str, str] = {"device_state": device.state if device else "missing"}
        screenshot_ok = False
        app_detected = False
        media_writable = False
        foreground = ""
        if authorized:
            try:
                screenshot_ok = self.screenshot_ok(serial)
            except Exception as exc:  # noqa: BLE001
                details["screenshot_error"] = str(exc)
            try:
                app_detected = self.package_exists(serial, app_package) if app_package else True
            except Exception as exc:  # noqa: BLE001
                details["app_error"] = str(exc)
            try:
                media_writable = self.media_dir_writable(serial, remote_video_dir)
            except Exception as exc:  # noqa: BLE001
                details["media_dir_error"] = str(exc)
            try:
                foreground = self.foreground_package(serial)
            except Exception as exc:  # noqa: BLE001
                details["foreground_error"] = str(exc)
        return PhoneHealthResult(
            serial=serial,
            adb_online=adb_online,
            authorized=authorized,
            screenshot_ok=screenshot_ok,
            app_detected=app_detected,
            media_dir_writable=media_writable,
            current_foreground_package=foreground,
            details=details,
        )

    def inspect_tool(self) -> ADBToolInspection:
        required = {name: (Path(self.adb_path).parent / name).exists() for name in REQUIRED_WINDOWS_FILES}
        platform_status = "complete" if all(required.values()) else "missing_files"
        version = ""
        service_status = "unknown"
        error = ""
        devices: list[ADBDevice] = []
        try:
            version = self.version()
            service_status = "running" if version else "error"
            devices = self.devices()
        except Exception as exc:  # noqa: BLE001
            service_status = "error"
            error = str(exc)
        return ADBToolInspection(
            source="bundled" if Path(self.adb_path).resolve() == BUNDLED_ADB.resolve() else "custom",
            adb_path=self.adb_path,
            version=version,
            service_status=service_status,
            platform_tools_status=platform_status,
            required_files=required,
            server_port=adb_server_port(),
            devices=devices,
            error=error,
        )


def resolve_adb_path(path: str | Path | None = None) -> Path:
    if path:
        return Path(path).expanduser().resolve()
    if BUNDLED_ADB.exists():
        return BUNDLED_ADB
    found = shutil.which("adb")
    if found:
        return Path(found).resolve()
    return BUNDLED_ADB


def adb_env() -> dict[str, str]:
    ADB_HOME.mkdir(parents=True, exist_ok=True)
    android_dir = ADB_HOME / ".android"
    android_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    project_adb_key = android_dir / "adbkey"
    user_profile = os.environ.get("USERPROFILE", "")
    key_candidates = [project_adb_key]
    if user_profile:
        key_candidates.append(Path(user_profile).expanduser() / ".android" / "adbkey")
    key_candidates.append(Path("C:/Users/admin/.android/adbkey"))
    for adb_key in key_candidates:
        if adb_key and adb_key.exists():
            env["ADB_VENDOR_KEYS"] = str(adb_key)
            break
    env["ADB_SERVER_PORT"] = adb_server_port()
    return env


def adb_server_port() -> str:
    return os.environ.get("GROUP_CONTROL_ADB_SERVER_PORT", DEFAULT_ADB_SERVER_PORT)


def adb_process_output(result: subprocess.CompletedProcess[str]) -> str:
    return (result.stdout + result.stderr).strip()


def is_protocol_fault(output: str) -> bool:
    normalized = str(output or "").casefold()
    return any(marker in normalized for marker in ADB_PROTOCOL_FAULT_MARKERS)


def describe_adb_failure(action: str, target: str, output: str) -> str:
    clean_output = output.strip() or "ADB 没有返回详细错误。"
    if is_protocol_fault(clean_output):
        return (
            f"ADB {action} {target} 时通信异常，已自动重启 ADB 服务并重试一次但仍失败。"
            "请重新打开手机“无线调试”的配对码，确认电脑和手机在同一 Wi-Fi，"
            "IP、配对端口、配对码没有过期；如果手动配对仍失败，请填写无线调试主页显示的连接端口。"
            f"原始错误：{clean_output}"
        )
    return clean_output or f"ADB {action} failed: {target}"


def parse_adb_devices(output: str) -> list[ADBDevice]:
    devices: list[ADBDevice] = []
    for raw_line in output.splitlines()[1:]:
        line = raw_line.strip()
        if not line or line.startswith("*"):
            continue
        parts = line.split(None, 2)
        if len(parts) >= 2:
            detail = parts[2] if len(parts) > 2 else ""
            devices.append(ADBDevice(serial=parts[0], state=parts[1], detail=detail))
    return devices


def parse_mdns_services(output: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    target_pattern = re.compile(r"((?:\d{1,3}\.){3}\d{1,3}|\[[0-9a-fA-F:]+\]|[A-Za-z0-9_.-]+):(\d{1,5})")
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or line.lower().startswith("list of"):
            continue
        if "_adb-tls-" not in line:
            continue
        parts = line.split()
        service = parts[0].rstrip(".") if parts else ""
        service_type = ""
        instance = service
        if "._adb-tls-" in service:
            instance, suffix = service.split("._adb-tls-", 1)
            service_type = "_adb-tls-" + suffix.split(".", 1)[0]
        elif len(parts) > 1 and parts[1].startswith("_adb-tls-"):
            service_type = parts[1].rstrip(".")
        target = ""
        for match in target_pattern.finditer(line):
            target = match.group(0)
        if service and target:
            rows.append(
                {
                    "service": service,
                    "instance": instance,
                    "type": service_type,
                    "target": target,
                }
            )
    return rows


def devices_to_counts(devices: list[ADBDevice]) -> dict[str, int]:
    return {
        "online": sum(1 for device in devices if device.state == "device"),
        "unauthorized": sum(1 for device in devices if device.state == "unauthorized"),
        "offline": sum(1 for device in devices if device.state == "offline"),
    }


def inspection_to_json(inspection: ADBToolInspection) -> str:
    return json.dumps(
        {
            "source": inspection.source,
            "adb_path": inspection.adb_path,
            "version": inspection.version,
            "service_status": inspection.service_status,
            "platform_tools_status": inspection.platform_tools_status,
            "required_files": inspection.required_files,
            "server_port": inspection.server_port,
            "devices": [device.__dict__ for device in inspection.devices],
            "error": inspection.error,
        },
        ensure_ascii=False,
    )
