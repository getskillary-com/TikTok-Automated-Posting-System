from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from .models import ReleaseTaskCreateResult, ReleaseTaskPlanItem
from .task_repository import ReleaseTaskRepository


class ReleaseTaskService:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.repository = ReleaseTaskRepository(db_path)

    def create_task(
        self,
        *,
        video_id: str,
        phone_id: str,
        scheduled_at: str,
        caption_id: str = "",
        publish_mode: str = "scheduled",
        product_id: str = "",
        product_link: str = "",
        product_name: str = "",
        product_search_title: str = "",
        product_publish_name: str = "",
        max_retries: int = 3,
        note: str = "",
        allow_reuse: bool = False,
        timezone: str = "Asia/Shanghai",
    ) -> ReleaseTaskCreateResult:
        return self.repository.create_task(
            video_id=video_id,
            caption_id=caption_id,
            phone_id=phone_id,
            scheduled_at=scheduled_at,
            publish_mode=publish_mode,
            product_id=product_id,
            product_link=product_link,
            product_name=product_name,
            product_search_title=product_search_title,
            product_publish_name=product_publish_name,
            max_retries=max_retries,
            note=note,
            allow_reuse=allow_reuse,
            timezone=timezone,
        )

    def create_from_binding(
        self,
        *,
        binding_id: str,
        phone_id: str,
        scheduled_at: str,
        publish_mode: str = "scheduled",
        product_id: str = "",
        product_link: str = "",
        product_name: str = "",
        product_search_title: str = "",
        product_publish_name: str = "",
        max_retries: int = 3,
        note: str = "",
        allow_reuse: bool = False,
        timezone: str = "Asia/Shanghai",
    ) -> ReleaseTaskCreateResult:
        return self.repository.create_from_binding(
            binding_id=binding_id,
            phone_id=phone_id,
            scheduled_at=scheduled_at,
            publish_mode=publish_mode,
            product_id=product_id,
            product_link=product_link,
            product_name=product_name,
            product_search_title=product_search_title,
            product_publish_name=product_publish_name,
            max_retries=max_retries,
            note=note,
            allow_reuse=allow_reuse,
            timezone=timezone,
        )

    def import_csv(
        self,
        path: str | Path,
        *,
        allow_reuse: bool = False,
        timezone: str = "Asia/Shanghai",
    ) -> list[ReleaseTaskCreateResult]:
        items = read_plan_csv(Path(path).expanduser().resolve())
        results: list[ReleaseTaskCreateResult] = []
        for item in items:
            results.append(
                self.create_task(
                    video_id=item.video_id,
                    caption_id=item.caption_id,
                    phone_id=item.phone_id,
                    scheduled_at=item.scheduled_at,
                    publish_mode=item.publish_mode,
                    product_id=item.product_id,
                    product_link=item.product_link,
                    product_name=item.product_name,
                    product_search_title=item.product_search_title,
                    product_publish_name=item.product_publish_name,
                    max_retries=item.max_retries,
                    note=item.note,
                    allow_reuse=allow_reuse,
                    timezone=timezone,
                )
            )
        return results


def read_plan_csv(path: Path) -> list[ReleaseTaskPlanItem]:
    if not path.exists():
        raise FileNotFoundError(f"Task plan CSV not found: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        required = {"video_id", "phone_id", "scheduled_at"}
        if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
            raise ValueError("CSV must contain video_id, phone_id, and scheduled_at columns.")
        items: list[ReleaseTaskPlanItem] = []
        for row in reader:
            if not str(row.get("video_id") or "").strip():
                continue
            items.append(
                ReleaseTaskPlanItem(
                    video_id=str(row.get("video_id") or "").strip(),
                    caption_id=str(row.get("caption_id") or "").strip(),
                    phone_id=str(row.get("phone_id") or "").strip(),
                    scheduled_at=str(row.get("scheduled_at") or "").strip(),
                    publish_mode=str(row.get("publish_mode") or "scheduled").strip() or "scheduled",
                    product_id=str(row.get("product_id") or "").strip(),
                    product_link=str(row.get("product_link") or "").strip(),
                    product_name=str(row.get("product_name") or "").strip(),
                    product_search_title=str(row.get("product_search_title") or row.get("search_title") or "").strip(),
                    product_publish_name=str(row.get("product_publish_name") or row.get("publish_name") or "").strip(),
                    max_retries=parse_int(row.get("max_retries"), default=3),
                    note=str(row.get("note") or "").strip(),
                )
            )
    return items


def parse_int(value: Any, *, default: int) -> int:
    try:
        if value is None or str(value).strip() == "":
            return default
        return int(str(value).strip())
    except ValueError:
        return default
