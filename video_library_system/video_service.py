from __future__ import annotations

from pathlib import Path

from .models import VideoImportResult
from .video_repository import VideoRepository
from .video_scanner import iter_video_files, read_video_metadata


class VideoLibraryService:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.repository = VideoRepository(db_path)

    def import_file(
        self,
        path: str | Path,
        *,
        title: str = "",
        tags: list[str] | None = None,
        batch_name: str = "",
        note: str = "",
    ) -> VideoImportResult:
        metadata = read_video_metadata(path)
        return self.repository.add_video(
            metadata,
            title=title,
            tags=tags or [],
            batch_name=batch_name,
            note=note,
        )

    def scan_folder(
        self,
        folder: str | Path,
        *,
        recursive: bool = True,
        tags: list[str] | None = None,
        batch_name: str = "",
        note: str = "",
    ) -> list[VideoImportResult]:
        results: list[VideoImportResult] = []
        for path in iter_video_files(folder, recursive=recursive):
            results.append(
                self.import_file(
                    path,
                    tags=tags or [],
                    batch_name=batch_name,
                    note=note,
                )
            )
        return results
