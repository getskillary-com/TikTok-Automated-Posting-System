from __future__ import annotations

from datetime import datetime, timedelta, timezone as datetime_timezone
from typing import Any
from zoneinfo import ZoneInfo

from .account_rules import normalize_account_type, product_fields_from_mapping, validate_account_workflow


CLAIMABLE_TASK_STATUSES = {"pending", "ready"}
BLOCKING_PHONE_STATUSES = {"running", "disabled", "removed"}
READY_PHONE_STATUS = "online_idle"


def evaluate_task_candidate(
    task: dict[str, Any],
    *,
    now: datetime,
    preparation_window_minutes: int,
    require_phone_ready: bool,
    allow_overdue: bool,
) -> tuple[bool, str]:
    phone_status = str(task.get("phone_status") or "")
    if phone_status in BLOCKING_PHONE_STATUSES:
        return False, f"phone is {phone_status}"
    if require_phone_ready and phone_status != READY_PHONE_STATUS:
        return False, f"phone is {phone_status or 'unknown'}, expected {READY_PHONE_STATUS}"

    retry_count = int(task.get("retry_count") or 0)
    max_retries = int(task.get("max_retries") or 0)
    if retry_count > max_retries:
        return False, f"retry_count {retry_count} exceeded max_retries {max_retries}"

    account_type = normalize_account_type(str(task.get("phone_account_type") or task.get("account_type") or "marketing"))
    publish_mode = str(task.get("publish_mode") or "scheduled")
    workflow_reason = validate_account_workflow(
        account_type=account_type,
        publish_mode=publish_mode,
        product_fields=product_fields_from_mapping(task),
    )
    if workflow_reason:
        return False, workflow_reason

    scheduled_at = parse_datetime(str(task.get("scheduled_at") or ""), now.tzinfo or datetime_timezone.utc)
    if publish_mode == "immediate":
        if scheduled_at <= now:
            return True, "immediate task is due"
        return False, f"immediate task is scheduled for {scheduled_at.isoformat()}"

    start_at = scheduled_at - timedelta(minutes=max(0, preparation_window_minutes))
    if scheduled_at <= now:
        if allow_overdue:
            return True, "scheduled task is overdue but allow_overdue is enabled"
        return False, f"scheduled target is already past: {scheduled_at.isoformat()}"
    if account_type in {"marketing", "showcase"} and publish_mode == "scheduled":
        return True, f"{account_type} scheduled task is claimable immediately, target={scheduled_at.isoformat()}"
    if now >= start_at:
        return True, f"scheduled task is inside preparation window, target={scheduled_at.isoformat()}"
    return False, f"scheduled task opens at {start_at.isoformat()}"


def parse_datetime(value: str, fallback_tz: Any) -> datetime:
    text = value.strip().replace("Z", "+00:00")
    if not text:
        raise ValueError("scheduled_at is empty")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=fallback_tz)
    return parsed.astimezone(fallback_tz)


def resolve_timezone(name: str):
    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001
        if name in {"Asia/Shanghai", "UTC+8", "+08:00"}:
            return datetime_timezone(timedelta(hours=8))
        if name in {"America/Sao_Paulo", "Brazil/East", "BRT"}:
            return datetime_timezone(timedelta(hours=-3))
        return datetime_timezone.utc
