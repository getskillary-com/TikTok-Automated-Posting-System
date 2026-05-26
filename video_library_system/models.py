from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


VIDEO_STATUSES = {
    "unused",
    "assigned",
    "publishing",
    "published",
    "failed",
    "archived",
    "disabled",
    "removed",
}

SUPPORTED_VIDEO_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".m4v",
    ".webm",
    ".3gp",
    ".3gpp",
    ".mkv",
    ".avi",
}


@dataclass(frozen=True)
class VideoMetadata:
    file_path: Path
    file_name: str
    file_ext: str
    file_size: int
    sha256: str
    duration_seconds: float | None = None
    width: int | None = None
    height: int | None = None


@dataclass(frozen=True)
class VideoImportResult:
    video_id: str
    file_path: str
    status: str
    duplicate: bool
