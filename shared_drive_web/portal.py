from __future__ import annotations

import shutil
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Any

from openpyxl import Workbook

from mobile_phone_library.phone_repository import PhoneRepository
from mobile_phone_library.phone_service import PhoneLibraryService
from shared.account_rules import ACCOUNT_TYPES, normalize_account_type
from shared.database import session, utc_now
from shared_drive_importer.excel_io import INPUT_COLUMNS, format_sheet, save_workbook
from shared_drive_importer.importer import (
    DEFAULT_SHARE_ROOT,
    SharedDriveImporter,
    safe_batch_id,
    unique_destination,
)
from video_library_system.video_scanner import SUPPORTED_VIDEO_EXTENSIONS


@dataclass(frozen=True)
class UploadedFile:
    filename: str
    stream: BinaryIO


class SharedDrivePortal:
    def __init__(
        self,
        *,
        share_root: str | Path = DEFAULT_SHARE_ROOT,
        db_path: str | Path | None = None,
        timezone: str = "America/Sao_Paulo",
        phone_sync_enabled: bool = False,
        phone_sync_seconds: int = 60,
        phone_full_sync_seconds: int = 300,
    ) -> None:
        self.importer = SharedDriveImporter(share_root=share_root, db_path=db_path, timezone=timezone)
        self.phone_repository = PhoneRepository(db_path)
        self.share_root = self.importer.share_root
        self.db_path = db_path
        self.timezone = timezone
        self.phone_sync_enabled = phone_sync_enabled
        self.phone_sync_seconds = max(10, int(phone_sync_seconds))
        self.phone_full_sync_seconds = max(self.phone_sync_seconds, int(phone_full_sync_seconds))
        self.phone_service = PhoneLibraryService(db_path) if phone_sync_enabled else None
        self._phone_sync_lock = threading.Lock()
        self._phone_sync_thread: threading.Thread | None = None
        self._last_phone_sync = 0.0
        self._last_full_phone_sync = 0.0
        self._last_phone_sync_result: dict[str, Any] = {}
        self.importer.ensure_layout()
        (self.share_root / "_system" / "web_uploads").mkdir(parents=True, exist_ok=True)

    def create_templates(self) -> dict[str, str]:
        return self.importer.create_templates()

    def health(self) -> dict[str, Any]:
        data = self.importer.doctor()
        data["timezone"] = self.timezone
        data["batch_count"] = len(self.list_batches())
        data["phone_sync"] = {
            "enabled": self.phone_sync_enabled,
            "light_interval_seconds": self.phone_sync_seconds,
            "full_interval_seconds": self.phone_full_sync_seconds,
            "last_result": self._last_phone_sync_result,
        }
        return data

    def phones(self) -> list[dict[str, Any]]:
        self.sync_phones_if_due()
        return self._phones_with_sync_names()

    def sync_phone_names_now(self) -> dict[str, Any]:
        sync_result = self.sync_phones_if_due(force=True)
        return {
            "sync": sync_result or {
                "enabled": self.phone_sync_enabled,
                "mode": "disabled",
                "synced_at": utc_now(),
            },
            "phones": self._phones_with_sync_names(),
        }

    def update_phone_account(self, *, phone_id: str, account_name: str, account_type: str) -> dict[str, Any]:
        clean_phone_id = str(phone_id or "").strip()
        clean_account_name = str(account_name or "").strip()
        clean_account_type = normalize_account_type(account_type)
        if not clean_phone_id:
            raise ValueError("phone_id is required")
        if clean_account_type not in ACCOUNT_TYPES:
            raise ValueError(f"unsupported account_type: {clean_account_type or 'unknown'}")
        if not self.phone_repository.get(clean_phone_id):
            raise KeyError(f"phone_id not found: {clean_phone_id}")
        self.phone_repository.update_phone(
            clean_phone_id,
            account_name=clean_account_name,
            account_type=clean_account_type,
        )
        phone = self.phone_repository.get(clean_phone_id)
        return dict(phone or {})

    def start_phone_sync(self) -> None:
        if not self.phone_sync_enabled or self._phone_sync_thread is not None:
            return
        self._phone_sync_thread = threading.Thread(target=self._phone_sync_loop, name="shared-drive-phone-sync", daemon=True)
        self._phone_sync_thread.start()

    def sync_phones_if_due(self, *, force: bool = False) -> dict[str, Any]:
        if not self.phone_sync_enabled or self.phone_service is None:
            return {}
        now = time.monotonic()
        full_due = force or now - self._last_full_phone_sync >= self.phone_full_sync_seconds
        light_due = force or full_due or now - self._last_phone_sync >= self.phone_sync_seconds
        if not light_due:
            return self._last_phone_sync_result
        return self.sync_phones(full=full_due)

    def sync_phones(self, *, full: bool = False) -> dict[str, Any]:
        if not self.phone_sync_enabled or self.phone_service is None:
            return {}
        if not self._phone_sync_lock.acquire(blocking=False):
            return self._last_phone_sync_result
        try:
            result = self.phone_service.safe_sync_devices(register_new=full, mark_missing=full)
            now = time.monotonic()
            self._last_phone_sync = now
            if full:
                self._last_full_phone_sync = now
            self._last_phone_sync_result = {
                **result,
                "mode": "full" if full else "light",
                "synced_at": utc_now(),
            }
            return self._last_phone_sync_result
        except Exception as exc:  # noqa: BLE001
            self._last_phone_sync_result = {
                "mode": "full" if full else "light",
                "synced_at": utc_now(),
                "error": str(exc),
            }
            return self._last_phone_sync_result
        finally:
            self._phone_sync_lock.release()

    def _phones_with_sync_names(self) -> list[dict[str, Any]]:
        phones = self.importer.list_phones()
        sync_devices = {
            str(device.get("serial") or "").casefold(): device
            for device in self._last_phone_sync_result.get("devices", [])
            if isinstance(device, dict)
        }
        for phone in phones:
            device = sync_devices.get(str(phone.get("adb_serial") or "").casefold())
            if not device:
                phone.setdefault("explorer_name", "")
                phone.setdefault("sync_device_name", "")
                continue
            phone["explorer_name"] = str(device.get("explorer_name") or "")
            phone["sync_device_name"] = str(device.get("device_name") or "")
            phone["sync_action"] = str(device.get("action") or "")
        return phones

    def _phone_sync_loop(self) -> None:
        while True:
            self.sync_phones_if_due()
            time.sleep(self.phone_sync_seconds)

    def list_batches(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with session(self.db_path) as connection:
            batches = connection.execute(
                """
                SELECT *
                FROM shared_import_batches
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (max(1, min(limit, 500)),),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for batch in batches:
            item = dict(batch)
            rows = self.importer.status_rows(str(item["batch_id"]))
            item["items"] = rows
            item["latest_task_status"] = latest_task_status(rows)
            item["task_status_summary"] = task_status_summary(rows)
            item["failure_count"] = sum(1 for row in rows if str(row.get("failure_reason") or ""))
            result.append(item)
        return result

    def get_batch(self, batch_id: str) -> dict[str, Any]:
        clean_batch_id = safe_batch_id(batch_id)
        with session(self.db_path) as connection:
            batch = connection.execute(
                "SELECT * FROM shared_import_batches WHERE batch_id = ?",
                (clean_batch_id,),
            ).fetchone()
        if not batch:
            raise KeyError(f"batch_id not found: {clean_batch_id}")
        data = dict(batch)
        data["items"] = self.importer.status_rows(clean_batch_id)
        data["latest_task_status"] = latest_task_status(data["items"])
        data["task_status_summary"] = task_status_summary(data["items"])
        return data

    def template_path(self, name: str) -> Path:
        self.create_templates()
        if name == "task_template.xlsx":
            return self.share_root / "templates" / "task_template.xlsx"
        if name == "phones.xlsx":
            return self.share_root / "templates" / "phones.xlsx"
        raise KeyError(f"template not found: {name}")

    def status_path(self, batch_id: str) -> Path:
        clean_batch_id = safe_batch_id(batch_id)
        self.importer.export_status(batch_id=clean_batch_id)
        path = self.share_root / "status" / clean_batch_id / "status.xlsx"
        if not path.exists():
            raise KeyError(f"status.xlsx not found for batch: {clean_batch_id}")
        return path

    def upload_batch(
        self,
        *,
        batch_id: str,
        tasks_file: UploadedFile,
        video_files: list[UploadedFile],
        auto_import: bool = True,
    ) -> dict[str, Any]:
        clean_batch_id = safe_batch_id(batch_id) if batch_id.strip() else generated_batch_id()
        if tasks_file.filename.casefold().endswith(".xlsx") is False:
            raise ValueError("tasks file must be .xlsx")
        if not video_files:
            raise ValueError("at least one video file is required")

        inbox_dir = self.share_root / "inbox" / clean_batch_id
        if inbox_dir.exists():
            raise ValueError(f"batch already exists in inbox: {clean_batch_id}")

        staging_dir = unique_destination(self.share_root / "_system" / "web_uploads" / f"{clean_batch_id}.uploading")
        staging_dir.mkdir(parents=True, exist_ok=False)
        try:
            write_uploaded_file(tasks_file, staging_dir / "tasks.xlsx")
            seen_video_names: set[str] = set()
            saved_videos: list[str] = []
            for uploaded in video_files:
                filename = safe_filename(uploaded.filename)
                suffix = Path(filename).suffix.casefold()
                if suffix not in SUPPORTED_VIDEO_EXTENSIONS:
                    raise ValueError(f"unsupported video extension: {filename}")
                if filename.casefold() in seen_video_names:
                    raise ValueError(f"duplicate uploaded video filename: {filename}")
                seen_video_names.add(filename.casefold())
                write_uploaded_file(uploaded, staging_dir / filename)
                saved_videos.append(filename)
            (staging_dir / ".ready").write_text(utc_now(), encoding="utf-8")
            shutil.move(str(staging_dir), str(inbox_dir))
        except Exception:
            shutil.rmtree(staging_dir, ignore_errors=True)
            raise

        import_result = None
        status_paths: list[str] = []
        if auto_import:
            import_result = self.importer.process_inbox_batch(inbox_dir)
            status_paths = [str(path) for path in self.importer.export_status(batch_id=clean_batch_id)]

        return {
            "batch_id": clean_batch_id,
            "videos": saved_videos,
            "import_result": import_result.__dict__ if import_result else None,
            "status_paths": status_paths,
            "batch": self.get_batch(clean_batch_id),
        }

    def submit_task(
        self,
        *,
        phone_id: str,
        scheduled_at: str,
        caption: str,
        video_file: UploadedFile,
        note: str = "",
        product_search_title: str = "",
        product_publish_name: str = "",
    ) -> dict[str, Any]:
        clean_phone_id = phone_id.strip()
        clean_scheduled_at = scheduled_at.strip()
        clean_caption = caption.strip()
        clean_note = note.strip()
        clean_product_search_title = product_search_title.strip()
        clean_product_publish_name = product_publish_name.strip()

        if not clean_phone_id:
            raise ValueError("phone_id is required")
        if not clean_scheduled_at:
            raise ValueError("scheduled_at is required")
        if not clean_caption:
            raise ValueError("caption is required")

        phone = self.importer.get_phone(clean_phone_id)
        if not phone:
            raise ValueError(f"phone_id not found: {clean_phone_id}")
        phone_status = str(phone.get("current_status") or "").strip()
        if phone_status in {"disabled", "removed"}:
            raise ValueError(f"phone is not allowed for task submission: {clean_phone_id} status={phone_status}")
        if phone_status not in {"online_idle", "running"}:
            raise ValueError(f"phone is not ready for task submission: {clean_phone_id} status={phone_status or 'unknown'}")

        account_type = normalize_account_type(str(phone.get("account_type") or "marketing"))
        if account_type == "marketing":
            if clean_product_search_title or clean_product_publish_name:
                raise ValueError("marketing task cannot contain product fields")
            publish_mode = "scheduled"
            product_name = ""
        elif account_type == "showcase":
            if not clean_product_search_title:
                raise ValueError("product_search_title is required for showcase task")
            if not clean_product_publish_name:
                raise ValueError("product_publish_name is required for showcase task")
            publish_mode = "scheduled"
            product_name = clean_product_publish_name
        else:
            raise ValueError(f"unsupported account_type: {account_type or 'unknown'}")

        source_filename = safe_filename(video_file.filename)
        suffix = Path(source_filename).suffix.casefold()
        if suffix not in SUPPORTED_VIDEO_EXTENSIONS:
            raise ValueError(f"unsupported video extension: {source_filename}")

        clean_batch_id = safe_batch_id(generated_batch_id(prefix="task"))
        unique_video_name = f"{clean_batch_id}{suffix}"
        inbox_dir = self.share_root / "inbox" / clean_batch_id
        staging_dir = unique_destination(self.share_root / "_system" / "web_uploads" / f"{clean_batch_id}.uploading")
        staging_dir.mkdir(parents=True, exist_ok=False)
        try:
            write_uploaded_file(video_file, staging_dir / unique_video_name)
            write_single_task_xlsx(
                staging_dir / "tasks.xlsx",
                row={
                    "row_id": "row-1",
                    "phone_id": clean_phone_id,
                    "video_file": unique_video_name,
                    "caption": clean_caption,
                    "scheduled_at": clean_scheduled_at,
                    "publish_mode": publish_mode,
                    "product_link": "",
                    "product_name": product_name,
                    "product_search_title": clean_product_search_title,
                    "product_publish_name": clean_product_publish_name,
                    "max_retries": "3",
                    "note": clean_note,
                },
            )
            (staging_dir / ".ready").write_text(utc_now(), encoding="utf-8")
            shutil.move(str(staging_dir), str(inbox_dir))
        except Exception:
            shutil.rmtree(staging_dir, ignore_errors=True)
            raise

        import_result = self.importer.process_inbox_batch(inbox_dir)
        status_paths = [str(path) for path in self.importer.export_status(batch_id=clean_batch_id)]
        return {
            "batch_id": clean_batch_id,
            "row_id": "row-1",
            "phone_id": clean_phone_id,
            "account_type": account_type,
            "publish_mode": publish_mode,
            "video": unique_video_name,
            "import_result": import_result.__dict__,
            "status_paths": status_paths,
            "batch": self.get_batch(clean_batch_id),
        }


def generated_batch_id(prefix: str = "web") -> str:
    stamp = utc_now().replace(":", "").replace("+00:00", "Z")
    return f"{prefix}-{stamp}-{uuid.uuid4().hex[:8]}"


def safe_filename(filename: str) -> str:
    name = Path(str(filename or "")).name.strip()
    if not name or name in {".", ".."}:
        raise ValueError("uploaded file has an invalid filename")
    if any(char in name for char in '<>:"/\\|?*'):
        raise ValueError(f"uploaded filename contains unsafe characters: {name}")
    return name


def write_uploaded_file(uploaded: UploadedFile, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as target:
        shutil.copyfileobj(uploaded.stream, target, length=1024 * 1024)
    if destination.stat().st_size <= 0:
        raise ValueError(f"uploaded file is empty: {uploaded.filename}")


def write_single_task_xlsx(path: Path, *, row: dict[str, str]) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "tasks"
    sheet.append(INPUT_COLUMNS)
    sheet.append([row.get(column, "") for column in INPUT_COLUMNS])
    format_sheet(sheet)
    save_workbook(workbook, path)


def latest_task_status(rows: list[dict[str, Any]]) -> str:
    priority = {
        "failed": 0,
        "running": 1,
        "pending": 2,
        "ready": 2,
        "debug_ready": 3,
        "published": 4,
        "dry_run": 5,
    }
    statuses = [str(row.get("task_status") or "") for row in rows if str(row.get("task_status") or "")]
    if not statuses:
        return ""
    return sorted(statuses, key=lambda status: priority.get(status, 9))[0]


def task_status_summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for row in rows:
        status = str(row.get("task_status") or "")
        if status:
            summary[status] = summary.get(status, 0) + 1
    return summary
