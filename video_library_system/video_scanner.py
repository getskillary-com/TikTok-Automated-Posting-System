from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Iterable

from .models import SUPPORTED_VIDEO_EXTENSIONS, VideoMetadata


def is_supported_video(path: Path) -> bool:
    try:
        return path.is_file() and path.suffix.casefold() in SUPPORTED_VIDEO_EXTENSIONS
    except OSError:
        return False


def iter_video_files(folder: str | Path, *, recursive: bool = True) -> Iterable[Path]:
    root = Path(folder).expanduser()
    if not root.exists():
        raise ValueError(f"Video folder does not exist: {root}")
    if not root.is_dir():
        raise ValueError(f"Video scan path is not a folder: {root}")

    if recursive:
        for dirpath, _dirnames, filenames in os.walk(root, onerror=lambda _exc: None):
            for filename in filenames:
                path = Path(dirpath) / filename
                if is_supported_video(path):
                    yield path.resolve()
        return

    for path in root.iterdir():
        if is_supported_video(path):
            yield path.resolve()


def hash_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_video_metadata(path: str | Path) -> VideoMetadata:
    resolved = Path(path).expanduser().resolve()
    if not is_supported_video(resolved):
        raise ValueError(f"Unsupported or missing video file: {resolved}")
    duration, width, height = probe_video(resolved)
    return VideoMetadata(
        file_path=resolved,
        file_name=resolved.name,
        file_ext=resolved.suffix.casefold(),
        file_size=resolved.stat().st_size,
        sha256=hash_file(resolved),
        duration_seconds=duration,
        width=width,
        height=height,
    )


def probe_video(path: Path) -> tuple[float | None, int | None, int | None]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None, None, None
    command = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,duration",
        "-of",
        "json",
        str(path),
    ]
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None, None, None
    if result.returncode != 0:
        return None, None, None
    try:
        data = json.loads(result.stdout or "{}")
        stream = (data.get("streams") or [{}])[0]
    except (json.JSONDecodeError, IndexError, TypeError):
        return None, None, None
    duration = parse_float(stream.get("duration"))
    width = parse_int(stream.get("width"))
    height = parse_int(stream.get("height"))
    return duration, width, height


def parse_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def parse_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
