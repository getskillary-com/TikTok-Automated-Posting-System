from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mobile_phone_library.adb_manager import ADBManager
from shared.database import session
from shared.queue_rules import READY_PHONE_STATUS

from .idle_wake import wake_idle_phone
from .pipeline_adapter import WORKFLOW_ROOT, load_workflow_index
from .queue_service import ExecutionQueueService, phone_health_ready, summarize_phone_health
from .worker_state import WorkerStateRepository


EXPECTED_STEADY_STATE_WORKERS = 5
DEFAULT_STAGGER_SECONDS = 25
ACTIVE_WORKER_STATUSES = {"starting", "idle", "running"}


@dataclass(frozen=True)
class ReadinessIssue:
    code: str
    message: str
    phone_id: str = ""
    adb_serial: str = ""
    severity: str = "error"


@dataclass
class WorkerReadinessReport:
    ok: bool
    expected_workers: int
    selected_workers: int
    adb_online_serials: list[str] = field(default_factory=list)
    phones: list[dict[str, Any]] = field(default_factory=list)
    profiles: list[dict[str, Any]] = field(default_factory=list)
    recovered_workers: list[dict[str, Any]] = field(default_factory=list)
    running_tasks: list[dict[str, Any]] = field(default_factory=list)
    active_workers: list[dict[str, Any]] = field(default_factory=list)
    issues: list[ReadinessIssue] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "expected_workers": self.expected_workers,
            "selected_workers": self.selected_workers,
            "adb_online_serials": self.adb_online_serials,
            "phones": self.phones,
            "profiles": self.profiles,
            "recovered_workers": self.recovered_workers,
            "running_tasks": self.running_tasks,
            "active_workers": self.active_workers,
            "issues": [issue.__dict__ for issue in self.issues],
        }


