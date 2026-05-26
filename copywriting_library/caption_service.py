from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from .caption_repository import CaptionRepository, normalize_content
from .models import CaptionImportResult


class CaptionLibraryService:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.repository = CaptionRepository(db_path)

    def add_caption(
        self,
        content: str,
        *,
        tags: list[str] | None = None,
        platform: str = "",
        account_scope: str = "",
        note: str = "",
    ) -> CaptionImportResult:
        return self.repository.add_caption(
            content,
            tags=tags or [],
            platform=platform,
            account_scope=account_scope,
            note=note,
        )

    def import_file(
        self,
        path: str | Path,
        *,
        tags: list[str] | None = None,
        platform: str = "",
        account_scope: str = "",
        note: str = "",
    ) -> list[CaptionImportResult]:
        source = Path(path).expanduser().resolve()
        if not source.exists():
            raise FileNotFoundError(f"Caption file not found: {source}")
        suffix = source.suffix.casefold()
        if suffix == ".json":
            items = read_json_items(source)
        elif suffix == ".csv":
            items = read_csv_items(source)
        else:
            items = read_text_items(source)

        results: list[CaptionImportResult] = []
        for item in items:
            content = normalize_content(str(item.get("content", "")))
            if not content:
                continue
            item_tags = merge_tags(tags or [], parse_tags_value(item.get("tags", [])))
            results.append(
                self.repository.add_caption(
                    content,
                    tags=item_tags,
                    platform=str(item.get("platform") or platform),
                    account_scope=str(item.get("account_scope") or account_scope),
                    note=str(item.get("note") or note),
                )
            )
        return results


def read_text_items(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8-sig")
    blocks = [normalize_content(block) for block in text.split("\n\n")]
    return [{"content": block} for block in blocks if block]


def read_json_items(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(data, list):
        return [normalize_json_item(item) for item in data]
    if isinstance(data, dict):
        if isinstance(data.get("captions"), list):
            return [normalize_json_item(item) for item in data["captions"]]
        return [normalize_json_item(data)]
    raise ValueError("JSON caption file must be an object, a list, or contain a captions list.")


def normalize_json_item(item: Any) -> dict[str, Any]:
    if isinstance(item, str):
        return {"content": item}
    if isinstance(item, dict):
        return dict(item)
    raise ValueError("Caption JSON items must be strings or objects.")


def read_csv_items(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        if not reader.fieldnames or "content" not in reader.fieldnames:
            raise ValueError("CSV caption file must contain a content column.")
        return [dict(row) for row in reader]


def parse_tags_value(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return []


def merge_tags(base: list[str], extra: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for tag in [*base, *extra]:
        clean = str(tag).strip()
        if clean and clean not in seen:
            seen.add(clean)
            merged.append(clean)
    return merged
