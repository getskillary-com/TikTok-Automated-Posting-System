from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from copywriting_library.caption_repository import CaptionRepository
from release_task_list.models import PUBLISH_MODES
from release_task_list.task_repository import ReleaseTaskRepository
from shared.account_rules import require_account_workflow
from shared.database import init_database, session, utc_now
from video_library_system.video_repository import VideoRepository
from video_library_system.video_scanner import SUPPORTED_VIDEO_EXTENSIONS, hash_file, read_video_metadata

from .excel_io import read_task_rows, write_phones, write_status, write_task_template


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SHARE_ROOT = PROJECT_ROOT / "shared_drive"
DEFAULT_TIMEZONE = "America/Sao_Paulo"
TERMINAL_BATCH_STATUSES = {"accepted", "rejected", "duplicate_batch"}


@dataclass(frozen=True)
class ScanResult:
    scanned: int = 0
    accepted_batches: int = 0
    rejected_batches: int = 0
    skipped: int = 0


@dataclass(frozen=True)
class BatchResult:
    batch_id: str
    status: str
    total_rows: int
    accepted_count: int
    rejected_count: int
    duplicate_count: int
    current_path: str


class SharedDriveImporter:
    def __init__(
        self,
        *,
        share_root: str | Path = DEFAULT_SHARE_ROOT,
        db_path: str | Path | None = None,
        timezone: str = DEFAULT_TIMEZONE,
    ) -> None:
        self.share_root = Path(share_root).expanduser().resolve()
        self.db_path = db_path
        self.timezone = timezone
        init_database(db_path)
        self.video_repository = VideoRepository(db_path)
        self.caption_repository = CaptionRepository(db_path)
        self.task_repository = ReleaseTaskRepository(db_path)

    def ensure_layout(self) -> None:
        for relative in (
            "templates",
            "inbox",
            "processing",
            "accepted",
            "rejected",
            "status",
            "archive",
            "_system/logs",
        ):
            (self.share_root / relative).mkdir(parents=True, exist_ok=True)

    def create_templates(self) -> dict[str, str]:
        self.ensure_layout()
        task_template = self.share_root / "templates" / "task_template.xlsx"
        phones_template = self.share_root / "templates" / "phones.xlsx"
        write_task_template(task_template)
        write_phones(phones_template, self.list_phones())
        return {"task_template": str(task_template), "phones": str(phones_template)}

    def doctor(self) -> dict[str, Any]:
        self.ensure_layout()
        return {
            "share_root": str(self.share_root),
            "layout_ok": all((self.share_root / name).exists() for name in ("templates", "inbox", "status")),
            "openpyxl_ok": True,
            "phone_count": len(self.list_phones()),
        }

    def scan_once(self, *, export_status: bool = False) -> ScanResult:
        self.ensure_layout()
        scanned = accepted = rejected = skipped = 0
        for batch_dir in sorted((self.share_root / "inbox").iterdir(), key=lambda path: path.name.casefold()):
            if not batch_dir.is_dir():
                continue
            if not (batch_dir / ".ready").exists():
                skipped += 1
                continue
            scanned += 1
            result = self.process_inbox_batch(batch_dir)
            if result.status == "accepted":
                accepted += 1
            else:
                rejected += 1
            if export_status:
                self.export_status(batch_id=result.batch_id)
        if export_status and scanned == 0:
            self.export_status()
        return ScanResult(scanned=scanned, accepted_batches=accepted, rejected_batches=rejected, skipped=skipped)

    def process_inbox_batch(self, batch_dir: Path) -> BatchResult:
        batch_id = safe_batch_id(batch_dir.name)
        lock_path = batch_dir / ".importing.lock"
        try:
            lock_file = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(lock_file, "w", encoding="utf-8") as file:
                file.write(utc_now())
        except FileExistsError:
            return BatchResult(batch_id=batch_id, status="skipped_locked", total_rows=0, accepted_count=0, rejected_count=0, duplicate_count=0, current_path=str(batch_dir))

        if self.batch_is_terminal(batch_id):
            destination = unique_destination(self.share_root / "rejected" / f"{batch_id}_duplicate_batch")
            shutil.move(str(batch_dir), str(destination))
            self.upsert_batch(batch_id, original_path=str(batch_dir), current_path=str(destination), status="duplicate_batch", error_message="batch_id was already imported")
            return BatchResult(batch_id=batch_id, status="duplicate_batch", total_rows=0, accepted_count=0, rejected_count=0, duplicate_count=0, current_path=str(destination))

        processing_dir = unique_destination(self.share_root / "processing" / batch_id)
        shutil.move(str(batch_dir), str(processing_dir))
        self.upsert_batch(batch_id, original_path=str(batch_dir), current_path=str(processing_dir), status="processing")
        try:
            result = self.import_processing_batch(batch_id, processing_dir)
            final_root = self.share_root / ("accepted" if result.accepted_count > 0 else "rejected")
            final_dir = unique_destination(final_root / batch_id)
            remove_lock(processing_dir)
            shutil.move(str(processing_dir), str(final_dir))
            final_status = "accepted" if result.accepted_count > 0 else "rejected"
            if result.accepted_count > 0:
                self.refresh_accepted_video_paths(batch_id, final_dir)
            self.finish_batch(
                batch_id,
                current_path=str(final_dir),
                status=final_status,
                total_rows=result.total_rows,
                accepted_count=result.accepted_count,
                rejected_count=result.rejected_count,
                duplicate_count=result.duplicate_count,
                error_message="",
            )
            return BatchResult(
                batch_id=batch_id,
                status=final_status,
                total_rows=result.total_rows,
                accepted_count=result.accepted_count,
                rejected_count=result.rejected_count,
                duplicate_count=result.duplicate_count,
                current_path=str(final_dir),
            )
        except Exception as exc:  # noqa: BLE001
            remove_lock(processing_dir)
            final_dir = unique_destination(self.share_root / "rejected" / batch_id)
            if processing_dir.exists():
                shutil.move(str(processing_dir), str(final_dir))
            self.finish_batch(
                batch_id,
                current_path=str(final_dir),
                status="rejected",
                total_rows=0,
                accepted_count=0,
                rejected_count=0,
                duplicate_count=0,
                error_message=str(exc),
            )
            return BatchResult(batch_id=batch_id, status="rejected", total_rows=0, accepted_count=0, rejected_count=0, duplicate_count=0, current_path=str(final_dir))

    def import_processing_batch(self, batch_id: str, batch_dir: Path) -> BatchResult:
        tasks_path = batch_dir / "tasks.xlsx"
        if not tasks_path.exists():
            raise FileNotFoundError(f"tasks.xlsx not found in batch {batch_id}")
        rows = read_task_rows(tasks_path)
        accepted_count = rejected_count = duplicate_count = 0
        seen_video_names: set[str] = set()

        for row in rows:
            item_status = "invalid"
            try:
                import_status = self.import_row(batch_id=batch_id, batch_dir=batch_dir, row=row, seen_video_names=seen_video_names)
                item_status = import_status
                if import_status == "accepted":
                    accepted_count += 1
                elif import_status == "duplicate":
                    duplicate_count += 1
                    rejected_count += 1
                else:
                    rejected_count += 1
            except Exception as exc:  # noqa: BLE001
                rejected_count += 1
                self.record_item_failure(batch_id=batch_id, row=row, status=item_status, error=str(exc))

        return BatchResult(
            batch_id=batch_id,
            status="accepted" if accepted_count else "rejected",
            total_rows=len(rows),
            accepted_count=accepted_count,
            rejected_count=rejected_count,
            duplicate_count=duplicate_count,
            current_path=str(batch_dir),
        )

    def import_row(self, *, batch_id: str, batch_dir: Path, row: dict[str, str], seen_video_names: set[str]) -> str:
        row_id = clean_required(row, "row_id")
        if self.item_exists(batch_id, row_id):
            return "duplicate"

        phone_id = clean_required(row, "phone_id")
        video_file = clean_required(row, "video_file")
        caption = clean_required(row, "caption")
        scheduled_at = clean_required(row, "scheduled_at")
        publish_mode = str(row.get("publish_mode") or "scheduled").strip() or "scheduled"
        max_retries = parse_int(row.get("max_retries"), default=3)
        note = str(row.get("note") or "").strip()

        video_path = resolve_batch_file(batch_dir, video_file)
        if not video_path.exists() or not video_path.is_file():
            raise ValueError(f"video file not found: {video_file}")
        if video_path.suffix.casefold() not in SUPPORTED_VIDEO_EXTENSIONS:
            raise ValueError(f"unsupported video extension: {video_path.suffix}")
        assert_file_stable(video_path)

        phone = self.get_phone(phone_id)
        if not phone:
            raise ValueError(f"phone_id not found: {phone_id}")
        if str(phone.get("current_status") or "") in {"removed", "disabled"}:
            raise ValueError(f"phone is not allowed for import: {phone_id} status={phone.get('current_status')}")

        product_fields = {
            "product_id": "",
            "product_link": str(row.get("product_link") or "").strip(),
            "product_name": str(row.get("product_name") or "").strip(),
            "product_search_title": str(row.get("product_search_title") or "").strip(),
            "product_publish_name": str(row.get("product_publish_name") or "").strip(),
        }
        if publish_mode not in PUBLISH_MODES:
            raise ValueError(f"Unsupported publish mode: {publish_mode}")
        if max_retries < 0:
            raise ValueError("max_retries cannot be negative")
        require_account_workflow(
            account_type=str(phone.get("account_type") or "marketing"),
            publish_mode=publish_mode,
            product_fields=product_fields,
        )

        file_name_key = normalize_video_file_name(video_path.name)
        file_hash = hash_file(video_path)
        if file_name_key in seen_video_names or self.video_file_name_exists(video_path.name):
            self.record_item(
                batch_id=batch_id,
                row_id=row_id,
                phone_id=phone_id,
                video_file=video_file,
                sha256=file_hash,
                status="duplicate",
                error="duplicate video filename is rejected",
                scheduled_at=scheduled_at,
            )
            return "duplicate"
        seen_video_names.add(file_name_key)

        metadata = read_video_metadata(video_path)
        video_result = self.video_repository.add_video(
            metadata,
            title=video_path.stem,
            batch_name=batch_id,
            note=note,
        )
        if video_result.duplicate:
            self.record_item(
                batch_id=batch_id,
                row_id=row_id,
                phone_id=phone_id,
                video_file=video_file,
                video_id=video_result.video_id,
                sha256=file_hash,
                status="duplicate",
                error="duplicate video was found during video import",
                scheduled_at=scheduled_at,
            )
            return "duplicate"

        caption_result = self.caption_repository.add_caption(caption, platform="tiktok", account_scope=str(phone.get("account_type") or ""), note=note)
        task_result = self.task_repository.create_task(
            video_id=video_result.video_id,
            caption_id=caption_result.caption_id,
            phone_id=phone_id,
            scheduled_at=scheduled_at,
            publish_mode=publish_mode,
            product_link=product_fields["product_link"],
            product_name=product_fields["product_name"],
            product_search_title=product_fields["product_search_title"],
            product_publish_name=product_fields["product_publish_name"],
            max_retries=max_retries,
            note=note,
            allow_reuse=False,
            timezone=self.timezone,
        )
        self.record_item(
            batch_id=batch_id,
            row_id=row_id,
            phone_id=phone_id,
            video_file=video_file,
            video_id=video_result.video_id,
            caption_id=caption_result.caption_id,
            task_id=task_result.task_id,
            sha256=file_hash,
            status="accepted",
            error="",
            scheduled_at=scheduled_at,
        )
        return "accepted"

    def export_status(self, *, batch_id: str = "") -> list[Path]:
        self.ensure_layout()
        exported: list[Path] = []
        for current_batch_id in self.list_batch_ids(batch_id=batch_id):
            rows = self.status_rows(current_batch_id)
            path = self.share_root / "status" / current_batch_id / "status.xlsx"
            write_status(path, rows)
            exported.append(path)
        return exported

    def list_phones(self, *, include_removed: bool = False) -> list[dict[str, Any]]:
        where_sql = "" if include_removed else "WHERE current_status != 'removed'"
        with session(self.db_path) as connection:
            rows = connection.execute(
                f"""
                SELECT phone_id, device_name, adb_serial, account_name, account_type,
                       current_status, app_package, remote_video_dir
                FROM phones
                {where_sql}
                ORDER BY phone_id
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def get_phone(self, phone_id: str) -> dict[str, Any] | None:
        with session(self.db_path) as connection:
            row = connection.execute("SELECT * FROM phones WHERE phone_id = ?", (phone_id,)).fetchone()
        return dict(row) if row else None

    def video_file_name_exists(self, file_name: str) -> bool:
        with session(self.db_path) as connection:
            row = connection.execute(
                "SELECT video_id FROM videos WHERE lower(file_name) = lower(?) AND status != 'removed' LIMIT 1",
                (file_name,),
            ).fetchone()
        return row is not None

    def batch_is_terminal(self, batch_id: str) -> bool:
        with session(self.db_path) as connection:
            row = connection.execute("SELECT status FROM shared_import_batches WHERE batch_id = ?", (batch_id,)).fetchone()
        return bool(row and str(row["status"]) in TERMINAL_BATCH_STATUSES)

    def item_exists(self, batch_id: str, row_id: str) -> bool:
        with session(self.db_path) as connection:
            row = connection.execute(
                "SELECT import_item_id FROM shared_import_items WHERE batch_id = ? AND row_id = ? LIMIT 1",
                (batch_id, row_id),
            ).fetchone()
        return row is not None

    def upsert_batch(self, batch_id: str, *, original_path: str, current_path: str, status: str, error_message: str = "") -> None:
        now = utc_now()
        with session(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO shared_import_batches(batch_id, original_path, current_path, status, error_message, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(batch_id) DO UPDATE SET
                    current_path = excluded.current_path,
                    status = excluded.status,
                    error_message = excluded.error_message,
                    updated_at = excluded.updated_at
                """,
                (batch_id, original_path, current_path, status, error_message, now, now),
            )

    def finish_batch(
        self,
        batch_id: str,
        *,
        current_path: str,
        status: str,
        total_rows: int,
        accepted_count: int,
        rejected_count: int,
        duplicate_count: int,
        error_message: str,
    ) -> None:
        now = utc_now()
        with session(self.db_path) as connection:
            connection.execute(
                """
                UPDATE shared_import_batches
                SET current_path = ?, status = ?, total_rows = ?, accepted_count = ?,
                    rejected_count = ?, duplicate_count = ?, error_message = ?,
                    processed_at = ?, updated_at = ?
                WHERE batch_id = ?
                """,
                (current_path, status, total_rows, accepted_count, rejected_count, duplicate_count, error_message, now, now, batch_id),
            )

    def record_item_failure(self, *, batch_id: str, row: dict[str, str], status: str, error: str) -> None:
        self.record_item(
            batch_id=batch_id,
            row_id=str(row.get("row_id") or "").strip() or f"missing-{uuid.uuid4().hex[:8]}",
            phone_id=str(row.get("phone_id") or "").strip(),
            video_file=str(row.get("video_file") or "").strip(),
            status=status,
            error=error,
            scheduled_at=str(row.get("scheduled_at") or "").strip(),
        )

    def record_item(
        self,
        *,
        batch_id: str,
        row_id: str,
        phone_id: str = "",
        video_file: str = "",
        video_id: str = "",
        caption_id: str = "",
        task_id: str = "",
        sha256: str = "",
        status: str,
        error: str,
        scheduled_at: str,
    ) -> None:
        now = utc_now()
        with session(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO shared_import_items(
                    import_item_id, batch_id, row_id, phone_id, video_file, video_id,
                    caption_id, task_id, sha256, status, error, scheduled_at, created_at, updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(batch_id, row_id) DO UPDATE SET
                    phone_id = excluded.phone_id,
                    video_file = excluded.video_file,
                    video_id = excluded.video_id,
                    caption_id = excluded.caption_id,
                    task_id = excluded.task_id,
                    sha256 = excluded.sha256,
                    status = excluded.status,
                    error = excluded.error,
                    scheduled_at = excluded.scheduled_at,
                    updated_at = excluded.updated_at
                """,
                (
                    "SII-" + uuid.uuid4().hex[:12].upper(),
                    batch_id,
                    row_id,
                    phone_id,
                    video_file,
                    video_id,
                    caption_id,
                    task_id,
                    sha256,
                    status,
                    error,
                    scheduled_at,
                    now,
                    now,
                ),
            )

    def list_batch_ids(self, *, batch_id: str = "") -> list[str]:
        params: list[Any] = []
        where = ""
        if batch_id:
            where = "WHERE batch_id = ?"
            params.append(batch_id)
        with session(self.db_path) as connection:
            rows = connection.execute(
                f"SELECT batch_id FROM shared_import_batches {where} ORDER BY updated_at DESC",
                params,
            ).fetchall()
        return [str(row["batch_id"]) for row in rows]

    def status_rows(self, batch_id: str) -> list[dict[str, Any]]:
        with session(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT
                    i.batch_id,
                    i.row_id,
                    i.phone_id,
                    i.video_file,
                    i.video_id,
                    i.caption_id,
                    i.task_id,
                    i.status AS import_status,
                    COALESCE(t.status, '') AS task_status,
                    COALESCE(t.scheduled_at, i.scheduled_at) AS scheduled_at,
                    COALESCE(t.run_dir, '') AS run_dir,
                    CASE
                        WHEN i.error != '' THEN i.error
                        ELSE COALESCE(t.failure_reason, '')
                    END AS failure_reason,
                    CASE
                        WHEN t.updated_at IS NOT NULL AND t.updated_at > i.updated_at THEN t.updated_at
                        ELSE i.updated_at
                    END AS updated_at
                FROM shared_import_items i
                LEFT JOIN release_tasks t ON t.task_id = i.task_id
                WHERE i.batch_id = ?
                ORDER BY i.row_id
                """,
                (batch_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def refresh_accepted_video_paths(self, batch_id: str, final_dir: Path) -> None:
        now = utc_now()
        with session(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT video_id, video_file
                FROM shared_import_items
                WHERE batch_id = ? AND status = 'accepted' AND video_id != ''
                """,
                (batch_id,),
            ).fetchall()
            for row in rows:
                final_path = resolve_batch_file(final_dir, str(row["video_file"] or ""))
                connection.execute(
                    """
                    UPDATE videos
                    SET file_path = ?, file_name = ?, updated_at = ?
                    WHERE video_id = ?
                    """,
                    (str(final_path), final_path.name, now, str(row["video_id"])),
                )


def clean_required(row: dict[str, str], key: str) -> str:
    value = str(row.get(key) or "").strip()
    if not value:
        raise ValueError(f"{key} is required")
    return value


def normalize_video_file_name(value: str) -> str:
    return Path(value).name.casefold()


def parse_int(value: object, *, default: int) -> int:
    try:
        text = str(value or "").strip()
        return int(text) if text else default
    except ValueError:
        return default


def safe_batch_id(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in "-_" else "-" for char in value.strip())
    return cleaned[:80].strip("-_") or "batch"


def resolve_batch_file(batch_dir: Path, value: str) -> Path:
    if Path(value).is_absolute():
        raise ValueError("video_file must be relative to the batch folder")
    resolved = (batch_dir / value).resolve()
    batch_root = batch_dir.resolve()
    if resolved != batch_root and batch_root not in resolved.parents:
        raise ValueError("video_file cannot point outside the batch folder")
    return resolved


def assert_file_stable(path: Path) -> None:
    first = path.stat().st_size
    time.sleep(0.2)
    second = path.stat().st_size
    if first <= 0:
        raise ValueError(f"video file is empty: {path.name}")
    if first != second:
        raise ValueError(f"video file is still changing: {path.name}")


def unique_destination(path: Path) -> Path:
    if not path.exists():
        return path
    suffix = utc_now().replace(":", "").replace("+", "Z")
    candidate = path.with_name(f"{path.name}_{suffix}")
    index = 1
    while candidate.exists():
        index += 1
        candidate = path.with_name(f"{path.name}_{suffix}_{index}")
    return candidate


def remove_lock(batch_dir: Path) -> None:
    try:
        (batch_dir / ".importing.lock").unlink()
    except FileNotFoundError:
        pass


def result_to_json(result: object) -> str:
    if hasattr(result, "__dict__"):
        return json.dumps(result.__dict__, indent=2, ensure_ascii=False)
    return json.dumps(result, indent=2, ensure_ascii=False)