def build_worker_readiness_report(
    *,
    db_path: str | Path | None,
    phones: list[dict[str, Any]],
    expected_workers: int = EXPECTED_STEADY_STATE_WORKERS,
    allow_publish: bool = False,
    pipeline_dry_run: bool = False,
    dry_run: bool = False,
    stop_before_final_publish: bool = False,
    require_task_per_phone: bool = False,
    recover_stale_before_start: bool = True,
) -> WorkerReadinessReport:
    expected = max(1, int(expected_workers or EXPECTED_STEADY_STATE_WORKERS))
    issues: list[ReadinessIssue] = []
    phone_summaries: list[dict[str, Any]] = []
    profile_summaries: list[dict[str, Any]] = []
    recovered_workers: list[dict[str, Any]] = []

    if recover_stale_before_start:
        recovered_workers = WorkerStateRepository(db_path).recover_stale(
            limit=100,
            reason="worker lease expired before steady-state start",
        )

    adb = ADBManager()
    try:
        adb.start_server()
    except Exception as exc:  # noqa: BLE001
        issues.append(ReadinessIssue(code="adb_start_failed", message=f"ADB server start failed: {exc}"))

    adb_devices = adb.devices()
    online_serials = sorted(device.serial for device in adb_devices if device.state == "device")
    selected_count = len(phones)
    if selected_count != expected:
        issues.append(
            ReadinessIssue(
                code="worker_count_mismatch",
                message=f"selected {selected_count} phone(s), expected {expected}",
            )
        )
    if len(online_serials) < expected:
        issues.append(
            ReadinessIssue(
                code="adb_online_count_mismatch",
                message=f"ADB has {len(online_serials)} online device(s), expected at least {expected}",
            )
        )

    profile_index = load_workflow_index()
    service = ExecutionQueueService(db_path)
    running_tasks = selected_running_tasks(db_path, phones)
    active_workers = active_worker_rows(db_path, phones)
    for task in running_tasks:
        issues.append(
            ReadinessIssue(
                code="running_task_exists",
                message=f"task {task['task_id']} is still running",
                phone_id=str(task.get("phone_id") or ""),
            )
        )
    for worker in active_workers:
        issues.append(
            ReadinessIssue(
                code="active_worker_exists",
                message=f"active worker {worker['worker_id']} is still {worker['status']}",
                phone_id=str(worker.get("phone_id") or ""),
                adb_serial=str(worker.get("adb_serial") or ""),
            )
        )

    real_publish = allow_publish and not pipeline_dry_run and not dry_run and not stop_before_final_publish
    for phone in phones:
        phone_id = str(phone.get("phone_id") or "").strip()
        serial = str(phone.get("adb_serial") or "").strip()
        account_type = str(phone.get("account_type") or "").strip() or "marketing"
        app_package = str(phone.get("app_package") or "").strip()
        remote_video_dir = str(phone.get("remote_video_dir") or "").strip()
        status = str(phone.get("current_status") or "").strip()
        summary: dict[str, Any] = {
            "phone_id": phone_id,
            "adb_serial": serial,
            "account_type": account_type,
            "status": status,
            "app_package": app_package,
            "remote_video_dir": remote_video_dir,
        }

        if not phone_id:
            issues.append(ReadinessIssue(code="missing_phone_id", message="phone is missing phone_id", adb_serial=serial))
        if not serial:
            issues.append(ReadinessIssue(code="missing_adb_serial", message="phone is missing adb_serial", phone_id=phone_id))
        if not account_type:
            issues.append(ReadinessIssue(code="missing_account_type", message="phone is missing account_type", phone_id=phone_id, adb_serial=serial))
        if not app_package:
            issues.append(ReadinessIssue(code="missing_app_package", message="phone is missing app_package", phone_id=phone_id, adb_serial=serial))
        if not remote_video_dir:
            issues.append(ReadinessIssue(code="missing_remote_video_dir", message="phone is missing remote_video_dir", phone_id=phone_id, adb_serial=serial))
        if status != READY_PHONE_STATUS:
            issues.append(
                ReadinessIssue(
                    code="phone_not_online_idle",
                    message=f"phone status is {status or 'unknown'}, expected {READY_PHONE_STATUS}",
                    phone_id=phone_id,
                    adb_serial=serial,
                )
            )
        if serial and serial not in online_serials:
            issues.append(ReadinessIssue(code="adb_serial_not_online", message=f"ADB serial is not online: {serial}", phone_id=phone_id, adb_serial=serial))

        profile_report, profile_issues = inspect_phone_profile(
            adb=adb,
            profile_index=profile_index,
            phone=phone,
            online_serials=set(online_serials),
            require_publish_ready=real_publish,
        )
        profile_summaries.append(profile_report)
        issues.extend(profile_issues)
        expected_app = str(profile_report.get("expected_app_package") or "")
        health_app_package = expected_app or app_package
        if app_package and expected_app and app_package != expected_app:
            issues.append(
                ReadinessIssue(
                    code="app_package_mismatch",
                    message=f"phone app_package={app_package}, profile expects {expected_app}",
                    phone_id=phone_id,
                    adb_serial=serial,
                )
            )

        if serial in online_serials:
            wake_result = wake_idle_phone(serial)
            summary["idle_wake_ok"] = wake_result.success
            if not wake_result.success:
                issues.append(
                    ReadinessIssue(
                        code="idle_wake_failed",
                        message=wake_result.failure_reason() or "idle wake failed",
                        phone_id=phone_id,
                        adb_serial=serial,
                    )
                )
            try:
                health = service.adb_manager.health_check(
                    serial,
                    app_package=health_app_package,
                    remote_video_dir=remote_video_dir or "/sdcard/DCIM/Camera",
                )
                summary["health_ready"] = phone_health_ready(health)
                summary["health_summary"] = "" if phone_health_ready(health) else summarize_phone_health(health)
                if not phone_health_ready(health):
                    issues.append(
                        ReadinessIssue(
                            code="phone_health_not_ready",
                            message=summarize_phone_health(health),
                            phone_id=phone_id,
                            adb_serial=serial,
                        )
                    )
            except Exception as exc:  # noqa: BLE001
                summary["health_ready"] = False
                summary["health_summary"] = str(exc)
                issues.append(ReadinessIssue(code="phone_health_failed", message=str(exc), phone_id=phone_id, adb_serial=serial))

        if require_task_per_phone:
            candidates = service.preview(
                phone_id=phone_id,
                account_type=account_type,
                require_phone_ready=True,
                limit=20,
            )
            eligible_count = sum(1 for candidate in candidates if candidate.eligible)
            summary["eligible_task_count"] = eligible_count
            if eligible_count < 1:
                issues.append(ReadinessIssue(code="no_task_for_phone", message="phone has no eligible task", phone_id=phone_id, adb_serial=serial))

        phone_summaries.append(summary)

    return WorkerReadinessReport(
        ok=not any(issue.severity == "error" for issue in issues),
        expected_workers=expected,
        selected_workers=selected_count,
        adb_online_serials=online_serials,
        phones=phone_summaries,
        profiles=profile_summaries,
        recovered_workers=recovered_workers,
        running_tasks=running_tasks,
        active_workers=active_workers,
        issues=issues,
    )


