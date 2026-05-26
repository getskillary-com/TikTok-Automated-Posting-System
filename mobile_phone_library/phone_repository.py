from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from shared.database import init_database, session, utc_now

from .adb_manager import devices_to_counts
from .models import (
    ACCOUNT_TYPES,
    ADBDevice,
    ADBToolInspection,
    AUTHORIZATION_STATUSES,
    CONNECTION_MODES,
    PAIRING_STATUSES,
    PHONE_STATUSES,
    PhoneHealthResult,
)


def new_phone_id() -> str:
    return "PHN-" + uuid.uuid4().hex[:12].upper()


def new_check_id() -> str:
    return "PHC-" + uuid.uuid4().hex[:12].upper()


class PhoneRepository:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = db_path
        init_database(db_path)

    def add_phone(
        self,
        *,
        device_name: str,
        adb_serial: str,
        account_name: str = "",
        account_type: str = "marketing",
        connection_mode: str = "usb",
        pairing_status: str = "paired",
        authorization_status: str = "unknown",
        current_status: str = "offline",
        app_package: str = "",
        remote_video_dir: str = "/sdcard/DCIM/Camera",
        note: str = "",
    ) -> str:
        validate_connection_mode(connection_mode)
        validate_pairing_status(pairing_status)
        validate_authorization_status(authorization_status)
        validate_phone_status(current_status)
        clean_account_type = normalize_account_type(account_type)
        clean_serial = adb_serial.strip()
        if not clean_serial:
            raise ValueError("ADB serial cannot be empty.")
        clean_name = device_name.strip() or clean_serial
        now = utc_now()

        with session(self.db_path) as connection:
            existing = connection.execute(
                "SELECT phone_id FROM phones WHERE adb_serial = ? LIMIT 1",
                (clean_serial,),
            ).fetchone()
            if existing:
                phone_id = str(existing["phone_id"])
                connection.execute(
                    """
                    UPDATE phones
                    SET device_name = ?, account_name = ?, account_type = ?, connection_mode = ?, pairing_status = ?,
                        authorization_status = ?, current_status = ?, app_package = ?, remote_video_dir = ?,
                        note = ?, last_online_at = CASE WHEN ? = 'online_idle' THEN ? ELSE last_online_at END,
                        updated_at = ?
                    WHERE phone_id = ?
                    """,
                    (
                        clean_name,
                        account_name,
                        clean_account_type,
                        connection_mode,
                        pairing_status,
                        authorization_status,
                        current_status,
                        app_package,
                        remote_video_dir or "/sdcard/DCIM/Camera",
                        note,
                        current_status,
                        now,
                        now,
                        phone_id,
                    ),
                )
                return phone_id

            phone_id = new_phone_id()
            connection.execute(
                """
                INSERT INTO phones(
                    phone_id, device_name, adb_serial, account_name, account_type, connection_mode,
                    pairing_status, authorization_status, current_status, app_package,
                    remote_video_dir, last_online_at, note, created_at, updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    phone_id,
                    clean_name,
                    clean_serial,
                    account_name,
                    clean_account_type,
                    connection_mode,
                    pairing_status,
                    authorization_status,
                    current_status,
                    app_package,
                    remote_video_dir or "/sdcard/DCIM/Camera",
                    now if current_status == "online_idle" else None,
                    note,
                    now,
                    now,
                ),
            )
        return phone_id

    def get(self, phone_id: str) -> dict[str, Any] | None:
        with session(self.db_path) as connection:
            row = connection.execute("SELECT * FROM phones WHERE phone_id = ?", (phone_id,)).fetchone()
        return row_to_dict(row)

    def get_by_serial(self, adb_serial: str) -> dict[str, Any] | None:
        with session(self.db_path) as connection:
            row = connection.execute("SELECT * FROM phones WHERE adb_serial = ?", (adb_serial,)).fetchone()
        return row_to_dict(row)

    def list_phones(
        self,
        *,
        status: str = "",
        pairing_status: str = "",
        connection_mode: str = "",
        search: str = "",
        limit: int = 100,
        include_disabled: bool = True,
        include_removed: bool = False,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("current_status = ?")
            params.append(status)
        if pairing_status:
            clauses.append("pairing_status = ?")
            params.append(pairing_status)
        if connection_mode:
            clauses.append("connection_mode = ?")
            params.append(connection_mode)
        if search:
            clauses.append("(phone_id LIKE ? OR device_name LIKE ? OR adb_serial LIKE ? OR account_name LIKE ? OR account_type LIKE ?)")
            params.extend([f"%{search}%", f"%{search}%", f"%{search}%", f"%{search}%", f"%{search}%"])
        if not include_disabled:
            clauses.append("current_status != 'disabled'")
        if not include_removed and status != "removed":
            clauses.append("current_status != 'removed'")
        where_sql = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(max(1, min(limit, 1000)))
        with session(self.db_path) as connection:
            rows = connection.execute(
                f"SELECT * FROM phones{where_sql} ORDER BY updated_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [row_to_dict(row) for row in rows]

    def update_status(
        self,
        phone_id: str,
        current_status: str,
        *,
        authorization_status: str | None = None,
        pairing_status: str | None = None,
    ) -> None:
        validate_phone_status(current_status)
        if authorization_status is not None:
            validate_authorization_status(authorization_status)
        if pairing_status is not None:
            validate_pairing_status(pairing_status)
        fields = ["current_status = ?", "updated_at = ?"]
        params: list[Any] = [current_status, utc_now()]
        if authorization_status is not None:
            fields.append("authorization_status = ?")
            params.append(authorization_status)
        if pairing_status is not None:
            fields.append("pairing_status = ?")
            params.append(pairing_status)
        if current_status == "online_idle":
            fields.append("last_online_at = ?")
            params.append(utc_now())
        params.append(phone_id)
        with session(self.db_path) as connection:
            result = connection.execute(f"UPDATE phones SET {', '.join(fields)} WHERE phone_id = ?", params)
            if result.rowcount == 0:
                raise KeyError(f"Phone not found: {phone_id}")

    def update_phone(
        self,
        phone_id: str,
        *,
        device_name: str | None = None,
        account_name: str | None = None,
        account_type: str | None = None,
        app_package: str | None = None,
        remote_video_dir: str | None = None,
        note: str | None = None,
    ) -> None:
        fields: list[str] = []
        params: list[Any] = []
        for column, value in (
            ("device_name", device_name),
            ("account_name", account_name),
            ("account_type", normalize_account_type(account_type) if account_type is not None else None),
            ("app_package", app_package),
            ("remote_video_dir", remote_video_dir),
            ("note", note),
        ):
            if value is not None:
                fields.append(f"{column} = ?")
                params.append(value)
        if not fields:
            return
        fields.append("updated_at = ?")
        params.append(utc_now())
        params.append(phone_id)
        with session(self.db_path) as connection:
            result = connection.execute(f"UPDATE phones SET {', '.join(fields)} WHERE phone_id = ?", params)
            if result.rowcount == 0:
                raise KeyError(f"Phone not found: {phone_id}")

    def remove_phone(self, phone_id: str) -> None:
        now = utc_now()
        with session(self.db_path) as connection:
            phone = connection.execute(
                "SELECT current_status FROM phones WHERE phone_id = ?",
                (phone_id,),
            ).fetchone()
            if not phone:
                raise KeyError(f"Phone not found: {phone_id}")
            if str(phone["current_status"]) == "running":
                raise ValueError("手机正在执行任务，不能移除。请等待任务结束后再试。")
            connection.execute(
                """
                UPDATE phones
                SET current_status = 'removed',
                    authorization_status = 'unknown',
                    pairing_status = 'unpaired',
                    updated_at = ?
                WHERE phone_id = ?
                """,
                (now, phone_id),
            )

    def mark_missing_usb_devices_removed(self, detected_serials: set[str]) -> int:
        return self.mark_missing_usb_devices_offline(detected_serials)

    def mark_missing_usb_devices_offline(self, detected_serials: set[str]) -> int:
        clean_serials = sorted(serial.strip() for serial in detected_serials if serial.strip())
        clauses = [
            "connection_mode = 'usb'",
            "current_status != 'removed'",
            "current_status != 'running'",
            "current_status != 'disabled'",
        ]
        params: list[Any] = []
        if clean_serials:
            placeholders = ", ".join("?" for _ in clean_serials)
            clauses.append(f"adb_serial NOT IN ({placeholders})")
            params.extend(clean_serials)
        now = utc_now()
        with session(self.db_path) as connection:
            result = connection.execute(
                f"""
                UPDATE phones
                SET current_status = 'offline',
                    authorization_status = 'offline',
                    pairing_status = 'paired',
                    updated_at = ?
                WHERE {' AND '.join(clauses)}
                """,
                (now, *params),
            )
        return int(result.rowcount or 0)

    def upsert_from_adb_device(
        self,
        device: ADBDevice,
        *,
        device_name: str = "",
        account_name: str = "",
        account_type: str = "",
        app_package: str = "",
        remote_video_dir: str = "/sdcard/DCIM/Camera",
    ) -> str:
        current_status, authorization_status, pairing_status = status_from_adb_state(device.state)
        name = device_name or device.serial
        now = utc_now()
        with session(self.db_path) as connection:
            existing = connection.execute(
                "SELECT * FROM phones WHERE adb_serial = ? LIMIT 1",
                (device.serial,),
            ).fetchone()
            if existing:
                phone_id = str(existing["phone_id"])
                connection.execute(
                    """
                    UPDATE phones
                    SET device_name = CASE WHEN ? != '' THEN ? ELSE device_name END,
                        account_name = CASE WHEN ? != '' THEN ? ELSE account_name END,
                        account_type = CASE WHEN ? != '' THEN ? ELSE account_type END,
                        connection_mode = CASE WHEN adb_serial LIKE '%:%' THEN 'wireless' ELSE connection_mode END,
                        pairing_status = ?, authorization_status = ?, current_status = ?,
                        app_package = CASE WHEN ? != '' THEN ? ELSE app_package END,
                        remote_video_dir = CASE WHEN ? != '' THEN ? ELSE remote_video_dir END,
                        last_online_at = CASE WHEN ? = 'online_idle' THEN ? ELSE last_online_at END,
                        updated_at = ?
                    WHERE phone_id = ?
                    """,
                    (
                        device_name,
                        name,
                        account_name,
                        account_name,
                        account_type,
                        normalize_account_type(account_type),
                        pairing_status,
                        authorization_status,
                        current_status,
                        app_package,
                        app_package,
                        remote_video_dir,
                        remote_video_dir,
                        current_status,
                        now,
                        now,
                        phone_id,
                    ),
                )
                return phone_id

        return self.add_phone(
            device_name=name,
            adb_serial=device.serial,
            account_name=account_name,
            account_type=normalize_account_type(account_type),
            connection_mode="wireless" if ":" in device.serial else "usb",
            pairing_status=pairing_status,
            authorization_status=authorization_status,
            current_status=current_status,
            app_package=app_package,
            remote_video_dir=remote_video_dir,
        )

    def safe_upsert_from_adb_device(
        self,
        device: ADBDevice,
        *,
        device_name: str = "",
        account_name: str = "",
        account_type: str = "",
        app_package: str = "",
        remote_video_dir: str = "/sdcard/DCIM/Camera",
        revive_removed: bool = False,
    ) -> dict[str, Any]:
        current_status, authorization_status, pairing_status = status_from_adb_state(device.state)
        name = device_name or device.serial
        now = utc_now()
        with session(self.db_path) as connection:
            existing = connection.execute(
                "SELECT * FROM phones WHERE adb_serial = ? LIMIT 1",
                (device.serial,),
            ).fetchone()
            if existing:
                phone_id = str(existing["phone_id"])
                old_status = str(existing["current_status"] or "")
                if old_status == "running":
                    connection.execute(
                        """
                        UPDATE phones
                        SET device_name = CASE WHEN ? != '' THEN ? ELSE device_name END,
                            authorization_status = ?,
                            pairing_status = ?,
                            last_online_at = CASE WHEN ? = 'online_idle' THEN ? ELSE last_online_at END,
                            updated_at = ?
                        WHERE phone_id = ?
                        """,
                        (device_name, name, authorization_status, pairing_status, current_status, now, now, phone_id),
                    )
                    return {"phone_id": phone_id, "serial": device.serial, "action": "protected_running", "status": old_status}
                if old_status == "disabled":
                    return {"phone_id": phone_id, "serial": device.serial, "action": "skipped_disabled", "status": old_status}
                if old_status == "removed" and not revive_removed:
                    return {"phone_id": phone_id, "serial": device.serial, "action": "skipped_removed", "status": old_status}
                connection.execute(
                    """
                    UPDATE phones
                    SET device_name = CASE WHEN ? != '' THEN ? ELSE device_name END,
                        account_name = CASE WHEN ? != '' THEN ? ELSE account_name END,
                        account_type = CASE WHEN ? != '' THEN ? ELSE account_type END,
                        connection_mode = CASE WHEN adb_serial LIKE '%:%' THEN 'wireless' ELSE connection_mode END,
                        pairing_status = ?, authorization_status = ?, current_status = ?,
                        app_package = CASE WHEN ? != '' THEN ? ELSE app_package END,
                        remote_video_dir = CASE WHEN ? != '' THEN ? ELSE remote_video_dir END,
                        last_online_at = CASE WHEN ? = 'online_idle' THEN ? ELSE last_online_at END,
                        updated_at = ?
                    WHERE phone_id = ?
                    """,
                    (
                        device_name,
                        name,
                        account_name,
                        account_name,
                        account_type,
                        normalize_account_type(account_type) if account_type else "",
                        pairing_status,
                        authorization_status,
                        current_status,
                        app_package,
                        app_package,
                        remote_video_dir,
                        remote_video_dir,
                        current_status,
                        now,
                        now,
                        phone_id,
                    ),
                )
                return {"phone_id": phone_id, "serial": device.serial, "action": "updated", "status": current_status}

        template = self.find_removed_template_by_device_name(name)
        if template:
            account_name = account_name or str(template.get("account_name") or "")
            account_type = account_type or str(template.get("account_type") or "")
            app_package = app_package or str(template.get("app_package") or "")
            remote_video_dir = remote_video_dir or str(template.get("remote_video_dir") or "/sdcard/DCIM/Camera")
        clean_account_type = normalize_account_type(account_type)
        app_package = app_package or default_app_package_for_account(clean_account_type)
        phone_id = self.add_phone(
            device_name=name,
            adb_serial=device.serial,
            account_name=account_name,
            account_type=clean_account_type,
            connection_mode="wireless" if ":" in device.serial else "usb",
            pairing_status=pairing_status,
            authorization_status=authorization_status,
            current_status=current_status,
            app_package=app_package,
            remote_video_dir=remote_video_dir,
            note="auto registered by safe phone sync",
        )
        return {"phone_id": phone_id, "serial": device.serial, "action": "created", "status": current_status}

    def find_removed_template_by_device_name(self, device_name: str) -> dict[str, Any] | None:
        clean_name = str(device_name or "").strip()
        if not clean_name:
            return None
        with session(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM phones
                WHERE current_status = 'removed' AND device_name = ?
                ORDER BY updated_at DESC
                LIMIT 2
                """,
                (clean_name,),
            ).fetchall()
        if len(rows) != 1:
            return None
        return dict(rows[0])

    def save_adb_tool_status(self, inspection: ADBToolInspection) -> None:
        counts = devices_to_counts(inspection.devices)
        with session(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO adb_tool_status(
                    id, source, adb_path, version, service_status, platform_tools_status,
                    required_files, online_device_count, unauthorized_device_count,
                    offline_device_count, last_checked_at
                )
                VALUES(1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    source = excluded.source,
                    adb_path = excluded.adb_path,
                    version = excluded.version,
                    service_status = excluded.service_status,
                    platform_tools_status = excluded.platform_tools_status,
                    required_files = excluded.required_files,
                    online_device_count = excluded.online_device_count,
                    unauthorized_device_count = excluded.unauthorized_device_count,
                    offline_device_count = excluded.offline_device_count,
                    last_checked_at = excluded.last_checked_at
                """,
                (
                    inspection.source,
                    inspection.adb_path,
                    inspection.version,
                    inspection.service_status,
                    inspection.platform_tools_status,
                    json.dumps(inspection.required_files, ensure_ascii=False),
                    counts["online"],
                    counts["unauthorized"],
                    counts["offline"],
                    utc_now(),
                ),
            )

    def record_health(self, phone_id: str, result: PhoneHealthResult) -> str:
        checked_at = utc_now()
        check_id = new_check_id()
        current_status, authorization_status, pairing_status = health_to_status(result)
        with session(self.db_path) as connection:
            phone = connection.execute("SELECT phone_id FROM phones WHERE phone_id = ?", (phone_id,)).fetchone()
            if not phone:
                raise KeyError(f"Phone not found: {phone_id}")
            connection.execute(
                """
                INSERT INTO phone_health_checks(
                    check_id, phone_id, checked_at, adb_online, authorized, screenshot_ok,
                    app_detected, media_dir_writable, current_foreground_package, details
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    check_id,
                    phone_id,
                    checked_at,
                    int(result.adb_online),
                    int(result.authorized),
                    int(result.screenshot_ok),
                    int(result.app_detected),
                    int(result.media_dir_writable),
                    result.current_foreground_package,
                    json.dumps(result.details, ensure_ascii=False),
                ),
            )
            connection.execute(
                """
                UPDATE phones
                SET current_status = ?, authorization_status = ?, pairing_status = ?,
                    last_online_at = CASE WHEN ? = 'online_idle' THEN ? ELSE last_online_at END,
                    updated_at = ?
                WHERE phone_id = ?
                """,
                (
                    current_status,
                    authorization_status,
                    pairing_status,
                    current_status,
                    checked_at,
                    checked_at,
                    phone_id,
                ),
            )
        return check_id

    def list_health_checks(self, phone_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
        with session(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT * FROM phone_health_checks
                WHERE phone_id = ?
                ORDER BY checked_at DESC
                LIMIT ?
                """,
                (phone_id, max(1, min(limit, 200))),
            ).fetchall()
        return [health_row_to_dict(row) for row in rows]


def status_from_adb_state(state: str) -> tuple[str, str, str]:
    if state == "device":
        return "online_idle", "authorized", "paired"
    if state == "unauthorized":
        return "offline", "unauthorized", "authorization_failed"
    if state == "offline":
        return "offline", "offline", "paired"
    return "offline", "unknown", "unpaired"


def health_to_status(result: PhoneHealthResult) -> tuple[str, str, str]:
    if not result.adb_online:
        return "offline", "offline", "paired"
    if not result.authorized:
        return "offline", "unauthorized", "authorization_failed"
    if result.screenshot_ok and result.app_detected and result.media_dir_writable:
        return "online_idle", "authorized", "paired"
    return "error", "authorized", "paired"


def validate_connection_mode(value: str) -> None:
    if value not in CONNECTION_MODES:
        raise ValueError(f"Unsupported connection mode: {value}")


def validate_pairing_status(value: str) -> None:
    if value not in PAIRING_STATUSES:
        raise ValueError(f"Unsupported pairing status: {value}")


def validate_authorization_status(value: str) -> None:
    if value not in AUTHORIZATION_STATUSES:
        raise ValueError(f"Unsupported authorization status: {value}")


def validate_phone_status(value: str) -> None:
    if value not in PHONE_STATUSES:
        raise ValueError(f"Unsupported phone status: {value}")


def validate_account_type(value: str) -> None:
    if value not in ACCOUNT_TYPES:
        raise ValueError(f"Unsupported account type: {value}")


def normalize_account_type(value: str | None) -> str:
    text = str(value or "marketing").strip().casefold()
    aliases = {
        "market": "marketing",
        "marketing_account": "marketing",
        "营销号": "marketing",
        "yingxiao": "marketing",
        "shop": "showcase",
        "showcase_account": "showcase",
        "window": "showcase",
        "橱窗号": "showcase",
        "chuchuang": "showcase",
    }
    normalized = aliases.get(text, text)
    validate_account_type(normalized)
    return normalized


def default_app_package_for_account(account_type: str) -> str:
    if account_type == "showcase":
        return "com.zhiliaoapp.musically"
    return "com.ss.android.tt.creator"


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)


def health_row_to_dict(row: sqlite3.Row | None) -> dict[str, Any]:
    if row is None:
        return {}
    data = dict(row)
    try:
        data["details"] = json.loads(str(data.get("details") or "{}"))
    except json.JSONDecodeError:
        data["details"] = {}
    return data
