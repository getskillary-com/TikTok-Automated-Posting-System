from __future__ import annotations

from dataclasses import dataclass


PUBLISH_MODES = {"scheduled", "immediate", "timed"}
TASK_STATUSES = {
    "pending",
    "ready",
    "running",
    "published",
    "dry_run",
    "debug_ready",
    "failed",
    "cancelled",
    "paused",
    "removed",
}
ACTIVE_TASK_STATUSES = {"pending", "ready", "running", "paused"}
TERMINAL_TASK_STATUSES = {"published", "dry_run", "debug_ready", "failed", "cancelled"}


@dataclass(frozen=True)
class ReleaseTaskCreateResult:
    task_id: str
    status: str
    duplicate: bool = False


@dataclass(frozen=True)
class ReleaseTaskPlanItem:
    video_id: str
    phone_id: str
    scheduled_at: str
    caption_id: str = ""
    publish_mode: str = "scheduled"
    product_id: str = ""
    product_link: str = ""
    product_name: str = ""
    product_search_title: str = ""
    product_publish_name: str = ""
    max_retries: int = 3
    note: str = ""