def inspect_phone_profile(
    *,
    adb: ADBManager,
    profile_index: dict[str, Any],
    phone: dict[str, Any],
    online_serials: set[str],
    require_publish_ready: bool,
) -> tuple[dict[str, Any], list[ReadinessIssue]]:
    issues: list[ReadinessIssue] = []
    phone_id = str(phone.get("phone_id") or "").strip()
    serial = str(phone.get("adb_serial") or "").strip()
    account_type = str(phone.get("account_type") or "").strip() or "marketing"
    live_model = ""
    live_size = ""
    if serial in online_serials:
        live_model = clean_adb_line(adb.shell(serial, "getprop", "ro.product.model", timeout=15))
        live_size = clean_adb_line(adb.shell(serial, "wm", "size", timeout=15))
    profile = match_profile(profile_index, phone=phone, account_type=account_type, live_model=live_model)
    report: dict[str, Any] = {
        "phone_id": phone_id,
        "adb_serial": serial,
        "account_type": account_type,
        "live_model": live_model,
        "live_size": live_size,
        "profile_id": "",
        "profile_path": "",
        "index_status": "",
        "file_status": "",
        "device_model": "",
        "screen": "",
        "expected_app_package": "",
    }
    if profile is None:
        issues.append(
            ReadinessIssue(
                code="missing_profile",
                message=f"no workflow profile found for account_type={account_type}",
                phone_id=phone_id,
                adb_serial=serial,
            )
        )
        return report, issues

    profile_path = WORKFLOW_ROOT / str(profile.get("profile_path") or "")
    report["profile_id"] = str(profile.get("profile_id") or "")
    report["profile_path"] = str(profile_path)
    report["index_status"] = str(profile.get("status") or "")
    if not profile_path.exists():
        issues.append(ReadinessIssue(code="profile_file_missing", message=f"profile file not found: {profile_path}", phone_id=phone_id, adb_serial=serial))
        return report, issues

    profile_data = read_json(profile_path)
    profile_status = str(profile_data.get("status") or profile.get("status") or "")
    device = profile_data.get("device", {}) if isinstance(profile_data.get("device"), dict) else {}
    screen = profile_data.get("screen", {}) if isinstance(profile_data.get("screen"), dict) else {}
    report["file_status"] = str(profile_data.get("status") or "")
    report["device_model"] = str(device.get("model") or profile.get("device_model") or "")
    report["screen"] = f"{screen.get('width', '')}x{screen.get('height', '')}"
    report["expected_app_package"] = expected_app_package(profile_data, account_type)

    bound_phone_id = str(profile.get("phone_id") or device.get("known_phone_id") or "").strip()
    bound_serial = str(profile.get("adb_serial") or device.get("known_adb_serial") or "").strip()
    if bound_phone_id and phone_id and bound_phone_id != phone_id:
        issues.append(ReadinessIssue(code="profile_phone_mismatch", message=f"profile phone_id={bound_phone_id}, phone={phone_id}", phone_id=phone_id, adb_serial=serial))
    if bound_serial and serial and bound_serial != serial:
        issues.append(ReadinessIssue(code="profile_serial_mismatch", message=f"profile adb_serial={bound_serial}, phone={serial}", phone_id=phone_id, adb_serial=serial))
    if live_model and report["device_model"] and normalize_device_text(live_model) != normalize_device_text(str(report["device_model"])):
        issues.append(ReadinessIssue(code="profile_model_mismatch", message=f"live model={live_model}, profile model={report['device_model']}", phone_id=phone_id, adb_serial=serial))
    width, height = parse_wm_size(live_size)
    expected_width = int(screen.get("width") or 0)
    expected_height = int(screen.get("height") or 0)
    if width and height and expected_width and expected_height and (width != expected_width or height != expected_height):
        issues.append(ReadinessIssue(code="profile_screen_mismatch", message=f"live screen={width}x{height}, profile={expected_width}x{expected_height}", phone_id=phone_id, adb_serial=serial))
    if require_publish_ready and profile_requires_calibration(profile_status):
        issues.append(
            ReadinessIssue(
                code="profile_requires_calibration",
                message=f"profile status does not allow real publish: {profile_status or 'unknown'}",
                phone_id=phone_id,
                adb_serial=serial,
            )
        )
    return report, issues


