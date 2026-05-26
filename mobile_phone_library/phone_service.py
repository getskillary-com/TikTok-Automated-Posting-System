from __future__ import annotations

import secrets
import socket
import string
import time
from pathlib import Path
from typing import Any

from .adb_manager import ADBManager, ADBManagerError
from .models import ADBDevice, ADBToolInspection, PhoneHealthResult
from .phone_repository import PhoneRepository
from .qr_svg import qr_svg_data_uri
from .windows_device_names import portable_device_names_by_serial


class PhoneLibraryService:
    def __init__(self, db_path: str | Path | None = None, adb_path: str | Path | None = None) -> None:
        self.repository = PhoneRepository(db_path)
        self.adb = ADBManager(adb_path)

    def inspect_adb(self) -> ADBToolInspection:
        inspection = self.adb.inspect_tool()
        self.repository.save_adb_tool_status(inspection)
        return inspection

    def refresh_devices(
        self,
        *,
        register: bool = False,
        account_name: str = "",
        account_type: str = "",
        app_package: str = "",
        remote_video_dir: str = "/sdcard/DCIM/Camera",
    ) -> list[dict[str, Any]]:
        devices = self.adb.devices()
        explorer_names = portable_device_names_by_serial()
        detected_usb_serials = {device.serial for device in devices if ":" not in device.serial}
        rows: list[dict[str, Any]] = []
        for device in devices:
            phone_id = ""
            device_name = self.detect_device_name(device, explorer_names=explorer_names) if not register else ""
            if register:
                existing = self.repository.get_by_serial(device.serial)
                existing_name = str((existing or {}).get("device_name") or "")
                update_name = self.default_device_name(
                    device,
                    existing_name=existing_name,
                    explorer_names=explorer_names,
                )
                device_name = update_name or existing_name
                phone_id = self.repository.upsert_from_adb_device(
                    device,
                    device_name=update_name,
                    account_name=account_name,
                    account_type=account_type,
                    app_package=app_package,
                    remote_video_dir=remote_video_dir,
                )
                if device.state == "device":
                    self.refresh_registered_phone_status(phone_id)
            rows.append(
                {
                    "phone_id": phone_id,
                    "serial": device.serial,
                    "state": device.state,
                    "detail": device.detail,
                    "device_name": device_name,
                }
            )
        if register:
            self.repository.mark_missing_usb_devices_offline(detected_usb_serials)
        return rows

    def safe_sync_devices(
        self,
        *,
        register_new: bool = True,
        mark_missing: bool = False,
        account_name: str = "",
        account_type: str = "",
        app_package: str = "",
        remote_video_dir: str = "/sdcard/DCIM/Camera",
    ) -> dict[str, Any]:
        devices = self.adb.devices()
        explorer_names = portable_device_names_by_serial()
        detected_usb_serials = {device.serial for device in devices if ":" not in device.serial}
        rows: list[dict[str, Any]] = []
        for device in devices:
            existing = self.repository.get_by_serial(device.serial)
            if existing is None and not register_new:
                rows.append(
                    {
                        "phone_id": "",
                        "serial": device.serial,
                        "state": device.state,
                        "action": "skipped_new",
                        "status": "",
                    }
                )
                continue
            explorer_name = explorer_names.get(device.serial.casefold(), "")
            existing_name = str(existing.get("device_name") or "") if existing else ""
            update_name = self.default_device_name(
                device,
                existing_name=existing_name,
                explorer_names=explorer_names,
            )
            device_name = update_name or existing_name
            result = self.repository.safe_upsert_from_adb_device(
                device,
                device_name=update_name,
                account_name=account_name,
                account_type=account_type,
                app_package=app_package,
                remote_video_dir=remote_video_dir,
            )
            rows.append(
                {
                    **result,
                    "state": device.state,
                    "device_name": device_name,
                    "explorer_name": explorer_name,
                }
            )
        offline_count = 0
        if mark_missing:
            offline_count = self.repository.mark_missing_usb_devices_offline(detected_usb_serials)
        return {
            "detected": len(devices),
            "online": sum(1 for device in devices if device.state == "device"),
            "unauthorized": sum(1 for device in devices if device.state == "unauthorized"),
            "offline": sum(1 for device in devices if device.state == "offline"),
            "registered": sum(1 for row in rows if str(row.get("action") or "") in {"created", "updated", "protected_running"}),
            "created": sum(1 for row in rows if row.get("action") == "created"),
            "protected_running": sum(1 for row in rows if row.get("action") == "protected_running"),
            "skipped_removed": sum(1 for row in rows if row.get("action") == "skipped_removed"),
            "marked_offline": offline_count,
            "devices": rows,
        }

    def refresh_registered_usb_devices(self) -> list[dict[str, Any]]:
        devices = self.adb.devices()
        usb_devices = {device.serial: device for device in devices if ":" not in device.serial}
        reports: list[dict[str, Any]] = []
        phones = self.repository.list_phones(
            connection_mode="usb",
            limit=1000,
            include_disabled=True,
            include_removed=False,
        )
        for phone in phones:
            phone_id = str(phone.get("phone_id") or "")
            serial = str(phone.get("adb_serial") or "")
            current_status = str(phone.get("current_status") or "")
            if not phone_id or not serial or current_status in {"running", "disabled", "removed"}:
                continue
            device = usb_devices.get(serial)
            if device is None:
                result = PhoneHealthResult(
                    serial=serial,
                    adb_online=False,
                    authorized=False,
                    screenshot_ok=False,
                    app_detected=False,
                    media_dir_writable=False,
                    details={"error": "USB device is not connected"},
                )
                check_id = self.repository.record_health(phone_id, result)
                reports.append({"phone": phone, "result": result, "check_id": check_id, "detected": False})
                continue
            if device.state != "device":
                result = PhoneHealthResult(
                    serial=serial,
                    adb_online=device.state != "offline",
                    authorized=False,
                    screenshot_ok=False,
                    app_detected=False,
                    media_dir_writable=False,
                    details={"device_state": device.state, "detail": device.detail},
                )
                check_id = self.repository.record_health(phone_id, result)
                reports.append({"phone": phone, "result": result, "check_id": check_id, "detected": True})
                continue
            try:
                _, result, check_id = self.health_check(phone_id)
            except Exception as exc:  # noqa: BLE001
                result = PhoneHealthResult(
                    serial=serial,
                    adb_online=False,
                    authorized=False,
                    screenshot_ok=False,
                    app_detected=False,
                    media_dir_writable=False,
                    details={"error": str(exc)},
                )
                check_id = self.repository.record_health(phone_id, result)
            reports.append({"phone": phone, "result": result, "check_id": check_id, "detected": True})
        return reports

    def add_phone(
        self,
        *,
        device_name: str,
        adb_serial: str,
        account_name: str = "",
        account_type: str = "marketing",
        connection_mode: str = "usb",
        app_package: str = "",
        remote_video_dir: str = "/sdcard/DCIM/Camera",
        note: str = "",
    ) -> str:
        clean_device_name = str(device_name or "").strip()
        if not clean_device_name:
            clean_device_name = self.detect_name_for_serial(adb_serial)
        phone_id = self.repository.add_phone(
            device_name=clean_device_name,
            adb_serial=adb_serial,
            account_name=account_name,
            account_type=account_type,
            connection_mode=connection_mode,
            pairing_status="paired" if connection_mode == "usb" else "unpaired",
            authorization_status="unknown",
            current_status="offline",
            app_package=app_package,
            remote_video_dir=remote_video_dir,
            note=note,
        )
        self.refresh_registered_phone_status(phone_id)
        return phone_id

    def refresh_registered_phone_status(self, phone_id: str) -> None:
        try:
            phone = self.repository.get(phone_id)
            if not phone:
                return
            result = self.adb.health_check(
                str(phone["adb_serial"]),
                app_package=str(phone.get("app_package") or ""),
                remote_video_dir=str(phone.get("remote_video_dir") or "/sdcard/DCIM/Camera"),
            )
            self.repository.record_health(phone_id, result)
        except Exception:
            # Registration should still succeed; a later health check can surface the exact failure.
            return

    def pair_wireless(
        self,
        *,
        host: str,
        pair_port: str | int,
        pair_code: str,
        connect_port: str | int | None = None,
        device_name: str = "",
        account_name: str = "",
        account_type: str = "marketing",
        app_package: str = "",
        remote_video_dir: str = "/sdcard/DCIM/Camera",
    ) -> dict[str, str]:
        connect_target = f"{host}:{connect_port}" if connect_port else ""
        pair_output = ""
        connect_output = ""
        try:
            before_serials = {device.serial for device in self.adb.devices() if device.state == "device"}
            before_connect_targets: set[str] = set()
            if not connect_target:
                before_connect_targets = {
                    service["target"]
                    for service in self.safe_mdns_services()
                    if service.get("type") == "_adb-tls-connect"
                }
            ensure_tcp_reachable(host, pair_port, "配对端口")
            pair_output = self.adb.pair(host, pair_port, pair_code)
            device = self.wait_for_new_connected_device(before_serials, timeout_seconds=5)
            if device is None:
                if not connect_target:
                    connect_service = self.wait_for_connect_service(
                        before_targets=before_connect_targets,
                        preferred_host=host,
                        timeout_seconds=20,
                    )
                    connect_target = connect_service["target"]
                else:
                    ensure_tcp_reachable(host, connect_port, "连接端口")
                connect_output = self.adb.connect_target(connect_target)
                device = self.require_connected_device(connect_target)
            detected_name = device_name
            if not detected_name:
                detected_name = self.detect_device_name(device)
            phone_id = self.repository.upsert_from_adb_device(
                device,
                device_name=detected_name,
                account_name=account_name,
                account_type=account_type,
                app_package=app_package,
                remote_video_dir=remote_video_dir,
            )
        except Exception:
            if connect_target:
                self.mark_existing_wireless_failure(connect_target)
            raise
        return {
            "phone_id": phone_id,
            "serial": device.serial,
            "pair_output": pair_output,
            "connect_output": connect_output,
            "connect_target": connect_target or device.serial,
        }

    def create_wireless_pairing_qr(self) -> dict[str, str]:
        service_name = "studio-" + random_token(10)
        password = random_token(16)
        qr_payload = f"WIFI:T:ADB;S:{service_name};P:{password};;"
        return {
            "service_name": service_name,
            "password": password,
            "qr_payload": qr_payload,
            "qr_data_uri": qr_svg_data_uri(qr_payload),
        }

    def pair_wireless_qr(
        self,
        *,
        service_name: str,
        password: str,
        device_name: str = "",
        account_name: str = "",
        account_type: str = "marketing",
        app_package: str = "",
        remote_video_dir: str = "/sdcard/DCIM/Camera",
        timeout_seconds: int = 60,
    ) -> dict[str, str]:
        before_serials = {device.serial for device in self.adb.devices() if device.state == "device"}
        before_connect_targets = {
            service["target"]
            for service in self.safe_mdns_services()
            if service.get("type") == "_adb-tls-connect"
        }
        pairing_service = self.wait_for_pairing_service(service_name, timeout_seconds=timeout_seconds)
        pair_target = pairing_service["target"]
        pair_output = self.adb.pair_target(pair_target, password)
        device = self.wait_for_new_connected_device(before_serials, timeout_seconds=8)
        connect_output = ""
        connect_target = ""
        if device is None:
            connect_service = self.wait_for_connect_service(
                before_targets=before_connect_targets,
                preferred_host=target_host(pair_target),
                timeout_seconds=20,
            )
            connect_target = connect_service["target"]
            connect_output = self.adb.connect_target(connect_target)
            device = self.require_connected_device(connect_target)
        detected_name = device_name or self.detect_device_name(device)
        phone_id = self.repository.upsert_from_adb_device(
            device,
            device_name=detected_name,
            account_name=account_name,
            account_type=account_type,
            app_package=app_package,
            remote_video_dir=remote_video_dir,
        )
        return {
            "phone_id": phone_id,
            "serial": device.serial,
            "service_name": service_name,
            "pair_target": pair_target,
            "connect_target": connect_target or device.serial,
            "pair_output": pair_output,
            "connect_output": connect_output,
        }

    def connect_wireless(self, *, host: str, connect_port: str | int) -> dict[str, str]:
        serial = f"{host}:{connect_port}"
        try:
            output = self.adb.connect(host, connect_port)
            device = self.require_connected_device(serial)
            phone_id = self.repository.upsert_from_adb_device(device, device_name=self.detect_device_name(device))
        except Exception:
            self.mark_existing_wireless_failure(serial)
            raise
        return {"phone_id": phone_id, "serial": serial, "output": output}

    def detect_name_for_serial(self, serial: str) -> str:
        clean_serial = str(serial or "").strip()
        if not clean_serial:
            return ""
        explorer_names = portable_device_names_by_serial()
        devices = {device.serial: device for device in self.adb.devices()}
        device = devices.get(clean_serial) or ADBDevice(serial=clean_serial, state="")
        return self.detect_device_name(device, explorer_names=explorer_names)

    def default_device_name(
        self,
        device: ADBDevice,
        *,
        existing_name: str = "",
        explorer_names: dict[str, str] | None = None,
    ) -> str:
        current_name = str(existing_name or "").strip()
        if current_name and current_name.casefold() != device.serial.casefold():
            return ""
        return self.detect_device_name(device, explorer_names=explorer_names)

    def detect_device_name(
        self,
        device: ADBDevice,
        *,
        explorer_names: dict[str, str] | None = None,
    ) -> str:
        names = explorer_names if explorer_names is not None else portable_device_names_by_serial()
        explorer_name = str(names.get(device.serial.casefold(), "")).strip()
        if explorer_name:
            return explorer_name
        if device.state == "device":
            try:
                return self.adb.get_device_name(device.serial)
            except ADBManagerError:
                pass
        return device.serial

    def require_connected_device(self, serial: str) -> ADBDevice:
        last_device: ADBDevice | None = None
        for attempt in range(3):
            devices = {device.serial: device for device in self.adb.devices()}
            last_device = devices.get(serial)
            if last_device and last_device.state == "device":
                return last_device
            if attempt < 2:
                time.sleep(1)
        if not last_device:
            raise ADBManagerError(f"ADB 已执行连接，但设备列表中没有看到 {serial}。请确认手机和电脑在同一网络，并且手机无线调试连接端口没有变化。")
        if last_device.state == "unauthorized":
            raise ADBManagerError(f"{serial} 已出现在 ADB 设备列表，但还未授权。请在手机上确认调试授权后重试。")
        raise ADBManagerError(f"{serial} 当前 ADB 状态是 {last_device.state}，还不能登记为可用手机。")

    def wait_for_new_connected_device(self, before_serials: set[str], *, timeout_seconds: int) -> ADBDevice | None:
        deadline = time.monotonic() + max(1, timeout_seconds)
        while time.monotonic() < deadline:
            for device in self.adb.devices():
                if device.state == "device" and device.serial not in before_serials:
                    return device
            time.sleep(1)
        return None

    def wait_for_pairing_service(self, service_name: str, *, timeout_seconds: int) -> dict[str, str]:
        deadline = time.monotonic() + max(5, timeout_seconds)
        last_services: list[dict[str, str]] = []
        while time.monotonic() < deadline:
            last_services = self.adb.mdns_services()
            for service in last_services:
                if service.get("instance") == service_name and service.get("type") == "_adb-tls-pairing":
                    return service
            time.sleep(1)
        names = ", ".join(service.get("service", "") for service in last_services if service.get("type") == "_adb-tls-pairing")
        raise ADBManagerError(
            f"没有发现手机扫码后的 ADB 配对服务 {service_name}。请确认手机已扫描二维码，并且电脑和手机在同一 Wi-Fi 网络。"
            + (f" 当前发现的配对服务：{names}" if names else "")
        )

    def wait_for_connect_service(
        self,
        *,
        before_targets: set[str],
        preferred_host: str,
        timeout_seconds: int,
    ) -> dict[str, str]:
        deadline = time.monotonic() + max(5, timeout_seconds)
        last_services: list[dict[str, str]] = []
        while time.monotonic() < deadline:
            last_services = self.adb.mdns_services()
            candidates = [
                service
                for service in last_services
                if service.get("type") == "_adb-tls-connect" and service.get("target") not in before_targets
            ]
            if candidates:
                same_host = [service for service in candidates if target_host(service.get("target", "")) == preferred_host]
                return (same_host or candidates)[0]
            time.sleep(1)
        names = ", ".join(service.get("service", "") for service in last_services if service.get("type") == "_adb-tls-connect")
        raise ADBManagerError(
            "配对已执行，但没有发现新的 ADB 连接服务。请确认手机仍停留在无线调试页面，并稍后重试。"
            + (f" 当前发现的连接服务：{names}" if names else "")
        )

    def safe_mdns_services(self) -> list[dict[str, str]]:
        try:
            return self.adb.mdns_services()
        except ADBManagerError:
            return []

    def mark_existing_wireless_failure(self, serial: str) -> None:
        existing = self.repository.get_by_serial(serial)
        if not existing:
            return
        self.repository.update_status(
            str(existing["phone_id"]),
            "error",
            authorization_status="unknown",
            pairing_status="authorization_failed",
        )

    def health_check(self, phone_id: str) -> tuple[dict[str, Any], PhoneHealthResult, str]:
        phone = self.repository.get(phone_id)
        if not phone:
            raise KeyError(f"Phone not found: {phone_id}")
        result = self.adb.health_check(
            str(phone["adb_serial"]),
            app_package=str(phone.get("app_package") or ""),
            remote_video_dir=str(phone.get("remote_video_dir") or "/sdcard/DCIM/Camera"),
        )
        check_id = self.repository.record_health(phone_id, result)
        return phone, result, check_id

    def health_check_all(self) -> list[dict[str, Any]]:
        reports: list[dict[str, Any]] = []
        phones = self.repository.list_phones(limit=1000, include_disabled=False)
        for phone in phones:
            phone_id = str(phone["phone_id"])
            try:
                _, result, check_id = self.health_check(phone_id)
            except Exception as exc:  # noqa: BLE001
                result = PhoneHealthResult(
                    serial=str(phone.get("adb_serial") or ""),
                    adb_online=False,
                    authorized=False,
                    screenshot_ok=False,
                    app_detected=False,
                    media_dir_writable=False,
                    details={"error": str(exc)},
                )
                check_id = self.repository.record_health(phone_id, result)
            reports.append({"phone": phone, "result": result, "check_id": check_id})
        return reports


def random_token(length: int) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def target_host(target: str) -> str:
    text = str(target or "").strip()
    if text.startswith("[") and "]:" in text:
        return text[1 : text.index("]:")]
    if ":" in text:
        return text.rsplit(":", 1)[0]
    return text


def ensure_tcp_reachable(host: str, port: str | int | None, label: str) -> None:
    if not port:
        return
    try:
        with socket.create_connection((str(host), int(port)), timeout=3):
            return
    except OSError as exc:
        raise ADBManagerError(
            f"电脑无法连接手机 {host}:{port} 的{label}。请确认手机和电脑在同一 Wi-Fi，"
            "无线调试页面没有关闭，端口没有填错；如果使用路由器访客网络/热点隔离/VPN，也会导致这里不通。"
        ) from exc
