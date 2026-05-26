from __future__ import annotations

import base64
import json
import shutil
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from copywriting_library.caption_repository import CaptionRepository
from copywriting_library.caption_service import CaptionLibraryService
from execution_queue.queue_service import ExecutionQueueService
from execution_queue.worker_manager import WorkerManager, WorkerManagerOptions
from execution_queue.worker_state import WorkerStateRepository
from mobile_phone_library.phone_repository import PhoneRepository
from mobile_phone_library.phone_service import PhoneLibraryService
from product_library.product_repository import ProductRepository
from release_task_list.task_repository import ReleaseTaskRepository
from release_task_list.task_service import ReleaseTaskService
from run_log.log_service import RunLogService
from shared.database import init_database, session
from video_library_system.models import SUPPORTED_VIDEO_EXTENSIONS
from video_library_system.video_repository import VideoRepository
from video_library_system.video_service import VideoLibraryService

from .serializers import parse_bool, parse_int, parse_tags, to_jsonable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
UPLOAD_VIDEO_DIR = PROJECT_ROOT / "storage" / "uploaded_videos"
UPLOAD_IMPORT_DIR = PROJECT_ROOT / "storage" / "uploaded_imports"
UPLOAD_ROOT = PROJECT_ROOT / "upload_system"
UPLOAD_CONFIG_PATH = UPLOAD_ROOT / "config.json"
CAPTION_PRESETS_PATH = UPLOAD_ROOT / "workflow_steps" / "caption_presets.json"
CAPTION_IMPORT_EXTENSIONS = {".txt", ".json", ".csv"}
TASK_IMPORT_EXTENSIONS = {".csv"}
USB_MONITOR_INTERVAL_SECONDS = 300