def match_profile(
    profile_index: dict[str, Any],
    *,
    phone: dict[str, Any],
    account_type: str,
    live_model: str,
) -> dict[str, Any] | None:
    profiles = [
        profile
        for profile in profile_index.get("profiles", [])
        if isinstance(profile, dict)
        and str(profile.get("account_type") or "") == account_type
        and str(profile.get("status") or "") != "disabled"
    ]
    phone_id = str(phone.get("phone_id") or "").strip()
    serial = str(phone.get("adb_serial") or "").strip()
    for profile in profiles:
        if phone_id and str(profile.get("phone_id") or "") == phone_id:
            return profile
    for profile in profiles:
        if serial and str(profile.get("adb_serial") or "") == serial:
            return profile
    normalized_model = normalize_device_text(live_model)
    if normalized_model:
        model_matches = [
            profile
            for profile in profiles
            if normalize_device_text(str(profile.get("device_model") or "")) == normalized_model
        ]
        if len(model_matches) == 1:
            return model_matches[0]
    return profiles[0] if len(profiles) == 1 else None


def expected_app_package(profile: dict[str, Any], account_type: str) -> str:
    apps = profile.get("apps", {}) if isinstance(profile.get("apps"), dict) else {}
    if account_type == "marketing":
        app = apps.get("tiktok_studio", {}) if isinstance(apps.get("tiktok_studio"), dict) else {}
        return str(app.get("package") or "")
    if account_type == "showcase":
        app = apps.get("tiktok", {}) if isinstance(apps.get("tiktok"), dict) else {}
        return str(app.get("package") or "")
    return ""


def selected_running_tasks(db_path: str | Path | None, phones: list[dict[str, Any]]) -> list[dict[str, Any]]:
    phone_ids = [str(phone.get("phone_id") or "") for phone in phones if str(phone.get("phone_id") or "")]
    if not phone_ids:
        return []
    placeholders = ", ".join("?" for _ in phone_ids)
    with session(db_path) as connection:
        rows = connection.execute(
            f"""
            SELECT task_id, phone_id, locked_by_worker, lock_expires_at
            FROM release_tasks
            WHERE status = 'running' AND phone_id IN ({placeholders})
            ORDER BY updated_at DESC
            """,
            phone_ids,
        ).fetchall()
    return [dict(row) for row in rows]


def active_worker_rows(db_path: str | Path | None, phones: list[dict[str, Any]]) -> list[dict[str, Any]]:
    phone_ids = [str(phone.get("phone_id") or "") for phone in phones if str(phone.get("phone_id") or "")]
    if not phone_ids:
        return []
    placeholders = ", ".join("?" for _ in phone_ids)
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    params: list[Any] = [*phone_ids, *sorted(ACTIVE_WORKER_STATUSES), now]
    with session(db_path) as connection:
        rows = connection.execute(
            f"""
            SELECT worker_id, phone_id, adb_serial, status, current_task_id, current_attempt_id, lease_expires_at, last_error
            FROM execution_workers
            WHERE phone_id IN ({placeholders})
              AND status IN ({", ".join("?" for _ in ACTIVE_WORKER_STATUSES)})
              AND lease_expires_at != ''
              AND lease_expires_at >= ?
            ORDER BY lease_expires_at DESC
            """,
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def profile_requires_calibration(status: str) -> bool:
    text = str(status or "").strip().casefold()
    return not text or text.startswith("draft") or "requires_calibration" in text


def parse_wm_size(output: str) -> tuple[int, int]:
    match = re.search(r"(\d+)x(\d+)", str(output or ""))
    if not match:
        return 0, 0
    return int(match.group(1)), int(match.group(2))


def normalize_device_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def clean_adb_line(output: str) -> str:
    for line in str(output or "").splitlines():
        clean_line = line.strip()
        if clean_line and not clean_line.startswith("* daemon"):
            return clean_line
    return ""


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"JSON must contain an object: {path}")
    return data
