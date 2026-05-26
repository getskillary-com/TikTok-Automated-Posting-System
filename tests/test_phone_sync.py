from __future__ import annotations

import shutil
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from mobile_phone_library.models import ADBDevice
from mobile_phone_library.phone_repository import PhoneRepository
from mobile_phone_library.phone_service import PhoneLibraryService
from shared.database import init_database, session


class PhoneSafeSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(".test_tmp") / self.id().replace(".", "_")
        shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "group_control.db"
        init_database(self.db_path)
        self.repository = PhoneRepository(self.db_path)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_safe_sync_keeps_running_phone_running(self) -> None:
        seed_phone(self.db_path, "PHN-RUN", "SERIAL-RUN", "Running Phone", "marketing", "running")

        result = self.repository.safe_upsert_from_adb_device(ADBDevice(serial="SERIAL-RUN", state="device"))

        self.assertEqual(result["action"], "protected_running")
        phone = self.repository.get("PHN-RUN")
        assert phone is not None
        self.assertEqual(phone["current_status"], "running")
        self.assertEqual(phone["authorization_status"], "authorized")
        self.assertEqual(phone["pairing_status"], "paired")

    def test_safe_sync_does_not_revive_removed_phone(self) -> None:
        seed_phone(self.db_path, "PHN-OLD", "SERIAL-OLD", "Old Phone", "marketing", "removed")

        result = self.repository.safe_upsert_from_adb_device(ADBDevice(serial="SERIAL-OLD", state="device"))

        self.assertEqual(result["action"], "skipped_removed")
        phone = self.repository.get("PHN-OLD")
        assert phone is not None
        self.assertEqual(phone["current_status"], "removed")

    def test_safe_sync_creates_new_phone_from_unique_removed_model_template(self) -> None:
        seed_phone(
            self.db_path,
            "PHN-REMOVED-SHOW",
            "SERIAL-OLD-SHOW",
            "Redmi 2312DRA50G",
            "showcase",
            "removed",
            app_package="com.zhiliaoapp.musically",
        )

        result = self.repository.safe_upsert_from_adb_device(
            ADBDevice(serial="SERIAL-NEW-SHOW", state="device"),
            device_name="Redmi 2312DRA50G",
        )

        self.assertEqual(result["action"], "created")
        with session(self.db_path) as connection:
            new_phone = connection.execute("SELECT * FROM phones WHERE adb_serial = 'SERIAL-NEW-SHOW'").fetchone()
            old_phone = connection.execute("SELECT current_status FROM phones WHERE phone_id = 'PHN-REMOVED-SHOW'").fetchone()
        self.assertEqual(new_phone["account_type"], "showcase")
        self.assertEqual(new_phone["app_package"], "com.zhiliaoapp.musically")
        self.assertEqual(new_phone["current_status"], "online_idle")
        self.assertEqual(old_phone["current_status"], "removed")

    def test_mark_missing_usb_devices_does_not_change_running_or_removed(self) -> None:
        seed_phone(self.db_path, "PHN-RUN", "SERIAL-RUN", "Running Phone", "marketing", "running")
        seed_phone(self.db_path, "PHN-REM", "SERIAL-REM", "Removed Phone", "marketing", "removed")
        seed_phone(self.db_path, "PHN-IDLE", "SERIAL-IDLE", "Idle Phone", "marketing", "online_idle")

        changed = self.repository.mark_missing_usb_devices_offline(set())

        self.assertEqual(changed, 1)
        with session(self.db_path) as connection:
            rows = {
                str(row["phone_id"]): str(row["current_status"])
                for row in connection.execute("SELECT phone_id, current_status FROM phones").fetchall()
            }
        self.assertEqual(rows["PHN-RUN"], "running")
        self.assertEqual(rows["PHN-REM"], "removed")
        self.assertEqual(rows["PHN-IDLE"], "offline")

    def test_add_phone_without_name_uses_detected_device_name(self) -> None:
        service = PhoneLibraryService(self.db_path)
        service.adb = FakeADB(
            devices=[ADBDevice(serial="SERIAL-A", state="device")],
            device_names={"SERIAL-A": "Samsung Galaxy A52"},
        )

        with patch("mobile_phone_library.phone_service.portable_device_names_by_serial", return_value={}):
            phone_id = service.add_phone(device_name="", adb_serial="SERIAL-A")

        phone = self.repository.get(phone_id)
        assert phone is not None
        self.assertEqual(phone["device_name"], "Samsung Galaxy A52")

    def test_safe_sync_replaces_serial_default_with_windows_device_name(self) -> None:
        seed_phone(self.db_path, "PHN-SERIAL", "SERIAL-B", "SERIAL-B", "marketing", "online_idle")
        service = PhoneLibraryService(self.db_path)
        service.adb = FakeADB(devices=[ADBDevice(serial="SERIAL-B", state="device")])

        with patch(
            "mobile_phone_library.phone_service.portable_device_names_by_serial",
            return_value={"serial-b": "Windows Phone Name"},
        ):
            service.safe_sync_devices()

        phone = self.repository.get("PHN-SERIAL")
        assert phone is not None
        self.assertEqual(phone["device_name"], "Windows Phone Name")

    def test_safe_sync_preserves_custom_device_name(self) -> None:
        seed_phone(self.db_path, "PHN-CUSTOM", "SERIAL-C", "Custom Marketing 01", "marketing", "online_idle")
        service = PhoneLibraryService(self.db_path)
        service.adb = FakeADB(devices=[ADBDevice(serial="SERIAL-C", state="device")])

        with patch(
            "mobile_phone_library.phone_service.portable_device_names_by_serial",
            return_value={"serial-c": "Windows Phone Name"},
        ):
            service.safe_sync_devices()

        phone = self.repository.get("PHN-CUSTOM")
        assert phone is not None
        self.assertEqual(phone["device_name"], "Custom Marketing 01")


def seed_phone(
    db_path: Path,
    phone_id: str,
    serial: str,
    device_name: str,
    account_type: str,
    status: str,
    *,
    app_package: str = "com.ss.android.tt.creator",
) -> None:
    now = datetime(2026, 5, 12, 10, 0, tzinfo=timezone.utc).replace(microsecond=0).isoformat()
    with session(db_path) as connection:
        connection.execute(
            """
            INSERT INTO phones(
                phone_id, device_name, adb_serial, account_type, current_status,
                app_package, remote_video_dir, created_at, updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, '/sdcard/DCIM/Camera', ?, ?)
            """,
            (phone_id, device_name, serial, account_type, status, app_package, now, now),
        )


class FakeADB:
    def __init__(self, *, devices: list[ADBDevice], device_names: dict[str, str] | None = None) -> None:
        self._devices = devices
        self._device_names = device_names or {}

    def devices(self) -> list[ADBDevice]:
        return self._devices

    def get_device_name(self, serial: str) -> str:
        return self._device_names.get(serial, serial)


if __name__ == "__main__":
    unittest.main()
