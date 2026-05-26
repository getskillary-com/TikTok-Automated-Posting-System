from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any


DEFAULT_CONFIG: dict[str, Any] = {
    "adb": {
        "path": "adb",
        "serial": "",
        "app_package": "",
        "remote_video_dir": "/sdcard/DCIM/Camera",
    },
    "recognition": {
        "provider": "paddle_ocr",
        "google_api_key_env": "GOOGLE_CLOUD_VISION_API_KEY",
        "api_base": "https://vision.googleapis.com/v1",
        "feature_type": "TEXT_DETECTION",
        "language_hints": ["en", "zh"],
        "paddle": {
            "lang": "ch",
            "ocr_version": "PP-OCRv4",
            "device": "cpu",
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": False,
            "use_textline_orientation": False,
            "text_rec_score_thresh": 0.3,
            "min_confidence": 0.0,
        },
        "click_targets": [
            {
                "name": "upload_source",
                "texts": [
                    "Upload",
                    "Upload video",
                    "From device",
                    "Select",
                    "Choose",
                    "上传",
                    "上传视频",
                    "从设备",
                    "选择",
                ],
            },
            {
                "name": "next_continue",
                "texts": [
                    "Next",
                    "Continue",
                    "Done",
                    "OK",
                    "Confirm",
                    "下一步",
                    "继续",
                    "完成",
                    "确定",
                    "确认",
                ],
            },
            {
                "name": "create_entry",
                "texts": ["Create", "Add", "New post", "创建", "添加", "新建"],
            },
            {
                "name": "final_publish",
                "texts": ["Post", "Publish", "Share", "发布", "发帖", "立即发布"],
                "final_publish": True,
            },
        ],
        "wait_keywords": [
            "Uploading",
            "Processing",
            "Loading",
            "Please wait",
            "正在上传",
            "处理中",
            "加载中",
            "请稍候",
        ],
        "done_keywords": [
            "Uploaded",
            "Posted",
            "Published",
            "Upload complete",
            "Your video is posted",
            "已上传",
            "已发布",
            "发布成功",
            "上传完成",
        ],
        "fallback_targets": [
            {
                "name": "first_media_thumbnail",
                "screen_hints": [
                    "Recents",
                    "Gallery",
                    "Photos",
                    "Videos",
                    "All",
                    "相册",
                    "照片",
                    "视频",
                    "最近",
                    "全部",
                ],
                "x_ratio": 0.18,
                "y_ratio": 0.32,
                "confidence": 0.45,
            },
            {
                "name": "bottom_create_button",
                "screen_hints": [
                    "Home",
                    "Following",
                    "For You",
                    "Inbox",
                    "Profile",
                    "首页",
                    "关注",
                    "推荐",
                    "收件箱",
                    "我",
                ],
                "x_ratio": 0.5,
                "y_ratio": 0.94,
                "confidence": 0.35,
            },
        ],
    },
    "ai": {
        "provider": "openai",
        "model": "gpt-4.1-mini",
        "api_key_env": "OPENAI_API_KEY",
        "api_base": "https://api.openai.com/v1",
    },
    "automation": {
        "max_steps": 25,
        "tap_delay_seconds": 1.0,
        "screen_settle_seconds": 0.5,
        "screenshot_dir": "runs/screenshots",
        "xml_dir": "runs/xml",
        "trace_dir": "runs/traces",
        "dry_run": False,
        "final_publish_keywords": [
            "post",
            "publish",
            "share",
            "发布",
            "发帖",
            "立即发布",
        ],
    },
    "pipeline": {
        "state_dir": "runs/pipeline",
        "caption_file": "workflow_steps/caption_presets.json",
        "post_step_delay_seconds": 1.0,
        "launch": {
            "force_stop_before_open": True,
            "after_force_stop_seconds": 1.0,
        },
        "permissions": {
            "grant_media_permissions_before_open": True,
        },
        "upload": {
            "before_tap_delay_seconds": 0.5,
            "after_tap_delay_seconds": 0.8,
            "tap_attempts": 3,
            "verify_attempts": 2,
            "verify_wait_seconds": 1.0,
            "container_x_ratios": [0.5, 0.45, 0.55],
            "container_y_ratios": [0.5],
            "fallback_points": [
                {"label": "upload_button_center", "x_ratio": 0.5, "y_ratio": 0.226},
                {"label": "upload_button_icon", "x_ratio": 0.456, "y_ratio": 0.226},
                {"label": "upload_button_text", "x_ratio": 0.525, "y_ratio": 0.226},
            ],
        },
        "media_store": {
            "touch_after_push": True,
            "unique_remote_name": True,
            "wait_for_index": True,
            "index_timeout_seconds": 60,
            "index_poll_seconds": 1,
            "refresh_index_timestamps": True,
            "insert_on_missing": True,
            "after_insert_wait_seconds": 2.0,
            "post_insert_index_timeout_seconds": 30,
            "require_index_before_upload": True,
            "fail_on_index_timeout": False,
            "after_scan_wait_seconds": 3.0,
        },
        "schedule": {
            "timezone": "America/Sao_Paulo",
            "require_explicit_date": True,
            "max_future_days": 30,
            "min_lead_minutes": 0,
            "max_wait_seconds": 86400,
            "poll_seconds": 30,
            "keep_awake_keyevent": "224",
        },
        "targets": {
            "upload": [
                "Upload",
                "Upload video",
                "From device",
                "Select from device",
                "Choose from device",
                "上传",
                "上传视频",
                "从设备",
                "从相册选择",
                "选择视频",
            ],
            "next": [
                "Next",
                "Continue",
                "Done",
                "OK",
                "Confirm",
                "下一步",
                "继续",
                "完成",
                "确定",
                "确认",
            ],
            "caption_field": [
                "Add description",
                "Description",
                "Caption",
                "Write a caption",
                "Tell viewers about your video",
                "添加描述",
                "描述",
                "标题",
                "说点什么",
                "添加标题",
            ],
            "publish": [
                "Post",
                "Publish",
                "Share",
                "发布",
                "发帖",
                "立即发布",
            ],
        },
        "fallbacks": {
            "first_video": {"x_ratio": 0.29, "y_ratio": 0.165},
            "caption_field": {"x_ratio": 0.5, "y_ratio": 0.22},
        },
        "media_picker": {
            "wait_attempts": 6,
            "wait_seconds": 1.0,
            "thumbnail_min_size": 120,
            "selection_control_max_size": 180,
            "selection_x_ratio": 0.865,
            "selection_y_ratio": 0.135,
            "prefer_video_album": True,
            "preferred_album_terms": ["Videos", "Video", "\u89c6\u9891"],
            "album_dropdown_terms": [
                "All",
                "Recent",
                "Recents",
                "Videos",
                "Photos",
                "\u5168\u90e8",
                "\u6700\u8fd1",
                "\u89c6\u9891",
                "\u7167\u7247",
                "\u76f8\u518c",
            ],
            "album_dropdown_x_ratio": 0.5,
            "album_dropdown_y_ratio": 0.065,
            "after_album_open_seconds": 0.6,
            "after_album_select_seconds": 0.8,
            "refresh_by_reopen": False,
            "refresh_reopen_attempts": 1,
            "refresh_wait_attempts": 6,
            "refresh_wait_seconds": 0.7,
            "after_close_seconds": 0.8,
            "after_reopen_seconds": 1.5,
            "close_x_ratio": 0.065,
            "close_y_ratio": 0.055,
            "open_fallback": {"x_ratio": 0.29, "y_ratio": 0.165},
            "after_open_fallback_seconds": 1.0,
        },
        "paste": {
            "mode": "adb_keyboard",
            "paste_keyevent": "279",
            "before_paste_delay_seconds": 0.2,
            "after_paste_delay_seconds": 0.5,
            "commit_space_keyevent": "62",
            "hide_keyboard_after_paste": True,
            "fallback_to_input_text": False,
            "adb_keyboard": {
                "ime_id": "com.android.adbkeyboard/.AdbIME",
                "enable_ime": True,
                "restore_previous_ime": True,
                "input_action": "ADB_INPUT_TEXT",
                "after_ime_set_delay_seconds": 0.5,
                "after_input_delay_seconds": 0.5,
            },
        },
        "cleanup": {
            "wait_for_publish_complete": False,
            "initial_wait_seconds": 0,
            "minimum_wait_seconds": 0,
            "idle_confirmations": 2,
            "post_idle_wait_seconds": 5,
            "max_wait_seconds": 600,
            "poll_seconds": 5,
            "use_ocr_when_idle": True,
            "ocr_status_bar_ignore_top_ratio": 0.08,
            "progress_keywords": [
                "Uploading",
                "Processing",
                "Publishing",
                "Posting",
                "Waiting",
                "Saving",
                "Preparing",
                "Finalizing",
                "\u4e0a\u4f20\u4e2d",
                "\u6b63\u5728\u4e0a\u4f20",
                "\u5904\u7406\u4e2d",
                "\u6b63\u5728\u5904\u7406",
                "\u53d1\u5e03\u4e2d",
                "\u6b63\u5728\u53d1\u5e03",
                "\u6b63\u5728\u53d1\u5e03\u4f5c\u54c1",
                "\u4fdd\u5b58\u4e2d",
                "\u6b63\u5728\u4fdd\u5b58",
                "\u51c6\u5907\u4e2d",
                "\u6b63\u5728\u51c6\u5907",
                "\u7b49\u5f85\u4e2d",
                "\u68c0\u67e5\u4e2d",
                "\u6b63\u5728\u68c0\u67e5",
            ],
            "fail_on_timeout": True,
            "home_keyevent": "3",
            "after_home_delay_seconds": 1.0,
            "force_stop_app": False,
        },
        "gallery_cleanup": {
            "enabled": False,
            "scope": "remote_video_dir",
            "confirm_delete_all_gallery_videos": False,
            "require_completed_step": "minimize_and_cleanup",
            "delete_files": True,
            "delete_media_store_records": True,
            "include_filesystem_scan": True,
            "scan_after_delete": False,
            "media_store_uris": [
                "content://media/external_primary/video/media",
                "content://media/external/video/media",
            ],
            "filesystem_scan_roots": [
                "/sdcard/DCIM",
                "/sdcard/Movies",
                "/sdcard/Pictures",
            ],
            "video_extensions": [
                ".mp4",
                ".mov",
                ".m4v",
                ".webm",
                ".3gp",
                ".3gpp",
                ".mkv",
                ".avi",
            ],
            "max_delete_count": 0,
            "trace_paths_limit": 200,
        },
    },
    "workflow": {
        "objective": (
            "Upload the video that was just pushed to the phone, move through "
            "TikTok Studio/TikTok upload screens, and publish it only when the "
            "operator explicitly allows final publishing."
        ),
        "context": (
            "The UI may be English or Chinese. When a gallery/media picker is "
            "shown, choose the newest or most recent video, usually the first "
            "video thumbnail."
        ),
        "known_steps": [
            "Open the create, add, upload, or post entry point.",
            "Choose upload/from device/gallery if that option appears.",
            "Select the newest video from the phone gallery/media picker.",
            "Tap Next/Continue through editing and preview screens.",
            "Leave optional monetization, ads, effects, and privacy settings unchanged unless required.",
            "Tap the final Post/Publish button only when final publishing is allowed.",
        ],
    },
}


