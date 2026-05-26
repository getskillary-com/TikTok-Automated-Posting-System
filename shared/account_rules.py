from __future__ import annotations

from typing import Any, Mapping


ACCOUNT_TYPES = {"marketing", "showcase"}
PRODUCT_FIELD_NAMES = (
    "product_id",
    "product_link",
    "product_name",
    "product_search_title",
    "product_publish_name",
)
SHOWCASE_PRODUCT_FIELD_NAMES = (
    "product_id",
    "product_link",
    "product_name",
    "product_search_title",
)


def normalize_account_type(value: str | None) -> str:
    text = str(value or "").strip().casefold()
    if text in {"showcase", "shop", "showcase_account", "window", "橱窗号", "chuchuang"}:
        return "showcase"
    if text in {"marketing", "market", "marketing_account", "营销号", "yingxiao"}:
        return "marketing"
    return text


def product_fields_from_mapping(data: Mapping[str, Any]) -> dict[str, str]:
    return {name: str(data.get(name) or "").strip() for name in PRODUCT_FIELD_NAMES}


def has_product_fields(product_fields: Mapping[str, Any]) -> bool:
    return any(str(product_fields.get(name) or "").strip() for name in PRODUCT_FIELD_NAMES)


def has_showcase_product_info(product_fields: Mapping[str, Any]) -> bool:
    return any(str(product_fields.get(name) or "").strip() for name in SHOWCASE_PRODUCT_FIELD_NAMES)


def validate_account_workflow(
    *,
    account_type: str,
    publish_mode: str,
    product_fields: Mapping[str, Any],
) -> str:
    clean_account_type = normalize_account_type(account_type)
    clean_publish_mode = str(publish_mode or "scheduled").strip() or "scheduled"
    if clean_account_type not in ACCOUNT_TYPES:
        return f"unsupported account_type: {clean_account_type or 'unknown'}"
    if clean_account_type == "showcase":
        if clean_publish_mode not in {"immediate", "scheduled"}:
            return "showcase only allows immediate or scheduled publish_mode"
        if not has_showcase_product_info(product_fields):
            return "showcase task requires product info"
        return ""
    if clean_account_type == "marketing" and clean_publish_mode != "scheduled":
        return "marketing only allows scheduled publish_mode"
    if has_product_fields(product_fields):
        return "marketing task cannot contain product fields"
    return ""


def require_account_workflow(
    *,
    account_type: str,
    publish_mode: str,
    product_fields: Mapping[str, Any],
) -> None:
    reason = validate_account_workflow(
        account_type=account_type,
        publish_mode=publish_mode,
        product_fields=product_fields,
    )
    if reason:
        raise ValueError(reason)
