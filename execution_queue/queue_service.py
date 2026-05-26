from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from mobile_phone_library.adb_manager import ADBManager
from mobile_phone_library.models import PhoneHealthResult
from mobile_phone_library.phone_repository import PhoneRepository

from .models import BLOCKING_PHONE_STATUSES, READY_PHONE_STATUS, ExecutionClaim, PipelineRunPlan, PipelineRunResult, QueueCandidate
from .pipeline_adapter import RUN_ROOT, UploadPipelineAdapter, command_to_text, load_base_config
from .queue_repository import ExecutionQueueRepository


class ExecutionQueueService:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.repository = ExecutionQueueRepository(db_path)
        self.adapter = UploadPipelineAdapter()
        self.phone_repository = PhoneRepository(db_path)
        self.adb_manager = ADBManager()

    def preview(
        self,
        *,
        task_id: str = "",
        phone_id: str = "",
        adb_serial: str = "",
        account_type: str = "",
        timezone: str = "Asia/Shanghai",
        preparation_window_minutes: int = 60,
        require_phone_ready: bool = True,
        allow_overdue: bool = False,
        limit: int = 20,
    ) -> list[QueueCandidate]:
        return self.repository.list_candidates(
            task_id=task_id,
            phone_id=phone_id,
            adb_serial=adb_serial,
            account_type=account_type,
            timezone=timezone,
            preparation_window_minutes=preparation_window_minutes,
            require_phone_ready=require_phone_ready,
            allow_overdue=allow_overdue,
            limit=limit,
        )

    def claim_next(
        self,
        *,
        task_id: str = "",
        phone_id: str = "",
        adb_serial: str = "",
        account_type: str = "",
        worker_id: str = "",
        lease_seconds: int = 300,
        timezone: str = "Asia/Shanghai",
        preparation_window_minutes: int = 60,
        require_phone_ready: bool = True,
        allow_overdue: bool = False,
    ) -> ExecutionClaim | None:
        return self.repository.claim_next(
            task_id=task_id,
            phone_id=phone_id,
            adb_serial=adb_serial,
            account_type=account_type,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
            timezone=timezone,
            preparation_window_minutes=preparation_window_minutes,
            require_phone_ready=require_phone_ready,
            allow_overdue=allow_overdue,
        )

    def run_once(
        self,
        *,
        task_id: str = "",
        phone_id: str = "",
        adb_serial: str = "",
        account_type: str = "",
        worker_id: str = "",
        lease_seconds: int = 300,
        dry_run: bool = False,
        pipeline_dry_run: bool = False,
        allow_publish: bool = False,
        stop_before_final_publish: bool = False,
        timezone: str = "Asia/Shanghai",
        preparation_window_minutes: int = 60,
        require_phone_ready: bool = True,
        allow_overdue: bool = False,
        timeout_seconds: int = 7200,
        claim_hook: Callable[[ExecutionClaim, PipelineRunPlan], None] | None = None,
    ) -> dict[str, Any]:
        if not dry_run and not pipeline_dry_run and not allow_publish and not stop_before_final_publish:
            raise RuntimeError(
                "Real execution requires --allow-publish. Use --dry-run to only generate the run plan, "
                "or --stop-before-final-publish for a pre-publish debug run."
            )
        refreshed_phones = self.refresh_candidate_phone_statuses(
            task_id=task_id,
            phone_id=phone_id,
            adb_serial=adb_serial,
            account_type=account_type,
            timezone=timezone,
            preparation_window_minutes=preparation_window_minutes,
            require_phone_ready=require_phone_ready,
            allow_overdue=allow_overdue,
        )
        claim = self.claim_next(
            task_id=task_id,
            phone_id=phone_id,
            adb_serial=adb_serial,
            account_type=account_type,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
            timezone=timezone,
            preparation_window_minutes=preparation_window_minutes,
            require_phone_ready=require_phone_ready,
            allow_overdue=allow_overdue,
        )
        if claim is None:
            return {"status": "no_task", "refreshed_phones": refreshed_phones}

        try:
            plan = self.adapter.prepare_plan(
                claim,
                allow_publish=allow_publish,
                pipeline_dry_run=pipeline_dry_run,
                stop_before_final_publish=stop_before_final_publish,
                schedule_timezone=timezone,
            )
        except Exception as exc:  # noqa: BLE001
            attempt_dir = RUN_ROOT / claim.task_id / f"{claim.attempt_no:02d}_{claim.attempt_id}"
            attempt_dir.mkdir(parents=True, exist_ok=True)
            state_path = attempt_dir / "state.json"
            state_path.write_text(
                json.dumps({"run_id": claim.attempt_id, "error": str(exc)}, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            next_status = self.repository.mark_failure(
                claim,
                run_dir=str(attempt_dir),
                state_path=str(state_path),
                failure_reason=str(exc),
                retryable=failure_is_retryable(str(exc)),
            )
            return {
                "status": "failed",
                "phase": "prepare_plan",
                "next_task_status": next_status,
                "claim": claim,
                "error": str(exc),
                "refreshed_phones": refreshed_phones,
            }
        if claim_hook is not None:
            claim_hook(claim, plan)
        if dry_run:
            self.repository.mark_dry_run(claim, run_dir=str(plan.attempt_dir), state_path=str(plan.state_path))
            return {
                "status": "dry_run",
                "claim": claim,
                "plan": plan,
                "command_text": command_to_text(plan.command),
                "refreshed_phones": refreshed_phones,
            }

        task_validation_error = validate_task_payload(claim)
        if task_validation_error:
            next_status = self.repository.mark_failure(
                claim,
                run_dir=str(plan.attempt_dir),
                state_path=str(plan.state_path),
                failure_reason=task_validation_error,
                retryable=False,
            )
            return {
                "status": "failed",
                "phase": "task_validation",
                "next_task_status": next_status,
                "claim": claim,
                "plan": plan,
                "error": task_validation_error,
                "retryable": False,
                "refreshed_phones": refreshed_phones,
            }

        phone_ready, phone_reason, phone_health = self.phone_preflight(claim)
        phone_health_summary = phone_health_status_summary(phone_health, task=claim.task) if phone_health else phone_reason
        if not phone_ready:
            next_status = self.repository.mark_failure(
                claim,
                run_dir=str(plan.attempt_dir),
                state_path=str(plan.state_path),
                failure_reason=phone_reason,
                retryable=True,
            )
            return {
                "status": "failed",
                "phase": "phone_preflight",
                "next_task_status": next_status,
                "claim": claim,
                "plan": plan,
                "phone_health": phone_health,
                "phone_health_summary": phone_health_summary,
                "error": phone_reason,
                "retryable": True,
                "refreshed_phones": refreshed_phones,
            }

        result: PipelineRunResult | None = None
        try:
            result = self.adapter.run_plan(plan, timeout_seconds=timeout_seconds)
        except Exception as exc:  # noqa: BLE001
            next_status = self.repository.mark_failure(
                claim,
                run_dir=str(plan.attempt_dir),
                state_path=str(plan.state_path),
                failure_reason=str(exc),
                retryable=failure_is_retryable(str(exc)),
            )
            return {
                "status": "failed",
                "next_task_status": next_status,
                "claim": claim,
                "plan": plan,
                "error": str(exc),
                "retryable": failure_is_retryable(str(exc)),
                "refreshed_phones": refreshed_phones,
            }

        if result.returncode == 0 and pipeline_dry_run:
            self.repository.mark_dry_run(claim, run_dir=str(plan.attempt_dir), state_path=str(plan.state_path))
            return {"status": "pipeline_dry_run", "claim": claim, "plan": plan, "result": result, "phone_health": phone_health, "phone_health_summary": phone_health_summary, "refreshed_phones": refreshed_phones}

        if result.returncode == 0 and pipeline_stopped_before_final_publish(plan.state_path):
            self.repository.mark_debug_ready(claim, run_dir=str(plan.attempt_dir), state_path=str(plan.state_path))
            return {
                "status": "debug_ready",
                "claim": claim,
                "plan": plan,
                "result": result,
                "phone_health": phone_health,
                "phone_health_summary": phone_health_summary,
                "refreshed_phones": refreshed_phones,
            }

        if result.returncode == 0:
            validation_error = validate_pipeline_success(plan, claim)
            if validation_error:
                next_status = self.repository.mark_failure(
                    claim,
                    run_dir=str(plan.attempt_dir),
                    state_path=str(plan.state_path),
                    failure_reason=validation_error,
                    retryable=failure_is_retryable(validation_error),
                )
                return {
                    "status": "failed",
                    "phase": "pipeline_success_validation",
                    "next_task_status": next_status,
                    "claim": claim,
                    "plan": plan,
                    "result": result,
                    "phone_health": phone_health,
                    "phone_health_summary": phone_health_summary,
                    "error": validation_error,
                    "retryable": failure_is_retryable(validation_error),
                    "refreshed_phones": refreshed_phones,
                }
            self.repository.mark_success(claim, run_dir=str(plan.attempt_dir), state_path=str(plan.state_path))
            return {"status": "published", "claim": claim, "plan": plan, "result": result, "phone_health": phone_health, "phone_health_summary": phone_health_summary, "refreshed_phones": refreshed_phones}

        failure_reason = summarize_failure(result)
        retryable = failure_is_retryable(failure_reason)
        next_status = self.repository.mark_failure(
            claim,
            run_dir=str(plan.attempt_dir),
            state_path=str(plan.state_path),
            failure_reason=failure_reason,
            retryable=retryable,
        )
        return {
            "status": "failed",
            "next_task_status": next_status,
            "claim": claim,
            "plan": plan,
            "result": result,
            "phone_health": phone_health,
            "phone_health_summary": phone_health_summary,
            "error": failure_reason,
            "retryable": retryable,
            "refreshed_phones": refreshed_phones,
        }

    def run_loop(
        self,
        *,
        task_id: str = "",
        phone_id: str = "",
        adb_serial: str = "",
        account_type: str = "",
        worker_id: str = "",
        lease_seconds: int = 300,
        dry_run: bool = False,
        pipeline_dry_run: bool = False,
        allow_publish: bool = False,
        stop_before_final_publish: bool = False,
        timezone: str = "Asia/Shanghai",
        preparation_window_minutes: int = 60,
        require_phone_ready: bool = True,
        allow_overdue: bool = False,
        timeout_seconds: int = 7200,
        poll_seconds: int = 5,
        max_runs: int = 0,
        stop_when_idle: bool = False,
    ) -> dict[str, Any]:
        if max_runs < 0:
            raise ValueError("max_runs cannot be negative.")
        clean_poll_seconds = max(1, poll_seconds)
        outcomes: list[dict[str, Any]] = []
        completed_runs = 0
        stopped_reason = ""

        while max_runs == 0 or completed_runs < max_runs:
            outcome = self.run_once(
                task_id=task_id,
                phone_id=phone_id,
                adb_serial=adb_serial,
                account_type=account_type,
                worker_id=worker_id,
                lease_seconds=lease_seconds,
                dry_run=dry_run,
                pipeline_dry_run=pipeline_dry_run,
                allow_publish=allow_publish,
                stop_before_final_publish=stop_before_final_publish,
                timezone=timezone,
                preparation_window_minutes=preparation_window_minutes,
                require_phone_ready=require_phone_ready,
                allow_overdue=allow_overdue,
                timeout_seconds=timeout_seconds,
            )
            outcomes.append(summarize_loop_outcome(outcome))
            status = str(outcome.get("status") or "")
            if status == "no_task":
                stopped_reason = "idle"
                if stop_when_idle:
                    break
                time.sleep(clean_poll_seconds)
                continue

            completed_runs += 1
            if dry_run and max_runs == 0:
                stopped_reason = "dry_run_completed"
                break

        if not stopped_reason:
            stopped_reason = "max_runs_reached" if max_runs else "stopped"
        return {
            "status": "loop_stopped",
            "reason": stopped_reason,
            "runs": completed_runs,
            "outcomes": outcomes,
        }

    def refresh_candidate_phone_statuses(
        self,
        *,
        task_id: str = "",
        phone_id: str = "",
        adb_serial: str = "",
        account_type: str = "",
        timezone: str = "Asia/Shanghai",
        preparation_window_minutes: int = 60,
        require_phone_ready: bool = True,
        allow_overdue: bool = False,
    ) -> list[dict[str, Any]]:
        if not require_phone_ready:
            return []
        candidates = self.repository.list_candidates(
            task_id=task_id,
            phone_id=phone_id,
            adb_serial=adb_serial,
            account_type=account_type,
            timezone=timezone,
            preparation_window_minutes=preparation_window_minutes,
            require_phone_ready=False,
            allow_overdue=allow_overdue,
            limit=50,
        )
        refreshed: list[dict[str, Any]] = []
        seen_phone_ids: set[str] = set()
        base_config = load_base_config()
        default_adb_config = base_config.get("adb", {}) if isinstance(base_config.get("adb", {}), dict) else {}
        for candidate in candidates:
            task = candidate.task
            phone_id = str(task.get("phone_id") or "").strip()
            phone_status = str(task.get("phone_status") or "").strip()
            if not phone_id or phone_id in seen_phone_ids:
                continue
            seen_phone_ids.add(phone_id)
            if phone_status == READY_PHONE_STATUS or phone_status in BLOCKING_PHONE_STATUSES:
                continue
            phone = self.phone_repository.get(phone_id)
            serial = str((phone or {}).get("adb_serial") or task.get("phone_adb_serial") or "").strip()
            if not phone or not serial:
                continue
            app_package = str(
                phone.get("app_package")
                or task.get("phone_app_package")
                or default_adb_config.get("app_package")
                or ""
            ).strip()
            remote_video_dir = str(
                phone.get("remote_video_dir")
                or task.get("phone_remote_video_dir")
                or default_adb_config.get("remote_video_dir")
                or "/sdcard/DCIM/Camera"
            ).strip()
            try:
                result = self.adb_manager.health_check(
                    serial,
                    app_package=app_package,
                    remote_video_dir=remote_video_dir or "/sdcard/DCIM/Camera",
                )
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
            check_id = self.phone_repository.record_health(phone_id, result)
            refreshed.append(
                {
                    "phone_id": phone_id,
                    "previous_status": phone_status,
                    "check_id": check_id,
                    "ready": phone_health_ready(result),
                    "summary": "" if phone_health_ready(result) else summarize_phone_health(result),
                }
            )
        return refreshed

    def phone_preflight(self, claim: ExecutionClaim) -> tuple[bool, str, PhoneHealthResult | None]:
        phone = self.phone_repository.get(claim.phone_id)
        if not phone:
            return False, f"phone preflight failed: phone not found {claim.phone_id}", None

        serial = str(phone.get("adb_serial") or claim.task.get("phone_adb_serial") or "").strip()
        if not serial:
            return False, f"phone preflight failed: phone {claim.phone_id} has no ADB serial", None

        base_config = load_base_config()
        default_adb_config = base_config.get("adb", {}) if isinstance(base_config.get("adb", {}), dict) else {}
        app_package = str(
            phone.get("app_package")
            or claim.task.get("phone_app_package")
            or default_adb_config.get("app_package")
            or ""
        ).strip()
        remote_video_dir = str(
            phone.get("remote_video_dir")
            or claim.task.get("phone_remote_video_dir")
            or default_adb_config.get("remote_video_dir")
            or "/sdcard/DCIM/Camera"
        ).strip()
        try:
            result = self.adb_manager.health_check(
                serial,
                app_package=app_package,
                remote_video_dir=remote_video_dir or "/sdcard/DCIM/Camera",
            )
        except Exception as exc:  # noqa: BLE001
            return False, f"phone preflight failed: {exc}", None

        if phone_health_ready(result):
            ocr_ready, ocr_reason = ocr_health_for_task(claim.task)
            if ocr_ready:
                return True, "", result
            return False, "phone preflight failed: " + ocr_reason, result
        return False, summarize_phone_health(result), result


def summarize_failure(result: PipelineRunResult) -> str:
    stderr = read_tail(result.stderr_path)
    stdout = read_tail(result.stdout_path)
    details = stderr or stdout or f"pipeline exited with code {result.returncode}"
    return details[-1000:]


NON_RETRYABLE_FAILURE_MARKERS = (
    "task validation failed",
    "marketing task cannot contain product fields",
    "showcase task must contain product",
    "target product was not found",
    "product was not found in tiktok shop",
    "ordinary tiktok search results",
    "normalized product_search_title is empty",
    "product_search_title is required",
    "paddleocr runtime is not available",
    "required paddleocr failed",
    "paddleocr failed",
    "ocr model",
    "shop add confirmation dialog is still visible after tapping confirm",
)


def failure_is_retryable(reason: str) -> bool:
    text = str(reason or "").casefold()
    if not text:
        return True
    return not any(marker in text for marker in NON_RETRYABLE_FAILURE_MARKERS)


def validate_task_payload(claim: ExecutionClaim) -> str:
    task = claim.task
    errors: list[str] = []
    video_path = str(task.get("video_file_path") or "").strip()
    if not video_path:
        errors.append("video file path is empty")
    elif not Path(video_path).exists():
        errors.append(f"video file does not exist: {video_path}")

    caption = str(task.get("caption_content") or "").strip()
    if not caption:
        errors.append("caption is empty")

    publish_mode = str(task.get("publish_mode") or "scheduled").strip().lower()
    scheduled_at = str(task.get("scheduled_at") or "").strip()
    if publish_mode == "scheduled":
        if not scheduled_at:
            errors.append("scheduled_at is empty for scheduled task")
        else:
            try:
                datetime.fromisoformat(scheduled_at)
            except ValueError:
                errors.append(f"scheduled_at is not valid ISO datetime: {scheduled_at}")

    account_type = str(task.get("phone_account_type") or "marketing").strip().lower()
    if account_type == "showcase":
        product_match_title = str(task.get("product_search_title") or task.get("product_name") or "").strip()
        product_publish_name = str(task.get("product_publish_name") or task.get("product_name") or product_match_title).strip()
        if not product_match_title:
            errors.append("showcase product match title is empty")
        if not product_publish_name:
            errors.append("showcase product publish name is empty")

    if not errors:
        return ""
    return "task validation failed: " + "; ".join(errors)


def ocr_health_for_task(task: dict[str, Any]) -> tuple[bool, str]:
    account_type = str(task.get("phone_account_type") or "marketing").strip().lower()
    if account_type != "showcase":
        return True, "ocr_runtime_available=not_required"
    try:
        package_root = Path(__file__).resolve().parents[1] / "upload_system"
        if str(package_root) not in sys.path:
            sys.path.insert(0, str(package_root))
        from upload_system.ai import paddle_ocr_runtime_available
    except Exception as exc:  # noqa: BLE001
        return False, f"ocr_runtime_available=False import_error={exc}"
    if paddle_ocr_runtime_available():
        return True, "ocr_runtime_available=True"
    return False, "ocr_runtime_available=False"


def read_tail(path: Path, *, limit: int = 4000) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[-limit:].strip()


def pipeline_stopped_before_final_publish(state_path: Path) -> bool:
    if not state_path.exists():
        return False
    try:
        with state_path.open("r", encoding="utf-8") as file:
            state = json.load(file)
    except Exception:  # noqa: BLE001
        return False
    if not isinstance(state, dict):
        return False
    return bool(state.get("stop_before_final_publish") and state.get("stopped_before_final_publish"))


def validate_pipeline_success(plan: PipelineRunPlan, claim: ExecutionClaim) -> str:
    if not plan.state_path.exists():
        return f"pipeline success validation failed: state file not found: {plan.state_path}"
    try:
        with plan.state_path.open("r", encoding="utf-8") as file:
            state = json.load(file)
    except Exception as exc:  # noqa: BLE001
        return f"pipeline success validation failed: cannot read state file: {exc}"

    if state.get("dry_run"):
        return "pipeline success validation failed: pipeline state is dry_run"
    if not state.get("allow_publish"):
        return "pipeline success validation failed: pipeline state did not allow publish"
    if not str(state.get("remote_video_path") or "").strip():
        return "pipeline success validation failed: remote_video_path is empty"

    publish_mode = str(state.get("publish_mode") or claim.task.get("publish_mode") or "scheduled").strip().lower()
    final_step = "tap_scheduled_publish" if publish_mode == "scheduled" else "tap_publish"
    completed_steps = set(str(step) for step in state.get("completed_steps", []))
    account_type = str(state.get("account_type") or claim.task.get("phone_account_type") or "marketing").strip().lower()
    if account_type == "marketing":
        required_steps = [
            "push_video",
            "wait_schedule_picker_open",
            "set_schedule_datetime",
            "verify_schedule_configured",
            final_step,
            "minimize_tiktok_studio",
        ]
        if not state.get("schedule_configured_verified"):
            return "pipeline success validation failed: scheduled publish state was not verified"
    elif account_type == "showcase":
        if publish_mode == "scheduled":
            required_steps = [
                "push_video",
                "tap_product_final_add",
                "set_schedule_datetime",
                "verify_schedule_configured",
                "tap_scheduled_publish",
                "minimize_tiktok_app",
            ]
            if not state.get("schedule_configured_verified"):
                return "pipeline success validation failed: scheduled publish state was not verified"
        else:
            required_steps = ["push_video", "tap_product_final_add", "tap_publish", "minimize_tiktok_app"]
    else:
        required_steps = ["push_video", final_step, "minimize_and_cleanup"]
    missing_steps = [step for step in required_steps if step not in completed_steps]
    if missing_steps:
        return "pipeline success validation failed: missing completed steps " + ", ".join(missing_steps)
    return ""


def phone_health_ready(result: PhoneHealthResult) -> bool:
    return all(
        (
            result.adb_online,
            result.authorized,
            result.screenshot_ok,
            result.app_detected,
            result.media_dir_writable,
        )
    )


def summarize_phone_health(result: PhoneHealthResult) -> str:
    parts = [
        f"adb_online={result.adb_online}",
        f"authorized={result.authorized}",
        f"screenshot_ok={result.screenshot_ok}",
        f"app_detected={result.app_detected}",
        f"media_dir_writable={result.media_dir_writable}",
    ]
    device_state = result.details.get("device_state", "")
    if device_state:
        parts.append(f"device_state={device_state}")
    for key in ("screenshot_error", "app_error", "media_dir_error", "foreground_error"):
        value = result.details.get(key, "")
        if value:
            parts.append(f"{key}={value}")
    return "phone preflight failed: " + ", ".join(parts)


def phone_health_status_summary(result: PhoneHealthResult | None, *, task: dict[str, Any] | None = None) -> str:
    if result is None:
        return "unavailable"
    ocr_ready, ocr_reason = ocr_health_for_task(task or {})
    fields = {
        "adb_online": result.adb_online,
        "authorized": result.authorized,
        "screenshot_ok": result.screenshot_ok,
        "app_detected": result.app_detected,
        "media_dir_writable": result.media_dir_writable,
        "ocr_ready": ocr_ready,
    }
    status = "ready" if all(fields.values()) else "not_ready"
    details = ", ".join(f"{key}={value}" for key, value in fields.items())
    return f"{status}: {details}, {ocr_reason}"


def summarize_loop_outcome(outcome: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "status": outcome.get("status"),
    }
    for key in ("phase", "next_task_status", "retryable", "phone_health_summary", "error"):
        if outcome.get(key) is not None and outcome.get(key) != "":
            summary[key] = outcome[key]
    claim = outcome.get("claim")
    if isinstance(claim, ExecutionClaim):
        summary.update(
            {
                "task_id": claim.task_id,
                "attempt_id": claim.attempt_id,
                "log_id": claim.log_id,
                "phone_id": claim.phone_id,
                "attempt_no": claim.attempt_no,
            }
        )
    plan = outcome.get("plan")
    if isinstance(plan, PipelineRunPlan):
        summary.update(
            {
                "run_dir": str(plan.attempt_dir),
                "state_path": str(plan.state_path),
                "command": command_to_text(plan.command),
            }
        )
    result = outcome.get("result")
    if isinstance(result, PipelineRunResult):
        summary.update(
            {
                "returncode": result.returncode,
                "stdout": str(result.stdout_path),
                "stderr": str(result.stderr_path),
            }
        )
    refreshed_phones = outcome.get("refreshed_phones")
    if refreshed_phones:
        summary["refreshed_phones"] = refreshed_phones
    return summary