DEFAULT_CONFIG["pipeline"]["targets"].update(
    {
        "schedule_entry": [
            "Schedule",
            "Schedule video",
            "Schedule post",
            "Schedule publish",
            "预约发布作品",
            "预约发布",
            "定时发布",
            "计划发布",
        ],
        "schedule_enable": [
            "Schedule",
            "Schedule video",
            "Schedule post",
            "预约发布作品",
            "预约发布",
            "定时发布",
        ],
        "schedule_date_field": [
            "Date",
            "Publish date",
            "Schedule date",
            "日期",
            "发布日期",
            "预约日期",
        ],
        "schedule_time_field": [
            "Time",
            "Publish time",
            "Schedule time",
            "时间",
            "发布时间",
            "预约时间",
        ],
        "schedule_confirm": [
            "Done",
            "OK",
            "Confirm",
            "Save",
            "Apply",
            "完成",
            "确定",
            "确认",
            "保存",
        ],
        "schedule_publish": [
            "Schedule",
            "Schedule post",
            "Schedule video",
            "Post",
            "Publish",
            "预约发布",
            "定时发布",
            "发布",
            "立即发布",
        ],
    }
)
DEFAULT_CONFIG["pipeline"]["targets"].update(
    {
        "product_entry": [
            "Add link",
            "Add product",
            "Product",
            "Products",
            "TikTok Shop",
            "Showcase",
            "\u6dfb\u52a0\u5546\u54c1",
            "\u5546\u54c1",
            "\u5546\u54c1\u94fe\u63a5",
            "\u6dfb\u52a0\u94fe\u63a5",
            "\u6a71\u7a97",
        ],
        "product_search_field": [
            "Search",
            "Search product",
            "Product link",
            "Paste link",
            "Enter product link",
            "\u641c\u7d22",
            "\u641c\u7d22\u5546\u54c1",
            "\u5546\u54c1\u94fe\u63a5",
            "\u7c98\u8d34\u94fe\u63a5",
            "\u8f93\u5165\u5546\u54c1\u94fe\u63a5",
        ],
        "product_select": [
            "Add",
            "Select",
            "Choose",
            "Attach",
            "\u6dfb\u52a0",
            "\u9009\u62e9",
            "\u6302\u8f7d",
        ],
        "product_confirm": [
            "Done",
            "OK",
            "Confirm",
            "Save",
            "Apply",
            "\u5b8c\u6210",
            "\u786e\u5b9a",
            "\u786e\u8ba4",
            "\u4fdd\u5b58",
        ],
    }
)
DEFAULT_CONFIG["pipeline"]["fallbacks"]["schedule_entry"] = {"x_ratio": 0.5, "y_ratio": 0.54}
DEFAULT_CONFIG["pipeline"]["fallbacks"].update(
    {
        "product_entry": {"x_ratio": 0.5, "y_ratio": 0.64},
        "product_search_field": {"x_ratio": 0.5, "y_ratio": 0.16},
        "product_select": {"x_ratio": 0.82, "y_ratio": 0.32},
        "product_confirm": {"x_ratio": 0.82, "y_ratio": 0.94},
    }
)
DEFAULT_CONFIG["pipeline"]["product_linking"] = {
    "after_entry_delay_seconds": 2.0,
    "after_search_paste_delay_seconds": 2.0,
    "hide_keyboard_after_paste": True,
    "confirm_after_select": True,
}
DEFAULT_CONFIG["pipeline"]["native_schedule"] = {
    "strategy": "wheel",
    "configured_time_tolerance_minutes": 30,
    "wheel": {
        "date_x_ratio": 0.214,
        "hour_x_ratio": 0.503,
        "minute_x_ratio": 0.789,
        "increase_start_y_ratio": 0.774,
        "increase_end_y_ratio": 0.734,
        "duration_ms": 150,
        "step_pause_seconds": 0.25,
        "confirm_x_ratio": 0.66,
        "confirm_y_ratio": 0.942,
        "after_confirm_delay_seconds": 1.0,
        "verification_retries": 1,
    },
    "actions": [
        {"type": "tap_text", "target": "schedule_date_field"},
        {"type": "input_text", "value": "{date_day_first}"},
        {"type": "tap_text", "target": "schedule_confirm", "optional": True},
        {"type": "tap_text", "target": "schedule_time_field"},
        {"type": "input_text", "value": "{time_24}"},
        {"type": "tap_text", "target": "schedule_confirm", "optional": True},
    ],
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(path: str | Path | None) -> dict[str, Any]:
    config = deepcopy(DEFAULT_CONFIG)
    if not path:
        return config

    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with config_path.open("r", encoding="utf-8") as file:
        user_config = json.load(file)
    return _deep_merge(config, user_config)


def write_example_config(path: str | Path) -> None:
    config_path = Path(path)
    config_path.write_text(
        json.dumps(DEFAULT_CONFIG, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
