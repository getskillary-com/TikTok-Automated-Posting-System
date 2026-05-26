from __future__ import annotations

from dataclasses import dataclass


CAPTION_STATUSES = {
    "unused",
    "assigned",
    "used",
    "disabled",
    "removed",
}


@dataclass(frozen=True)
class CaptionImportResult:
    caption_id: str
    status: str
    duplicate: bool