class ControlCenterService:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = db_path
        init_database(db_path)
        self.video_repo = VideoRepository(db_path)
        self.video_service = VideoLibraryService(db_path)
        self.caption_repo = CaptionRepository(db_path)
        self.caption_service = CaptionLibraryService(db_path)
        self.product_repo = ProductRepository(db_path)
        self.phone_repo = PhoneRepository(db_path)
        self.phone_service = PhoneLibraryService(db_path)
        self.task_repo = ReleaseTaskRepository(db_path)
        self.task_service = ReleaseTaskService(db_path)
        self.queue_service = ExecutionQueueService(db_path)
        self.log_service = RunLogService(db_path)
        self._worker_lock = threading.Lock()
        self._worker_manager: WorkerManager | None = None
        self._worker_thread: threading.Thread | None = None
        self._worker_outcome: dict[str, Any] | None = None
        self._worker_error: str = ""
        self._worker_started_at: str = ""
        self._worker_stop_requested_at: str = ""
        self._worker_options: dict[str, Any] = {}
        self._usb_monitor_lock = threading.Lock()
        self._usb_monitor_stop = threading.Event()
        self._usb_monitor_thread: threading.Thread | None = None
        self._usb_monitor_last_result: list[dict[str, Any]] = []
        self._usb_monitor_last_error: str = ""
        self._usb_monitor_last_checked_at: str = ""

    def health(self) -> dict[str, Any]:
        return {"status": "ok", "database_ready": True, "modules": self.summary_counts()}

    def summary_counts(self) -> dict[str, Any]:
        with session(self.db_path) as connection:
            return {
                "videos": table_count(connection, "videos", "status != 'removed'"),
                "captions": table_count(connection, "captions", "status != 'removed'"),
                "products": table_count(connection, "products", "status != 'removed'"),
                "phones": table_count(connection, "phones"),
                "release_tasks": table_count(connection, "release_tasks", "status != 'removed'"),
                "execution_logs": table_count(connection, "execution_logs"),
                "pending_tasks": table_count(connection, "release_tasks", "status IN ('pending', 'ready')"),
                "failed_tasks": table_count(connection, "release_tasks", "status = 'failed'"),
            }

    def list_videos(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        return self.video_repo.list_videos(
            status=str(query.get("status") or ""),
            tag=str(query.get("tag") or ""),
            batch_name=str(query.get("batch") or query.get("batch_name") or ""),
            search=str(query.get("search") or ""),
            limit=parse_int(query.get("limit"), default=50, minimum=1, maximum=1000),
            include_removed=parse_bool(query.get("include_removed"), default=False),
        )

    def import_video(self, body: dict[str, Any]) -> dict[str, Any]:
        result = self.video_service.import_file(
            str(body.get("path") or ""),
            title=str(body.get("title") or ""),
            tags=parse_tags(body.get("tags")),
            batch_name=str(body.get("batch") or body.get("batch_name") or ""),
            note=str(body.get("note") or ""),
        )
        return to_jsonable(result)

    def choose_video_folder(self, body: dict[str, Any]) -> dict[str, Any]:
        folder = choose_local_folder(
            title=str(body.get("title") or "选择视频文件夹"),
            initial_dir=str(body.get("initial_dir") or body.get("folder") or ""),
        )
        return {"folder": folder}

    def scan_video_folder(self, body: dict[str, Any]) -> dict[str, Any]:
        folder = str(body.get("folder") or body.get("path") or "").strip()
        if not folder:
            raise ValueError("Please choose a video folder to scan.")
        results = self.video_service.scan_folder(
            folder,
            recursive=parse_bool(body.get("recursive"), default=True),
            tags=parse_tags(body.get("tags")),
            batch_name=str(body.get("batch") or body.get("batch_name") or ""),
            note=str(body.get("note") or ""),
        )
        imported = sum(1 for result in results if not result.duplicate)
        duplicates = sum(1 for result in results if result.duplicate)
        return {"imported": imported, "duplicates": duplicates, "total": len(results), "results": to_jsonable(results)}

    def import_uploaded_video(self, body: dict[str, Any]) -> dict[str, Any]:
        file_part = body.get("file")
        if not isinstance(file_part, dict):
            raise ValueError("Please choose a video file to upload.")
        content = file_part.get("content")
        if not isinstance(content, bytes) or not content:
            raise ValueError("Uploaded video file is empty.")
        original_filename = str(file_part.get("filename") or "video.mp4")
        target_path = save_uploaded_video(original_filename, content)
        try:
            result = self.video_service.import_file(
                target_path,
                title=str(body.get("title") or ""),
                tags=parse_tags(body.get("tags")),
                batch_name=str(body.get("batch") or body.get("batch_name") or ""),
                note=str(body.get("note") or ""),
            )
        except Exception:
            target_path.unlink(missing_ok=True)
            raise
        data = to_jsonable(result)
        data["uploaded_path"] = str(target_path)
        data["original_filename"] = original_filename
        return data

    def update_video_status(self, video_id: str, body: dict[str, Any]) -> dict[str, Any]:
        status = str(body.get("status") or "")
        self.video_repo.update_status(video_id, status)
        return {"video_id": video_id, "status": status}

    def remove_video(self, video_id: str) -> dict[str, Any]:
        self.video_repo.remove_video(video_id)
        return {"video_id": video_id, "status": "removed"}

    def remove_videos(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.video_repo.remove_videos(parse_id_values(body, "video_ids", "ids"))

    def get_video(self, video_id: str) -> dict[str, Any]:
        video = self.video_repo.get(video_id)
        if not video:
            raise KeyError(f"Video not found: {video_id}")
        return video

    def list_captions(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        return self.caption_repo.list_captions(
            status=str(query.get("status") or ""),
            tag=str(query.get("tag") or ""),
            platform=str(query.get("platform") or ""),
            search=str(query.get("search") or ""),
            limit=parse_int(query.get("limit"), default=50, minimum=1, maximum=1000),
            include_removed=parse_bool(query.get("include_removed"), default=False),
        )

    def add_caption(self, body: dict[str, Any]) -> dict[str, Any]:
        result = self.caption_service.add_caption(
            str(body.get("content") or ""),
            tags=parse_tags(body.get("tags")),
            platform=str(body.get("platform") or ""),
            account_scope=str(body.get("account_scope") or ""),
            note=str(body.get("note") or ""),
        )
        return to_jsonable(result)

    def import_captions(self, body: dict[str, Any]) -> dict[str, Any]:
        path = self.import_path_from_body(body, allowed_suffixes=CAPTION_IMPORT_EXTENSIONS)
        results = self.caption_service.import_file(
            path,
            tags=parse_tags(body.get("tags")),
            platform=str(body.get("platform") or ""),
            account_scope=str(body.get("account_scope") or ""),
            note=str(body.get("note") or ""),
        )
        imported = sum(1 for result in results if not result.duplicate)
        duplicates = sum(1 for result in results if result.duplicate)
        return {
            "imported": imported,
            "duplicates": duplicates,
            "total": len(results),
            "results": to_jsonable(results),
            "source_path": str(path),
        }

    def get_caption(self, caption_id: str) -> dict[str, Any]:
        caption = self.caption_repo.get(caption_id)
        if not caption:
            raise KeyError(f"Caption not found: {caption_id}")
        return caption

    def edit_caption(self, caption_id: str, body: dict[str, Any]) -> dict[str, Any]:
        self.caption_repo.update_caption(
            caption_id,
            content=optional_body_value(body, "content"),
            tags=parse_tags(body.get("tags")) if "tags" in body else None,
            platform=optional_body_value(body, "platform"),
            account_scope=optional_body_value(body, "account_scope"),
            note=optional_body_value(body, "note"),
        )
        return self.get_caption(caption_id)

    def update_caption_status(self, caption_id: str, body: dict[str, Any]) -> dict[str, Any]:
        status = str(body.get("status") or "")
        self.caption_repo.update_status(caption_id, status)
        return {"caption_id": caption_id, "status": status}

    def remove_caption(self, caption_id: str) -> dict[str, Any]:
        self.caption_repo.remove_caption(caption_id)
        return {"caption_id": caption_id, "status": "removed"}

    def remove_captions(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.caption_repo.remove_captions(parse_id_values(body, "caption_ids", "ids"))

    def bind_caption_video(self, body: dict[str, Any]) -> dict[str, Any]:
        binding_id = self.caption_repo.bind_video(
            str(body.get("caption_id") or ""),
            str(body.get("video_id") or ""),
            note=str(body.get("note") or ""),
        )
        return {"binding_id": binding_id}

    def list_caption_bindings(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        return self.caption_repo.list_video_bindings(
            caption_id=str(query.get("caption_id") or ""),
            video_id=str(query.get("video_id") or ""),
        )

    def list_products(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        return self.product_repo.list_products(
            status=str(query.get("status") or ""),
            account_scope=str(query.get("account_scope") or ""),
            search=str(query.get("search") or ""),
            limit=parse_int(query.get("limit"), default=50, minimum=1, maximum=1000),
            include_removed=parse_bool(query.get("include_removed"), default=False),
        )

    def add_product(self, body: dict[str, Any]) -> dict[str, Any]:
        product_id = self.product_repo.add_product(
            title=str(body.get("title") or ""),
            search_title=str(body.get("search_title") or body.get("product_name") or ""),
            publish_name=str(body.get("publish_name") or ""),
            product_link=str(body.get("product_link") or ""),
            account_scope=str(body.get("account_scope") or ""),
            note=str(body.get("note") or ""),
        )
        return {"product_id": product_id, "product": self.get_product(product_id)}

    def get_product(self, product_id: str) -> dict[str, Any]:
        product = self.product_repo.get(product_id)
        if not product:
            raise KeyError(f"Product not found: {product_id}")
        return product

    def edit_product(self, product_id: str, body: dict[str, Any]) -> dict[str, Any]:
        self.product_repo.update_product(
            product_id,
            title=optional_body_value(body, "title"),
            search_title=optional_body_value(body, "search_title", "product_name"),
            publish_name=optional_body_value(body, "publish_name"),
            product_link=optional_body_value(body, "product_link"),
            account_scope=optional_body_value(body, "account_scope"),
            note=optional_body_value(body, "note"),
        )
        return self.get_product(product_id)

    def update_product_status(self, product_id: str, body: dict[str, Any]) -> dict[str, Any]:
        status = str(body.get("status") or "")
        self.product_repo.update_status(product_id, status)
        return {"product_id": product_id, "status": status}

    def remove_product(self, product_id: str) -> dict[str, Any]:
        self.product_repo.remove_product(product_id)
        return {"product_id": product_id, "status": "removed"}

    def list_phones(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        return self.phone_repo.list_phones(
            status=str(query.get("status") or ""),
            pairing_status=str(query.get("pairing_status") or ""),
            connection_mode=str(query.get("connection_mode") or ""),
            search=str(query.get("search") or ""),
            limit=parse_int(query.get("limit"), default=50, minimum=1, maximum=1000),
            include_disabled=parse_bool(query.get("include_disabled"), default=True),
            include_removed=parse_bool(query.get("include_removed"), default=False),
        )

    def add_phone(self, body: dict[str, Any]) -> dict[str, Any]:
        phone_id = self.phone_service.add_phone(
            device_name=str(body.get("device_name") or body.get("name") or ""),
            adb_serial=str(body.get("adb_serial") or body.get("serial") or ""),
            account_name=str(body.get("account_name") or body.get("account") or ""),
            account_type=str(body.get("account_type") or "marketing"),
            connection_mode=str(body.get("connection_mode") or "usb"),
            app_package=str(body.get("app_package") or ""),
            remote_video_dir=str(body.get("remote_video_dir") or "/sdcard/DCIM/Camera"),
            note=str(body.get("note") or ""),
        )
        return {"phone_id": phone_id, "phone": self.get_phone(phone_id)}

    def get_phone(self, phone_id: str) -> dict[str, Any]:
        phone = self.phone_repo.get(phone_id)
        if not phone:
            raise KeyError(f"Phone not found: {phone_id}")
        return phone

    def edit_phone(self, phone_id: str, body: dict[str, Any]) -> dict[str, Any]:
        self.phone_repo.update_phone(
            phone_id,
            device_name=optional_body_value(body, "device_name", "name"),
            account_name=optional_body_value(body, "account_name", "account"),
            account_type=optional_body_value(body, "account_type"),
            app_package=optional_body_value(body, "app_package"),
            remote_video_dir=optional_body_value(body, "remote_video_dir"),
            note=optional_body_value(body, "note"),
        )
        return self.get_phone(phone_id)

    def update_phone_status(self, phone_id: str, body: dict[str, Any]) -> dict[str, Any]:
        status = str(body.get("status") or "")
        self.phone_repo.update_status(
            phone_id,
            status,
            authorization_status=str(body["authorization_status"]) if body.get("authorization_status") else None,
            pairing_status=str(body["pairing_status"]) if body.get("pairing_status") else None,
        )
        return {"phone_id": phone_id, "status": status}

    def remove_phone(self, phone_id: str) -> dict[str, Any]:
        self.phone_repo.remove_phone(phone_id)
        return {"phone_id": phone_id, "status": "removed"}

    def adb_status(self) -> dict[str, Any]:
        return to_jsonable(self.phone_service.inspect_adb())

    def adb_devices(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        return self.phone_service.refresh_devices(
            register=parse_bool(query.get("register"), default=False),
            account_name=str(query.get("account") or query.get("account_name") or ""),
            account_type=str(query.get("account_type") or ""),
            app_package=str(query.get("app_package") or ""),
            remote_video_dir=str(query.get("remote_video_dir") or "/sdcard/DCIM/Camera"),
        )

    def pair_wireless(self, body: dict[str, Any]) -> dict[str, Any]:
        host = require_text(body, "host", "手机 IP")
        pair_port = require_port(body, "pair_port", "配对端口")
        pair_code = require_text(body, "pair_code", "配对码")
        connect_port = optional_port(body, "connect_port", "连接端口")
        return self.phone_service.pair_wireless(
            host=host,
            pair_port=pair_port,
            pair_code=pair_code,
            connect_port=connect_port,
            device_name=str(body.get("device_name") or body.get("name") or ""),
            account_name=str(body.get("account_name") or body.get("account") or ""),
            account_type=str(body.get("account_type") or "marketing"),
            app_package=str(body.get("app_package") or ""),
            remote_video_dir=str(body.get("remote_video_dir") or "/sdcard/DCIM/Camera"),
        )

    def wireless_pair_qr_start(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.phone_service.create_wireless_pairing_qr()

    def wireless_pair_qr_complete(self, body: dict[str, Any]) -> dict[str, Any]:
        service_name = require_text(body, "service_name", "二维码服务名")
        password = require_text(body, "password", "二维码密码")
        return self.phone_service.pair_wireless_qr(
            service_name=service_name,
            password=password,
            device_name=str(body.get("device_name") or body.get("name") or ""),
            account_name=str(body.get("account_name") or body.get("account") or ""),
            account_type=str(body.get("account_type") or "marketing"),
            app_package=str(body.get("app_package") or ""),
            remote_video_dir=str(body.get("remote_video_dir") or "/sdcard/DCIM/Camera"),
            timeout_seconds=parse_int(body.get("timeout_seconds"), default=60, minimum=5, maximum=180),
        )

    def connect_wireless(self, body: dict[str, Any]) -> dict[str, Any]:
        host = require_text(body, "host", "手机 IP")
        connect_port = require_port(body, "connect_port", "连接端口")
        return self.phone_service.connect_wireless(
            host=host,
            connect_port=connect_port,
        )

    def phone_health(self, phone_id: str) -> dict[str, Any]:
        phone, result, check_id = self.phone_service.health_check(phone_id)
        return {"phone": phone, "result": to_jsonable(result), "check_id": check_id}

    def phone_health_all(self) -> list[dict[str, Any]]:
        return to_jsonable(self.phone_service.health_check_all())

    def phone_checks(self, phone_id: str, query: dict[str, Any]) -> list[dict[str, Any]]:
        return self.phone_repo.list_health_checks(
            phone_id,
            limit=parse_int(query.get("limit"), default=20, minimum=1, maximum=200),
        )

    def refresh_registered_usb_devices(self) -> list[dict[str, Any]]:
        result = to_jsonable(self.phone_service.refresh_registered_usb_devices())
        with self._usb_monitor_lock:
            self._usb_monitor_last_result = result
            self._usb_monitor_last_error = ""
            self._usb_monitor_last_checked_at = datetime.now().isoformat(timespec="seconds")
        return result

    def start_usb_monitor(self, *, interval_seconds: int = USB_MONITOR_INTERVAL_SECONDS) -> None:
        with self._usb_monitor_lock:
            if self._usb_monitor_thread is not None and self._usb_monitor_thread.is_alive():
                return
            self._usb_monitor_stop.clear()
            thread = threading.Thread(
                target=self._usb_monitor_loop,
                args=(max(30, interval_seconds),),
                name="control-center-usb-monitor",
                daemon=True,
            )
            self._usb_monitor_thread = thread
            thread.start()

    def usb_monitor_status(self) -> dict[str, Any]:
        with self._usb_monitor_lock:
            thread_alive = self._usb_monitor_thread is not None and self._usb_monitor_thread.is_alive()
            return {
                "status": "running" if thread_alive else "stopped",
                "interval_seconds": USB_MONITOR_INTERVAL_SECONDS,
                "last_checked_at": self._usb_monitor_last_checked_at,
                "last_error": self._usb_monitor_last_error,
                "last_result": self._usb_monitor_last_result,
            }

    def _usb_monitor_loop(self, interval_seconds: int) -> None:
        while not self._usb_monitor_stop.is_set():
            try:
                self.refresh_registered_usb_devices()
            except Exception as exc:  # noqa: BLE001
                with self._usb_monitor_lock:
                    self._usb_monitor_last_error = str(exc)
                    self._usb_monitor_last_checked_at = datetime.now().isoformat(timespec="seconds")
            self._usb_monitor_stop.wait(interval_seconds)

    def list_tasks(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        return self.task_repo.list_tasks(
            status=str(query.get("status") or ""),
            phone_id=str(query.get("phone_id") or ""),
            video_id=str(query.get("video_id") or ""),
            date_from=str(query.get("date_from") or ""),
            date_to=str(query.get("date_to") or ""),
            search=str(query.get("search") or ""),
            limit=parse_int(query.get("limit"), default=50, minimum=1, maximum=1000),
            include_removed=parse_bool(query.get("include_removed"), default=False) or str(query.get("status") or "") == "removed",
        )

    def create_task(self, body: dict[str, Any]) -> dict[str, Any]:
        product_fields = self.task_product_fields(body)
        result = self.task_service.create_task(
            video_id=str(body.get("video_id") or ""),
            caption_id=str(body.get("caption_id") or ""),
            phone_id=str(body.get("phone_id") or ""),
            scheduled_at=str(body.get("scheduled_at") or ""),
            publish_mode=str(body.get("publish_mode") or "scheduled"),
            **product_fields,
            max_retries=parse_int(body.get("max_retries"), default=3, minimum=0, maximum=20),
            note=str(body.get("note") or ""),
            allow_reuse=parse_bool(body.get("allow_reuse"), default=False),
            timezone=str(body.get("timezone") or "Asia/Shanghai"),
        )
        return to_jsonable(result)

    def create_task_from_binding(self, body: dict[str, Any]) -> dict[str, Any]:
        product_fields = self.task_product_fields(body)
        result = self.task_service.create_from_binding(
            binding_id=str(body.get("binding_id") or ""),
            phone_id=str(body.get("phone_id") or ""),
            scheduled_at=str(body.get("scheduled_at") or ""),
            publish_mode=str(body.get("publish_mode") or "scheduled"),
            **product_fields,
            max_retries=parse_int(body.get("max_retries"), default=3, minimum=0, maximum=20),
            note=str(body.get("note") or ""),
            allow_reuse=parse_bool(body.get("allow_reuse"), default=False),
            timezone=str(body.get("timezone") or "Asia/Shanghai"),
        )
        return to_jsonable(result)

    def task_product_fields(self, body: dict[str, Any]) -> dict[str, str]:
        product_id = str(body.get("product_id") or "").strip()
        product_link = str(body.get("product_link") or "").strip()
        product_name = str(body.get("product_name") or "").strip()
        product_search_title = str(body.get("product_search_title") or body.get("search_title") or "").strip()
        product_publish_name = str(body.get("product_publish_name") or body.get("publish_name") or "").strip()

        if product_id:
            product = self.get_product(product_id)
            if str(product.get("status") or "") != "active":
                raise ValueError(f"Product is not active: {product_id}")
            product_link = product_link or str(product.get("product_link") or "")
            product_search_title = product_search_title or str(product.get("search_title") or "")
            product_publish_name = product_publish_name or str(product.get("publish_name") or "")

        if not product_search_title:
            product_search_title = product_name
        if not product_name:
            product_name = product_search_title
        if not product_publish_name:
            product_publish_name = product_search_title or product_name

        return {
            "product_id": product_id,
            "product_link": product_link,
            "product_name": product_name,
            "product_search_title": product_search_title,
            "product_publish_name": product_publish_name,
        }

    def import_tasks_csv(self, body: dict[str, Any]) -> dict[str, Any]:
        path = self.import_path_from_body(body, allowed_suffixes=TASK_IMPORT_EXTENSIONS)
        results = self.task_service.import_csv(
            path,
            allow_reuse=parse_bool(body.get("allow_reuse"), default=False),
            timezone=str(body.get("timezone") or "Asia/Shanghai"),
        )
        created = sum(1 for result in results if not result.duplicate)
        duplicates = sum(1 for result in results if result.duplicate)
        return {
            "created": created,
            "duplicates": duplicates,
            "total": len(results),
            "results": to_jsonable(results),
            "source_path": str(path),
        }

    def get_task(self, task_id: str) -> dict[str, Any]:
        task = self.task_repo.get(task_id)
        if not task:
            raise KeyError(f"Task not found: {task_id}")
        return task

    def reschedule_task(self, task_id: str, body: dict[str, Any]) -> dict[str, Any]:
        self.task_repo.reschedule(
            task_id,
            str(body.get("scheduled_at") or ""),
            timezone=str(body.get("timezone") or "Asia/Shanghai"),
        )
        return {"task_id": task_id, "status": "rescheduled"}

    def cancel_task(self, task_id: str, body: dict[str, Any]) -> dict[str, Any]:
        self.task_repo.update_status(task_id, "cancelled", failure_reason=str(body.get("reason") or ""))
        return {"task_id": task_id, "status": "cancelled"}

    def requeue_task(self, task_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return self.task_repo.requeue(
            task_id,
            reset_retry_count=parse_bool(body.get("reset_retry_count"), default=False),
            force_running=parse_bool(body.get("force_running"), default=False),
            reason=str(body.get("reason") or ""),
        )

    def update_task_status(self, task_id: str, body: dict[str, Any]) -> dict[str, Any]:
        status = str(body.get("status") or "")
        self.task_repo.update_status(
            task_id,
            status,
            failure_reason=str(body.get("failure_reason") or ""),
            run_dir=str(body.get("run_dir") or ""),
        )
        return {"task_id": task_id, "status": status}

    def assign_task_phone(self, task_id: str, body: dict[str, Any]) -> dict[str, Any]:
        phone_id = str(body.get("phone_id") or "")
        self.task_repo.assign_phone(task_id, phone_id)
        return {"task_id": task_id, "phone_id": phone_id, "status": "assigned"}

    def remove_published_tasks(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.task_repo.remove_published_tasks(parse_id_values(body, "task_ids", "ids"))

    def queue_preview(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        candidates = self.queue_service.preview(
            task_id=str(query.get("task_id") or ""),
            phone_id=str(query.get("phone_id") or ""),
            adb_serial=str(query.get("adb_serial") or ""),
            account_type=str(query.get("account_type") or ""),
            timezone=str(query.get("timezone") or "Asia/Shanghai"),
            preparation_window_minutes=parse_int(query.get("preparation_window_minutes"), default=60, minimum=0, maximum=1440),
            require_phone_ready=not parse_bool(query.get("ignore_phone_status"), default=False),
            allow_overdue=parse_bool(query.get("allow_overdue"), default=False),
            limit=parse_int(query.get("limit"), default=20, minimum=1, maximum=200),
        )
        return [to_jsonable(candidate) for candidate in candidates]

    def queue_run_once(self, body: dict[str, Any]) -> dict[str, Any]:
        self.require_publish_confirmation(body, context="queue")
        result = self.queue_service.run_once(
            task_id=str(body.get("task_id") or ""),
            phone_id=str(body.get("phone_id") or ""),
            adb_serial=str(body.get("adb_serial") or ""),
            account_type=str(body.get("account_type") or ""),
            dry_run=parse_bool(body.get("dry_run"), default=False),
            pipeline_dry_run=parse_bool(body.get("pipeline_dry_run"), default=False),
            allow_publish=parse_bool(body.get("allow_publish"), default=False),
            stop_before_final_publish=parse_bool(body.get("stop_before_final_publish"), default=False),
            timezone=str(body.get("timezone") or "Asia/Shanghai"),
            preparation_window_minutes=parse_int(body.get("preparation_window_minutes"), default=60, minimum=0, maximum=1440),
            require_phone_ready=not parse_bool(body.get("ignore_phone_status"), default=False),
            allow_overdue=parse_bool(body.get("allow_overdue"), default=False),
            timeout_seconds=parse_int(body.get("timeout_seconds"), default=7200, minimum=30, maximum=86400),
        )
        return to_jsonable(result)

    def queue_claim_next(self, body: dict[str, Any]) -> dict[str, Any]:
        claim = self.queue_service.claim_next(
            task_id=str(body.get("task_id") or ""),
            phone_id=str(body.get("phone_id") or ""),
            adb_serial=str(body.get("adb_serial") or ""),
            account_type=str(body.get("account_type") or ""),
            timezone=str(body.get("timezone") or "Asia/Shanghai"),
            preparation_window_minutes=parse_int(body.get("preparation_window_minutes"), default=60, minimum=0, maximum=1440),
            require_phone_ready=not parse_bool(body.get("ignore_phone_status"), default=False),
            allow_overdue=parse_bool(body.get("allow_overdue"), default=False),
        )
        return {"status": "no_task"} if claim is None else {"status": "claimed", "claim": to_jsonable(claim)}

    def queue_workers_start(self, body: dict[str, Any]) -> dict[str, Any]:
        dry_run = parse_bool(body.get("dry_run"), default=False)
        pipeline_dry_run = parse_bool(body.get("pipeline_dry_run"), default=False)
        allow_publish = parse_bool(body.get("allow_publish"), default=False)
        stop_before_final_publish = parse_bool(body.get("stop_before_final_publish"), default=False)
        if allow_publish and stop_before_final_publish:
            raise RuntimeError("Choose either allow_publish=true or stop_before_final_publish=true, not both.")
        if not dry_run and not pipeline_dry_run and not allow_publish and not stop_before_final_publish:
            raise RuntimeError(
                "Worker execution requires allow_publish=true, stop_before_final_publish=true, dry_run=true, or pipeline_dry_run=true."
            )

        options = WorkerManagerOptions(
            phone_ids=parse_csv_values(body.get("phone_ids", body.get("phone_id", ""))),
            adb_serials=parse_csv_values(body.get("adb_serials", body.get("adb_serial", ""))),
            account_type=str(body.get("account_type") or ""),
            max_workers=parse_int(body.get("max_workers"), default=5, minimum=1, maximum=100),
            stagger_seconds=parse_int(body.get("stagger_seconds"), default=25, minimum=0, maximum=3600),
            dry_run=dry_run,
            pipeline_dry_run=pipeline_dry_run,
            allow_publish=allow_publish,
            stop_before_final_publish=stop_before_final_publish,
            timezone=str(body.get("timezone") or "Asia/Shanghai"),
            preparation_window_minutes=parse_int(body.get("preparation_window_minutes"), default=60, minimum=0, maximum=1440),
            require_phone_ready=not parse_bool(body.get("ignore_phone_status"), default=False),
            allow_overdue=parse_bool(body.get("allow_overdue"), default=False),
            timeout_seconds=parse_int(body.get("timeout_seconds"), default=7200, minimum=30, maximum=86400),
            poll_seconds=parse_int(body.get("poll_seconds"), default=5, minimum=1, maximum=3600),
            max_runs_per_worker=parse_int(body.get("max_runs_per_worker"), default=0, minimum=0, maximum=1000),
            stop_when_idle=parse_bool(body.get("stop_when_idle"), default=False),
            heartbeat_seconds=parse_int(body.get("heartbeat_seconds"), default=10, minimum=1, maximum=300),
            lease_seconds=parse_int(body.get("lease_seconds"), default=120, minimum=30, maximum=3600),
            post_run_cooldown_seconds=parse_int(body.get("post_run_cooldown_seconds"), default=90, minimum=0, maximum=3600),
            stop_on_failure=parse_bool(body.get("stop_on_failure"), default=True),
            idle_wake_enabled=parse_bool(body.get("idle_wake_enabled"), default=True),
            idle_wake_interval_seconds=parse_int(body.get("idle_wake_interval_seconds"), default=300, minimum=1, maximum=86400),
            steady_state_gate_enabled=parse_bool(body.get("steady_state_gate_enabled"), default=True),
            expected_worker_count=parse_int(body.get("expected_worker_count"), default=5, minimum=1, maximum=100),
            require_task_per_phone=parse_bool(body.get("require_task_per_phone"), default=False),
        )

        manager = WorkerManager(options, db_path=self.db_path)
        if options.steady_state_gate_enabled and options.recover_stale_before_start:
            WorkerStateRepository(self.db_path).recover_stale(limit=100, reason="worker lease expired before steady-state start")
        planned_phones = manager.select_phones()
        readiness = manager.readiness_report(planned_phones) if options.steady_state_gate_enabled else {"ok": True}
        if options.steady_state_gate_enabled and not readiness.get("ok"):
            return {
                "status": "blocked",
                "workers": 0,
                "phones": [
                    {
                        "phone_id": str(phone.get("phone_id") or ""),
                        "adb_serial": str(phone.get("adb_serial") or ""),
                        "account_type": str(phone.get("account_type") or ""),
                    }
                    for phone in planned_phones
                ],
                "options": worker_options_to_dict(options),
                "readiness": readiness,
            }
        if not planned_phones:
            return {
                "status": "no_phones",
                "workers": 0,
                "phones": [],
                "options": worker_options_to_dict(options),
            }

        with self._worker_lock:
            if self._worker_thread is not None and self._worker_thread.is_alive():
                raise RuntimeError("Queue workers are already running.")
            self._worker_manager = manager
            self._worker_outcome = None
            self._worker_error = ""
            self._worker_started_at = datetime.now().isoformat(timespec="seconds")
            self._worker_stop_requested_at = ""
            self._worker_options = worker_options_to_dict(options)
            thread = threading.Thread(target=self._run_queue_workers, name="control-center-queue-workers", daemon=True)
            self._worker_thread = thread
            thread.start()

        return {
            "status": "started",
            "workers": len(planned_phones),
            "phones": [
                {
                    "phone_id": str(phone.get("phone_id") or ""),
                    "adb_serial": str(phone.get("adb_serial") or ""),
                    "account_type": str(phone.get("account_type") or ""),
                }
                for phone in planned_phones
            ],
            "started_at": self._worker_started_at,
            "options": self._worker_options,
            "readiness": readiness,
        }

    def queue_workers_status(self, query: dict[str, Any]) -> dict[str, Any]:
        status_filter = str(query.get("status") or "")
        phone_id = str(query.get("phone_id") or "")
        limit = parse_int(query.get("limit"), default=100, minimum=1, maximum=1000)
        include_finished = parse_bool(query.get("include_finished"), default=False)
        worker_limit = limit if status_filter or include_finished else 1000
        worker_repo = WorkerStateRepository(self.db_path)
        if not status_filter and not include_finished:
            worker_repo.recover_stale(limit=100, reason="worker lease expired during status refresh")
        workers = worker_repo.list_workers(status=status_filter, phone_id=phone_id, limit=worker_limit)
        if not status_filter and not include_finished:
            now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
            active_statuses = {"starting", "idle", "running"}
            workers = [
                worker
                for worker in workers
                if str(worker.get("status") or "") in active_statuses
                and str(worker.get("lease_expires_at") or "") >= now
            ][:limit]
        with self._worker_lock:
            thread_alive = self._worker_thread is not None and self._worker_thread.is_alive()
            outcome = self._worker_outcome
            error = self._worker_error
            started_at = self._worker_started_at
            stop_requested_at = self._worker_stop_requested_at
            options = dict(self._worker_options)
        if thread_alive:
            manager_status = "running"
        elif not status_filter and not include_finished and not workers:
            manager_status = "idle"
        elif outcome is not None:
            manager_status = str(outcome.get("status") or "completed")
        elif error:
            manager_status = "error"
        else:
            manager_status = "idle"
        return {
            "status": manager_status,
            "thread_alive": thread_alive,
            "started_at": started_at,
            "stop_requested_at": stop_requested_at,
            "options": options,
            "outcome": to_jsonable(outcome or {}),
            "error": error,
            "workers": workers,
        }

    def queue_workers_stop(self, body: dict[str, Any]) -> dict[str, Any]:
        wait_seconds = parse_int(body.get("wait_seconds"), default=5, minimum=0, maximum=60)
        with self._worker_lock:
            manager = self._worker_manager
            thread = self._worker_thread
            active = thread is not None and thread.is_alive()
            if active:
                self._worker_stop_requested_at = datetime.now().isoformat(timespec="seconds")
                if manager is not None:
                    manager.stop()
        if active and thread is not None and wait_seconds > 0:
            thread.join(timeout=wait_seconds)
        return self.queue_workers_status({"limit": body.get("limit", 100)})

    def queue_workers_recover_stale(self, body: dict[str, Any]) -> dict[str, Any]:
        recovered = WorkerStateRepository(self.db_path).recover_stale(
            limit=parse_int(body.get("limit"), default=50, minimum=1, maximum=500),
            reason=str(body.get("reason") or "worker stopped from control center"),
        )
        return {"recovered": recovered, "count": len(recovered)}

    def _run_queue_workers(self) -> None:
        manager = self._worker_manager
        if manager is None:
            return
        try:
            outcome = manager.run_forever()
            with self._worker_lock:
                self._worker_outcome = to_jsonable(outcome)
                self._worker_error = ""
        except Exception as exc:  # noqa: BLE001
            with self._worker_lock:
                self._worker_error = str(exc)
                self._worker_outcome = {"status": "error", "error": str(exc)}

    def list_logs(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        return self.log_service.list_logs(
            result=str(query.get("result") or ""),
            task_id=str(query.get("task_id") or ""),
            phone_id=str(query.get("phone_id") or ""),
            search=str(query.get("search") or ""),
            failures_only=parse_bool(query.get("failures_only"), default=False),
            limit=parse_int(query.get("limit"), default=50, minimum=1, maximum=1000),
        )

    def list_failures(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        query = dict(query)
        query["failures_only"] = True
        return self.list_logs(query)

    def remove_logs(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.log_service.remove_logs(parse_id_values(body, "log_ids", "ids"))

    def get_log(self, log_id: str, query: dict[str, Any] | None = None) -> dict[str, Any]:
        query = query or {}
        log, artifacts = self.log_service.get_log_with_artifacts(
            log_id,
            tail_chars=parse_int(query.get("tail_chars"), default=2000, minimum=0, maximum=20000),
        )
        return {"log": log, "artifacts": to_jsonable(artifacts)}

    def latest_log(self, query: dict[str, Any]) -> dict[str, Any]:
        result = self.log_service.latest_log_with_artifacts(
            tail_chars=parse_int(query.get("tail_chars"), default=2000, minimum=0, maximum=20000),
        )
        if result is None:
            return {}
        log, artifacts = result
        return {"log": log, "artifacts": to_jsonable(artifacts)}

    def task_report(self, task_id: str) -> dict[str, Any]:
        return to_jsonable(self.log_service.task_report(task_id))

    def log_attempts(self, log_id: str, query: dict[str, Any]) -> list[dict[str, Any]]:
        log = self.log_service.repository.get_log(log_id)
        if not log:
            raise KeyError(f"Log not found: {log_id}")
        return self.log_service.repository.attempts_for_task(
            str(log.get("task_id") or ""),
            limit=parse_int(query.get("limit"), default=100, minimum=1, maximum=1000),
        )

    def log_artifacts(self, log_id: str, query: dict[str, Any]) -> dict[str, Any]:
        log, artifacts = self.log_service.get_log_with_artifacts(
            log_id,
            tail_chars=parse_int(query.get("tail_chars"), default=2000, minimum=0, maximum=20000),
        )
        data = to_jsonable(artifacts)
        data["files"] = artifact_file_listing(artifacts)
        return {"log": log, "artifacts": data}

    def log_tail(self, log_id: str, query: dict[str, Any]) -> dict[str, Any]:
        _, artifacts = self.log_service.get_log_with_artifacts(
            log_id,
            tail_chars=parse_int(query.get("chars"), default=4000, minimum=0, maximum=50000),
        )
        stream = str(query.get("stream") or "both")
        return {
            "stream": stream,
            "stdout": artifacts.stdout_tail if stream in {"stdout", "both"} else "",
            "stderr": artifacts.stderr_tail if stream in {"stderr", "both"} else "",
        }

    def log_artifact(self, log_id: str, query: dict[str, Any]) -> dict[str, Any]:
        _, artifacts = self.log_service.get_log_with_artifacts(log_id, tail_chars=0)
        path = resolve_artifact_path(artifacts, kind=str(query.get("kind") or ""), name=str(query.get("name") or ""))
        if path.suffix.lower() == ".png":
            return {
                "kind": "image",
                "name": path.name,
                "path": str(path),
                "mime_type": "image/png",
                "base64": base64.b64encode(path.read_bytes()).decode("ascii"),
            }
        return {"kind": "text", "name": path.name, "path": str(path), "content": path.read_text(encoding="utf-8", errors="replace")}

    def get_ocr_settings(self) -> dict[str, Any]:
        config = read_json_file(UPLOAD_CONFIG_PATH)
        recognition = config.get("recognition", {}) if isinstance(config.get("recognition"), dict) else {}
        ai = config.get("ai", {}) if isinstance(config.get("ai"), dict) else {}
        provider = normalize_ocr_provider(str(recognition.get("provider") or "paddle_ocr"))
        key_value = ocr_api_key_from_config(provider, recognition, ai)
        if provider == "google_ocr":
            env_name = str(recognition.get("google_api_key_env") or "GOOGLE_CLOUD_VISION_API_KEY")
        elif provider == "openai":
            env_name = str(ai.get("api_key_env") or "OPENAI_API_KEY")
        else:
            env_name = ""
        return {
            "provider": provider,
            "provider_label": ocr_provider_label(provider),
            "api_key_saved": bool(key_value),
            "api_key_masked": mask_secret(key_value),
            "api_key_env": env_name,
        }

    def update_ocr_settings(self, body: dict[str, Any]) -> dict[str, Any]:
        provider = normalize_ocr_provider(str(body.get("provider") or "paddle_ocr"))
        api_key = str(body.get("api_key") or "").strip()
        clear_api_key = parse_bool(body.get("clear_api_key"), default=False)
        config = read_json_file(UPLOAD_CONFIG_PATH)
        recognition = config.setdefault("recognition", {})
        if not isinstance(recognition, dict):
            recognition = {}
            config["recognition"] = recognition
        ai = config.setdefault("ai", {})
        if not isinstance(ai, dict):
            ai = {}
            config["ai"] = ai

        recognition["provider"] = provider
        if provider == "google_ocr":
            recognition.setdefault("google_api_key_env", "GOOGLE_CLOUD_VISION_API_KEY")
            recognition.setdefault("api_base", "https://vision.googleapis.com/v1")
            if clear_api_key:
                recognition.pop("api_key", None)
            elif api_key and not is_masked_secret(api_key):
                recognition["api_key"] = api_key
        elif provider == "openai":
            ai["provider"] = "openai"
            ai.setdefault("model", "gpt-4.1-mini")
            ai.setdefault("api_key_env", "OPENAI_API_KEY")
            ai.setdefault("api_base", "https://api.openai.com/v1")
            if clear_api_key:
                ai.pop("api_key", None)
            elif api_key and not is_masked_secret(api_key):
                ai["api_key"] = api_key
        else:
            paddle_config = recognition.setdefault("paddle", {})
            if isinstance(paddle_config, dict):
                paddle_config.setdefault("lang", "ch")
                paddle_config.setdefault("ocr_version", "PP-OCRv4")
                paddle_config.setdefault("device", "cpu")
                paddle_config.setdefault("use_gpu", False)
                paddle_config.setdefault("use_angle_cls", False)
                paddle_config.setdefault("show_log", False)
                paddle_config.setdefault("enable_mkldnn", False)
                paddle_config.setdefault("cpu_threads", 4)
                paddle_config.setdefault("use_doc_orientation_classify", False)
                paddle_config.setdefault("use_doc_unwarping", False)
                paddle_config.setdefault("use_textline_orientation", False)
                paddle_config.setdefault("text_rec_score_thresh", 0.3)

        backup = write_json_with_backup(UPLOAD_CONFIG_PATH, config)
        settings = self.get_ocr_settings()
        settings["backup_path"] = str(backup) if backup else ""
        return settings

    def upload_doctor(self, body: dict[str, Any]) -> dict[str, Any]:
        return run_upload_command(["doctor"], timeout_seconds=parse_int(body.get("timeout_seconds"), default=60, minimum=5, maximum=600))

    def upload_push(self, body: dict[str, Any]) -> dict[str, Any]:
        video = str(body.get("video") or body.get("path") or "")
        if not video:
            raise ValueError("Video path is required.")
        return run_upload_command(["push", video], timeout_seconds=parse_int(body.get("timeout_seconds"), default=600, minimum=30, maximum=7200))

    def upload_run(self, body: dict[str, Any]) -> dict[str, Any]:
        self.require_publish_confirmation(body, context="upload")
        video = str(body.get("video") or body.get("path") or "")
        if not video:
            raise ValueError("Video path is required.")
        args = ["run", video]
        if parse_bool(body.get("allow_publish"), default=False):
            args.append("--allow-publish")
        if "dry_run" in body:
            args.append("--dry-run" if parse_bool(body.get("dry_run"), default=False) else "--no-dry-run")
        return run_upload_command(args, timeout_seconds=parse_int(body.get("timeout_seconds"), default=7200, minimum=30, maximum=86400))

    def get_upload_config(self) -> dict[str, Any]:
        return {"path": str(UPLOAD_CONFIG_PATH), "config": read_json_file(UPLOAD_CONFIG_PATH)}

    def update_upload_config(self, body: dict[str, Any]) -> dict[str, Any]:
        config = body.get("config", body.get("value"))
        if not isinstance(config, dict):
            raise ValueError("config must be a JSON object.")
        backup = write_json_with_backup(UPLOAD_CONFIG_PATH, config)
        return {"path": str(UPLOAD_CONFIG_PATH), "backup_path": str(backup) if backup else "", "config": config}

    def get_caption_presets(self) -> dict[str, Any]:
        return {"path": str(CAPTION_PRESETS_PATH), "presets": read_json_file(CAPTION_PRESETS_PATH)}

    def update_caption_presets(self, body: dict[str, Any]) -> dict[str, Any]:
        presets = body.get("presets", body.get("value"))
        if not isinstance(presets, dict):
            raise ValueError("presets must be a JSON object.")
        backup = write_json_with_backup(CAPTION_PRESETS_PATH, presets)
        return {"path": str(CAPTION_PRESETS_PATH), "backup_path": str(backup) if backup else "", "presets": presets}

    def import_path_from_body(self, body: dict[str, Any], *, allowed_suffixes: set[str]) -> Path:
        file_part = body.get("file")
        if isinstance(file_part, dict):
            return save_uploaded_import_file(file_part, allowed_suffixes=allowed_suffixes)
        path_text = str(body.get("path") or "").strip()
        if not path_text:
            raise ValueError("A file upload or server-side path is required.")
        return Path(path_text).expanduser().resolve()

    def require_publish_confirmation(self, body: dict[str, Any], *, context: str) -> None:
        if not parse_bool(body.get("allow_publish"), default=False):
            return
        if context == "queue" and not str(body.get("task_id") or "").strip():
            raise RuntimeError("Real queue publishing requires a specific task_id.")


def save_uploaded_video(filename: str, content: bytes) -> Path:
    safe_name = sanitize_filename(filename)
    suffix = Path(safe_name).suffix.casefold()
    if suffix not in SUPPORTED_VIDEO_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_VIDEO_EXTENSIONS))
        raise ValueError(f"Unsupported video extension: {suffix or '(none)'}. Supported: {supported}")
    UPLOAD_VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    stem = Path(safe_name).stem[:80] or "video"
    target = UPLOAD_VIDEO_DIR / f"{stem}-{uuid4().hex[:12]}{suffix}"
    target.write_bytes(content)
    return target.resolve()


def save_uploaded_import_file(file_part: dict[str, Any], *, allowed_suffixes: set[str]) -> Path:
    content = file_part.get("content")
    if not isinstance(content, bytes) or not content:
        raise ValueError("Uploaded file is empty.")
    safe_name = sanitize_filename(str(file_part.get("filename") or "import.txt"))
    suffix = Path(safe_name).suffix.casefold()
    if suffix not in allowed_suffixes:
        raise ValueError(f"Unsupported import file extension: {suffix or '(none)'}.")
    UPLOAD_IMPORT_DIR.mkdir(parents=True, exist_ok=True)
    target = UPLOAD_IMPORT_DIR / f"{Path(safe_name).stem}-{uuid4().hex[:12]}{suffix}"
    target.write_bytes(content)
    return target.resolve()


def sanitize_filename(filename: str) -> str:
    name = Path(filename.replace("\\", "/")).name.strip()
    if not name:
        name = "video.mp4"
    path = Path(name)
    suffix = path.suffix.casefold()
    stem = path.stem or "video"
    cleaned = "".join(char if char.isalnum() or char in {"-", "_", "."} else "_" for char in stem).strip("._-")
    return f"{cleaned or 'video'}{suffix}"


def choose_local_folder(*, title: str, initial_dir: str = "") -> str:
    if sys.platform.startswith("win"):
        folder = choose_local_folder_windows(title=title, initial_dir=initial_dir)
        if folder is not None:
            return folder
    return choose_local_folder_tkinter(title=title, initial_dir=initial_dir)


def choose_local_folder_windows(*, title: str, initial_dir: str = "") -> str | None:
    selected_path = powershell_literal(initial_dir) if initial_dir and Path(initial_dir).exists() else "''"
    script = "\n".join(
        [
            "Add-Type -AssemblyName System.Windows.Forms",
            "$dialog = New-Object System.Windows.Forms.FolderBrowserDialog",
            f"$dialog.Description = {powershell_literal(title)}",
            "$dialog.ShowNewFolderButton = $false",
            "$owner = New-Object System.Windows.Forms.Form",
            "$owner.TopMost = $true",
            "$owner.StartPosition = 'CenterScreen'",
            "$owner.ShowInTaskbar = $false",
            f"$selectedPath = {selected_path}",
            "if ($selectedPath -and (Test-Path -LiteralPath $selectedPath)) { $dialog.SelectedPath = $selectedPath }",
            "$result = $dialog.ShowDialog($owner)",
            "$owner.Dispose()",
            "if ($result -eq [System.Windows.Forms.DialogResult]::OK) {",
            "  [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false",
            "  Write-Output $dialog.SelectedPath",
            "}",
        ]
    )
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Sta", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
            check=False,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    return (result.stdout or "").strip()


def choose_local_folder_tkinter(*, title: str, initial_dir: str = "") -> str:
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        root.update()
        kwargs = {"title": title}
        if initial_dir and Path(initial_dir).exists():
            kwargs["initialdir"] = initial_dir
        folder = filedialog.askdirectory(**kwargs)
        root.destroy()
        return str(folder or "")
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Cannot open folder picker: {exc}") from exc


def powershell_literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def optional_body_value(body: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        if key in body:
            return str(body.get(key) or "")
    return None


def parse_csv_values(value: Any) -> tuple[str, ...]:
    if isinstance(value, (list, tuple, set)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return tuple(part.strip() for part in str(value or "").split(",") if part.strip())


def parse_id_values(body: dict[str, Any], *keys: str) -> list[str]:
    values: Any = None
    for key in keys:
        if key in body:
            values = body.get(key)
            break
    if values is None:
        values = body.get("id", body.get("ids", ""))
    if isinstance(values, (list, tuple, set)):
        raw_values = values
    else:
        raw_values = str(values or "").split(",")
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in raw_values:
        item = str(value or "").strip()
        if item and item not in seen:
            seen.add(item)
            cleaned.append(item)
    return cleaned


def normalize_ocr_provider(value: str) -> str:
    text = str(value or "").strip().casefold()
    aliases = {
        "paddle": "paddle_ocr",
        "paddleocr": "paddle_ocr",
        "paddle_vision": "paddle_ocr",
        "google": "google_ocr",
        "google_vision": "google_ocr",
        "cloud_vision": "google_ocr",
        "google_cloud_vision": "google_ocr",
        "openai_vision": "openai",
        "ai": "openai",
    }
    provider = aliases.get(text, text or "paddle_ocr")
    if provider not in {"paddle_ocr", "google_ocr", "openai"}:
        raise ValueError(f"Unsupported OCR provider: {value}")
    return provider


def ocr_provider_label(provider: str) -> str:
    labels = {
        "paddle_ocr": "PaddleOCR",
        "google_ocr": "Google Cloud Vision",
        "openai": "OpenAI",
    }
    return labels.get(provider, provider)


def ocr_api_key_from_config(provider: str, recognition: dict[str, Any], ai: dict[str, Any]) -> str:
    if provider == "google_ocr":
        return str(recognition.get("api_key") or "")
    if provider == "paddle_ocr":
        return ""
    return str(ai.get("api_key") or "")


def mask_secret(value: str) -> str:
    text = str(value or "")
    if not text:
        return ""
    if len(text) <= 8:
        return "•" * 8
    return f"{text[:4]}{'•' * 8}{text[-4:]}"


def is_masked_secret(value: str) -> bool:
    text = str(value or "").strip()
    return bool(text) and set(text) <= {"•", "*"}


def worker_options_to_dict(options: WorkerManagerOptions) -> dict[str, Any]:
    return {
        "phone_ids": list(options.phone_ids),
        "adb_serials": list(options.adb_serials),
        "account_type": options.account_type,
        "max_workers": options.max_workers,
        "stagger_seconds": options.stagger_seconds,
        "dry_run": options.dry_run,
        "pipeline_dry_run": options.pipeline_dry_run,
        "allow_publish": options.allow_publish,
        "stop_before_final_publish": options.stop_before_final_publish,
        "timezone": options.timezone,
        "preparation_window_minutes": options.preparation_window_minutes,
        "require_phone_ready": options.require_phone_ready,
        "allow_overdue": options.allow_overdue,
        "timeout_seconds": options.timeout_seconds,
        "poll_seconds": options.poll_seconds,
        "max_runs_per_worker": options.max_runs_per_worker,
        "stop_when_idle": options.stop_when_idle,
        "heartbeat_seconds": options.heartbeat_seconds,
        "lease_seconds": options.lease_seconds,
        "post_run_cooldown_seconds": options.post_run_cooldown_seconds,
        "stop_on_failure": options.stop_on_failure,
        "idle_wake_enabled": options.idle_wake_enabled,
        "idle_wake_interval_seconds": options.idle_wake_interval_seconds,
        "steady_state_gate_enabled": options.steady_state_gate_enabled,
        "expected_worker_count": options.expected_worker_count,
        "require_task_per_phone": options.require_task_per_phone,
        "recover_stale_before_start": options.recover_stale_before_start,
    }


def require_text(body: dict[str, Any], key: str, label: str) -> str:
    value = str(body.get(key) or "").strip()
    if not value:
        raise ValueError(f"{label}不能为空。")
    if key == "host" and ":" in value:
        raise ValueError(f"{label} 只填写 IP 地址，不要包含端口。端口请填到对应的端口输入框。")
    return value


def require_port(body: dict[str, Any], key: str, label: str) -> str:
    value = require_text(body, key, label)
    if not value.isdigit():
        raise ValueError(f"{label}必须是数字。")
    port = int(value)
    if port < 1 or port > 65535:
        raise ValueError(f"{label}必须在 1 到 65535 之间。")
    return value


def optional_port(body: dict[str, Any], key: str, label: str) -> str:
    value = str(body.get(key) or "").strip()
    if not value:
        return ""
    if not value.isdigit():
        raise ValueError(f"{label}必须是数字。")
    port = int(value)
    if port < 1 or port > 65535:
        raise ValueError(f"{label}必须在 1 到 65535 之间。")
    return value


def read_json_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"JSON file must contain an object: {path}")
    return data


def write_json_with_backup(path: Path, data: dict[str, Any]) -> Path | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    backup: Path | None = None
    if path.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = path.with_name(f"{path.name}.bak-{stamp}")
        shutil.copy2(path, backup)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return backup


def run_upload_command(args: list[str], *, timeout_seconds: int) -> dict[str, Any]:
    command = [sys.executable, "-m", "upload_system", *args]
    try:
        process = subprocess.run(
            command,
            cwd=str(UPLOAD_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "status": "failed",
            "returncode": -1,
            "command": command,
            "cwd": str(UPLOAD_ROOT),
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or f"Timed out after {timeout_seconds} seconds.",
        }
    return {
        "status": "ok" if process.returncode == 0 else "failed",
        "returncode": process.returncode,
        "command": command,
        "cwd": str(UPLOAD_ROOT),
        "stdout": process.stdout,
        "stderr": process.stderr,
    }


def artifact_file_listing(artifacts: Any) -> dict[str, list[str]]:
    return {
        "trace": list_artifact_names(artifacts.trace_dir, "*.json"),
        "screenshot": list_artifact_names(artifacts.screenshot_dir, "*.png"),
        "xml": list_artifact_names(artifacts.xml_dir, "*.xml"),
        "stdout": [artifacts.stdout_path.name] if artifacts.stdout_path is not None and artifacts.stdout_path.exists() else [],
        "stderr": [artifacts.stderr_path.name] if artifacts.stderr_path is not None and artifacts.stderr_path.exists() else [],
        "state": [artifacts.state_path.name] if artifacts.state_path is not None and artifacts.state_path.exists() else [],
        "manifest": [artifacts.manifest_path.name] if artifacts.manifest_path is not None and artifacts.manifest_path.exists() else [],
        "command": [artifacts.command_path.name] if artifacts.command_path is not None and artifacts.command_path.exists() else [],
    }


def list_artifact_names(path: Path | None, pattern: str) -> list[str]:
    if not path or not path.exists() or not path.is_dir():
        return []
    return [item.name for item in sorted(path.glob(pattern))]


def resolve_artifact_path(artifacts: Any, *, kind: str, name: str) -> Path:
    direct = {
        "state": artifacts.state_path,
        "manifest": artifacts.manifest_path,
        "command": artifacts.command_path,
        "stdout": artifacts.stdout_path,
        "stderr": artifacts.stderr_path,
    }
    if kind in direct:
        path = direct[kind]
        if path and path.exists():
            return path
        raise KeyError(f"Artifact not found: {kind}")
    directories = {
        "trace": artifacts.trace_dir,
        "traces": artifacts.trace_dir,
        "screenshot": artifacts.screenshot_dir,
        "screenshots": artifacts.screenshot_dir,
        "xml": artifacts.xml_dir,
    }
    directory = directories.get(kind)
    if not directory or not directory.exists() or not name:
        raise KeyError(f"Artifact not found: {kind}/{name}")
    path = (directory / Path(name).name).resolve()
    directory_resolved = directory.resolve()
    try:
        path.relative_to(directory_resolved)
    except ValueError as exc:
        raise ValueError("Artifact path is outside of the run directory.") from exc
    if not path.exists() or not path.is_file():
        raise KeyError(f"Artifact not found: {kind}/{name}")
    return path


def table_count(connection: Any, table: str, where_sql: str = "") -> int:
    sql = f"SELECT COUNT(*) AS count FROM {table}"
    if where_sql:
        sql += " WHERE " + where_sql
    row = connection.execute(sql).fetchone()
    return int(row["count"] if row else 0)
