from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from upload_system.adb import ADB, ADBError, UINode, parse_bounds, summarize_ui_xml
from upload_system.ai import (
    GoogleOCRButtonFinder,
    PaddleOCRButtonFinder,
    TextCandidate,
    _disabled_ui_texts,
    _matches_any,
    _parse_google_ocr_candidates,
    _ui_node_candidates,
    png_size,
)
from upload_system.config import load_config


def add_step_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default="config.json", help="Path to config JSON.")
    parser.add_argument("--state", default="", help="Path to pipeline state JSON.")
    parser.add_argument("--video", default="", help="Local video path, used when state is not available.")
    parser.add_argument("--dry-run", action="store_true", help="Print actions without tapping/typing.")
    parser.add_argument("--no-dry-run", action="store_true", help="Execute even if state/config dry_run is true.")
    parser.add_argument("--allow-publish", action="store_true", help="Allow final publish tap.")
    parser.add_argument("--publish-mode", default="", choices=["", "immediate", "scheduled", "timed"], help="Publish mode.")
    parser.add_argument("--account-type", default="", choices=["", "marketing", "showcase"], help="Account workflow: marketing or showcase.")
    parser.add_argument("--product-link", default="", help="Product link for showcase account workflow.")
    parser.add_argument("--product-name", default="", help="Product name or search keyword for showcase account workflow.")
    parser.add_argument("--schedule-time", default="", help="Target publish time, for example 08:00.")
    parser.add_argument("--schedule-date", default="", help="Target publish date in YYYY-MM-DD. Defaults to next valid day.")
    parser.add_argument("--schedule-timezone", default="", help="IANA timezone, for example America/Sao_Paulo.")
    parser.add_argument("--scheduled-at", default="", help="Full scheduled datetime in ISO format.")


def load_step_config(config_path: str) -> dict[str, Any]:
    load_env_file(PROJECT_ROOT / ".env")
    path = Path(config_path)
    return load_config(path if path.exists() else None)


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def load_or_create_state(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    if args.state:
        state_path = Path(args.state)
        if state_path.exists():
            with state_path.open("r", encoding="utf-8") as file:
                state = json.load(file)
        else:
            state = _new_state(config, args.video)
            state["state_path"] = str(state_path)
            state_path.parent.mkdir(parents=True, exist_ok=True)
            save_state(state)
    else:
        state = _new_state(config, args.video)
        save_state(state)

    video_arg = getattr(args, "video", "")
    if video_arg:
        state["video_path"] = str(Path(video_arg))
    state["config_path"] = args.config
    if args.dry_run:
        state["dry_run"] = True
    if args.no_dry_run:
        state["dry_run"] = False
    if args.allow_publish:
        state["allow_publish"] = True
    for key in (
        "publish_mode",
        "account_type",
        "product_link",
        "product_name",
        "product_search_title",
        "product_publish_name",
        "schedule_time",
        "schedule_date",
        "schedule_timezone",
        "scheduled_at",
    ):
        value = getattr(args, key, "")
        if value:
            state[key] = value
    save_state(state)
    return state


def _new_state(config: dict[str, Any], video_path: str = "") -> dict[str, Any]:
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    state_dir = Path(config["pipeline"]["state_dir"]) / run_id
    schedule_config = config["pipeline"].get("schedule", {})
    return {
        "run_id": run_id,
        "run_dir": str(state_dir),
        "state_path": str(state_dir / "state.json"),
        "video_path": str(Path(video_path)) if video_path else "",
        "remote_video_path": "",
        "caption": "",
        "caption_preset": "",
        "caption_file": config["pipeline"]["caption_file"],
        "account_type": "marketing",
        "product_link": "",
        "product_name": "",
        "product_search_title": "",
        "product_publish_name": "",
        "dry_run": bool(config["automation"].get("dry_run", False)),
        "allow_publish": False,
        "publish_mode": "immediate",
        "schedule_time": "",
        "schedule_date": "",
        "schedule_timezone": str(schedule_config.get("timezone", "America/Sao_Paulo")),
        "scheduled_at": "",
        "completed_steps": [],
    }


def save_state(state: dict[str, Any]) -> None:
    state_path = Path(state["state_path"])
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def mark_step_done(state: dict[str, Any], step_name: str, **updates: Any) -> None:
    completed = state.setdefault("completed_steps", [])
    if step_name not in completed:
        completed.append(step_name)
    state.update(updates)
    save_state(state)


def make_adb(config: dict[str, Any]) -> ADB:
    adb_config = config["adb"]
    return ADB(adb_path=adb_config.get("path", "adb"), serial=adb_config.get("serial", ""))


def resolve_package(adb: ADB, config: dict[str, Any]) -> str:
    configured = config["adb"].get("app_package", "").strip()
    if configured:
        return configured
    packages = adb.detect_packages()
    studio_packages = [
        package
        for package in packages
        if "studio" in package.lower() or "creator" in package.lower()
    ]
    if len(studio_packages) == 1:
        return studio_packages[0]
    if len(packages) == 1:
        return packages[0]
    if packages:
        raise ADBError(
            "Multiple possible packages detected. Set adb.app_package in config.json:\n"
            + "\n".join(f"  - {package}" for package in packages)
        )
    raise ADBError("No TikTok Studio package configured or detected. Run doctor and set adb.app_package.")


def require_video_path(state: dict[str, Any]) -> Path:
    video_path = state.get("video_path", "")
    if not video_path:
        raise ValueError("Missing video path. Pass --video or run through run_pipeline.py.")
    path = Path(video_path)
    if not path.exists():
        raise FileNotFoundError(f"Video not found: {path}")
    return path


def capture_screen(
    *,
    adb: ADB,
    state: dict[str, Any],
    step_name: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    adb.wait(float(config["automation"]["screen_settle_seconds"]))
    run_dir = Path(state["run_dir"])
    screenshot_dir = run_dir / "screenshots"
    xml_dir = run_dir / "xml"
    screenshot_path = adb.screenshot(screenshot_dir / f"{step_name}.png")
    xml_text = adb.dump_ui_xml()
    xml_dir.mkdir(parents=True, exist_ok=True)
    (xml_dir / f"{step_name}.xml").write_text(xml_text, encoding="utf-8")
    nodes = summarize_ui_xml(xml_text)
    return {
        "screenshot_path": screenshot_path,
        "xml_text": xml_text,
        "nodes": nodes,
        "screen_size": png_size(screenshot_path),
    }


def collect_text_candidates(
    *,
    screenshot_path: Path,
    nodes: list[UINode],
    config: dict[str, Any],
) -> tuple[list[TextCandidate], str]:
    ui_candidates = _ui_node_candidates(nodes)
    ocr_candidates: list[TextCandidate] = []
    provider = str(config.get("recognition", {}).get("provider", "paddle_ocr")).lower()
    if provider in {"google_ocr", "google", "google_vision", "cloud_vision"}:
        finder = GoogleOCRButtonFinder(config)
        data = finder._detect_text(screenshot_path)
        disabled = _disabled_ui_texts(nodes)
        ocr_candidates = [
            candidate
            for candidate in _parse_google_ocr_candidates(data)
            if not _matches_any(candidate.text, disabled)
        ]
    elif provider in {"paddle_ocr", "paddle", "paddleocr", "paddle_vision"}:
        finder = PaddleOCRButtonFinder(config)
        disabled = _disabled_ui_texts(nodes)
        ocr_candidates = [
            candidate
            for candidate in finder._detect_candidates(screenshot_path)
            if not _matches_any(candidate.text, disabled)
        ]
    candidates = ocr_candidates + ui_candidates
    visible_text = "\n".join(candidate.text for candidate in candidates)
    return candidates, visible_text


def find_candidate(candidates: list[TextCandidate], texts: list[str]) -> tuple[TextCandidate, str] | None:
    matches: list[tuple[float, TextCandidate, str]] = []
    for candidate in candidates:
        candidate_text = normalize_text(candidate.text)
        for text in texts:
            target = normalize_text(str(text))
            if target and target in candidate_text:
                score = candidate.confidence
                if candidate_text == target:
                    score += 0.3
                if candidate.source == "ui":
                    score += 0.15
                score += min(len(target), 20) / 100
                matches.append((score, candidate, str(text)))
    if not matches:
        return None
    matches.sort(key=lambda item: item[0], reverse=True)
    return matches[0][1], matches[0][2]


def tap_text_target(
    *,
    adb: ADB,
    state: dict[str, Any],
    config: dict[str, Any],
    step_name: str,
    texts: list[str],
    dry_run: bool | None = None,
    allow_fallback: dict[str, float] | None = None,
    final_publish: bool = False,
) -> dict[str, Any]:
    screen = capture_screen(adb=adb, state=state, step_name=step_name, config=config)
    ui_candidates = _ui_node_candidates(screen["nodes"])
    visible_text = "\n".join(candidate.text for candidate in ui_candidates)
    candidates = ui_candidates
    match = find_candidate(candidates, texts)
    if not match:
        candidates, visible_text = collect_text_candidates(
            screenshot_path=screen["screenshot_path"],
            nodes=screen["nodes"],
            config=config,
        )
        match = find_candidate(candidates, texts)
    if match:
        candidate, matched_text = match
        decision = {
            "action": "tap",
            "x": candidate.x,
            "y": candidate.y,
            "label": candidate.text,
            "matched_text": matched_text,
            "confidence": candidate.confidence,
            "source": candidate.source,
            "is_final_publish": final_publish,
            "recognized_text": visible_text,
            "reason": f"Matched step target '{matched_text}'.",
        }
    elif allow_fallback:
        width, height = screen["screen_size"]
        decision = {
            "action": "tap",
            "x": int(width * float(allow_fallback["x_ratio"])),
            "y": int(height * float(allow_fallback["y_ratio"])),
            "label": "fallback",
            "matched_text": "",
            "confidence": 0.35,
            "source": "fallback",
            "is_final_publish": final_publish,
            "recognized_text": visible_text,
            "reason": "Used configured fallback coordinate for this step.",
        }
    else:
        decision = {
            "action": "manual_review",
            "x": None,
            "y": None,
            "label": "",
            "matched_text": "",
            "confidence": 0.0,
            "source": "",
            "is_final_publish": final_publish,
            "recognized_text": visible_text,
            "reason": "No configured OCR/UI target matched this step.",
        }

    write_trace(state, step_name, decision)
    if decision["action"] != "tap":
        raise RuntimeError(f"{step_name}: {decision['reason']}")
    effective_dry_run = state.get("dry_run", False) if dry_run is None else dry_run
    if final_publish and not state.get("allow_publish", False) and not effective_dry_run:
        raise RuntimeError(f"{step_name}: final publish is blocked. Re-run with --allow-publish.")

    print_decision(step_name, decision, effective_dry_run)
    if not effective_dry_run:
        adb.tap(int(decision["x"]), int(decision["y"]))
    adb.wait(float(config["pipeline"]["post_step_delay_seconds"]))
    return decision


def tap_ratio(
    *,
    adb: ADB,
    state: dict[str, Any],
    config: dict[str, Any],
    step_name: str,
    x_ratio: float,
    y_ratio: float,
    label: str,
) -> dict[str, Any]:
    screen = capture_screen(adb=adb, state=state, step_name=step_name, config=config)
    width, height = screen["screen_size"]
    decision = {
        "action": "tap",
        "x": int(width * x_ratio),
        "y": int(height * y_ratio),
        "label": label,
        "matched_text": "",
        "confidence": 0.35,
        "source": "ratio",
        "is_final_publish": False,
        "reason": f"Tapped configured ratio coordinate for {label}.",
    }
    write_trace(state, step_name, decision)
    print_decision(step_name, decision, bool(state.get("dry_run", False)))
    if not state.get("dry_run", False):
        adb.tap(int(decision["x"]), int(decision["y"]))
    adb.wait(float(config["pipeline"]["post_step_delay_seconds"]))
    return decision


def write_trace(state: dict[str, Any], step_name: str, decision: dict[str, Any]) -> None:
    trace_dir = Path(state["run_dir"]) / "traces"
    trace_dir.mkdir(parents=True, exist_ok=True)
    (trace_dir / f"{step_name}.json").write_text(
        json.dumps(decision, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def print_decision(step_name: str, decision: dict[str, Any], dry_run: bool) -> None:
    suffix = " [dry-run]" if dry_run else ""
    print(
        f"{step_name}: tap ({decision['x']}, {decision['y']}) "
        f"{decision['label']!r} via {decision['source']}{suffix}"
    )


def load_caption(
    *,
    state: dict[str, Any],
    video_path: Path,
    caption_file: str | Path,
    preset: str = "",
    direct_caption: str = "",
) -> str:
    if direct_caption:
        return direct_caption
    path = Path(caption_file)
    if not path.exists():
        raise FileNotFoundError(f"Caption preset file not found: {path}")
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    preset_key = preset or state.get("caption_preset", "")
    if preset_key:
        presets = data.get("presets", {})
        if preset_key not in presets:
            raise KeyError(f"Caption preset not found: {preset_key}")
        return str(presets[preset_key])

    videos = data.get("videos", {})
    for key in (video_path.name, video_path.stem, str(video_path)):
        if key in videos:
            return str(videos[key])
    if "default" in data:
        return str(data["default"])
    raise KeyError(f"No caption preset matched video: {video_path.name}")


def paste_text(adb: ADB, text: str, config: dict[str, Any], dry_run: bool) -> None:
    if dry_run:
        print(f"paste_text [dry-run]: {text[:80]!r}")
        return
    paste_config = config["pipeline"]["paste"]
    mode = paste_config.get("mode", "adb_keyboard")
    if mode == "adb_keyboard":
        try:
            input_text_with_adb_keyboard(adb, text, paste_config)
        except ADBError as exc:
            if not paste_config.get("fallback_to_input_text", False):
                raise
            print(f"ADB keyboard unavailable, falling back to input text: {exc}")
            adb.shell("input", "text", escape_adb_input_text(text), timeout=30)
        return
    if mode == "clipboard_keyevent":
        try:
            set_clipboard_text(adb, text, timeout=15)
            adb.wait(float(paste_config.get("before_paste_delay_seconds", 0.2)))
            adb.shell("input", "keyevent", str(paste_config.get("paste_keyevent", "279")), timeout=15)
            adb.wait(float(paste_config.get("after_paste_delay_seconds", 0.5)))
            return
        except ADBError as exc:
            if not paste_config.get("fallback_to_input_text", False):
                raise ADBError(
                    "Clipboard paste failed and keyboard fallback is disabled. "
                    "Set pipeline.paste.fallback_to_input_text=true if you want to allow ADB keyboard typing."
                ) from exc
            print(f"Clipboard paste failed, falling back to input text: {exc}")
    if mode == "input_text":
        adb.shell("input", "text", escape_adb_input_text(text), timeout=30)
        commit_keyevent = paste_config.get("commit_space_keyevent", "")
        if commit_keyevent:
            adb.shell("input", "keyevent", str(commit_keyevent), timeout=15)
        return
    adb.shell("input", "text", escape_adb_input_text(text), timeout=30)


def input_text_with_adb_keyboard(adb: ADB, text: str, paste_config: dict[str, Any]) -> None:
    keyboard_config = paste_config.get("adb_keyboard", {})
    ime_id = keyboard_config.get("ime_id", "com.android.adbkeyboard/.AdbIME")
    installed_imes = adb.shell("ime", "list", "-s", check=False, timeout=15)
    if ime_id not in installed_imes.splitlines():
        raise ADBError(
            f"ADB keyboard IME is not installed/enabled: {ime_id}\n"
            "Install ADB Keyboard on the phone, then re-run this step. "
            "Current input methods:\n"
            f"{installed_imes.strip()}"
        )

    previous_ime = adb.shell(
        "settings",
        "get",
        "secure",
        "default_input_method",
        check=False,
        timeout=15,
    ).strip()

    try:
        if keyboard_config.get("enable_ime", True):
            adb.shell("ime", "enable", ime_id, check=False, timeout=15)
        adb.shell("ime", "set", ime_id, timeout=15)
        adb.wait(float(keyboard_config.get("after_ime_set_delay_seconds", 0.5)))
        action = keyboard_config.get("input_action", "ADB_INPUT_TEXT")
        command = "am broadcast -a " + shlex.quote(action) + " --es msg " + shlex.quote(text)
        adb.run(["shell", command], timeout=30)
        adb.wait(float(keyboard_config.get("after_input_delay_seconds", 0.5)))
    finally:
        if (
            keyboard_config.get("restore_previous_ime", True)
            and previous_ime
            and previous_ime != ime_id
        ):
            adb.shell("ime", "set", previous_ime, check=False, timeout=15)


def set_clipboard_text(adb: ADB, text: str, timeout: int = 15) -> None:
    command = "cmd clipboard set text " + shlex.quote(text)
    adb.run(["shell", command], timeout=timeout)


def escape_adb_input_text(text: str) -> str:
    escaped = text.replace("%", "%25").replace(" ", "%s")
    return re.sub(r"([&<>|;*()$`\"'\\])", r"\\\1", escaped)


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", text).casefold()


def read_phone_clock(adb: ADB, timezone_name: str) -> dict[str, Any]:
    epoch_text = adb.shell("date", "+%s", timeout=15).strip()
    try:
        epoch_seconds = int(epoch_text.splitlines()[-1].strip())
    except (IndexError, ValueError) as exc:
        raise RuntimeError(f"Cannot read phone clock epoch from adb date output: {epoch_text!r}") from exc

    phone_timezone = adb.shell("getprop", "persist.sys.timezone", check=False, timeout=15).strip()
    effective_timezone = phone_timezone or timezone_name
    try:
        timezone_info = load_timezone(effective_timezone)
    except Exception:
        effective_timezone = timezone_name
        timezone_info = load_timezone(effective_timezone)
    phone_now = datetime.fromtimestamp(epoch_seconds, timezone_info)
    return {
        "source": "adb_date",
        "epoch_seconds": epoch_seconds,
        "timezone": effective_timezone,
        "phone_timezone_raw": phone_timezone,
        "fallback_timezone": timezone_name,
        "iso": phone_now.isoformat(),
        "date_iso": phone_now.date().isoformat(),
        "time_24": phone_now.strftime("%H:%M"),
        "hour": phone_now.hour,
        "minute": phone_now.minute,
        "second": phone_now.second,
    }


def infer_native_schedule_initial_from_phone_clock(
    phone_clock: dict[str, Any],
    wheel_config: dict[str, Any],
) -> dict[str, Any]:
    phone_now = datetime.fromisoformat(str(phone_clock["iso"]))
    offset_minutes = int(wheel_config.get("initial_offset_minutes", 30))
    rounding = str(wheel_config.get("initial_rounding", "ceil_minute"))
    initial = phone_now + timedelta(minutes=offset_minutes)
    if rounding == "ceil_minute" and (initial.second or initial.microsecond):
        initial = initial + timedelta(minutes=1)
    if rounding in {"floor_minute", "ceil_minute"}:
        initial = initial.replace(second=0, microsecond=0)

    return {
        "source": "phone_clock",
        "date_iso": initial.date().isoformat(),
        "hour": initial.hour,
        "minute": initial.minute,
        "time_24": initial.strftime("%H:%M"),
        "display": f"phone_clock+{offset_minutes}m:{initial.strftime('%Y-%m-%d %H:%M')}",
        "phone_now_iso": phone_clock["iso"],
        "phone_timezone": phone_clock["timezone"],
        "initial_offset_minutes": offset_minutes,
        "initial_rounding": rounding,
        "linear_no_wrap": bool(wheel_config.get("linear_no_wrap", True)),
    }


def parse_schedule_iso_datetime(value: str, timezone_info: Any) -> datetime:
    text = str(value or "").strip().replace("Z", "+00:00")
    match = re.fullmatch(r"(.+T\d{2}:\d{2}:\d{2})\.(\d{6})\d+([+-]\d{2}:\d{2})?", text)
    if match:
        suffix = match.group(3) or ""
        text = f"{match.group(1)}.{match.group(2)}{suffix}"
    target = datetime.fromisoformat(text)
    if target.tzinfo is None:
        target = target.replace(tzinfo=timezone_info)
    return target


def resolve_publish_schedule(state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    schedule_config = config["pipeline"].get("schedule", {})
    timezone_name = state.get("schedule_timezone") or schedule_config.get("timezone", "America/Sao_Paulo")
    timezone_info = load_timezone(str(timezone_name))
    publish_mode = str(state.get("publish_mode", "immediate") or "immediate")
    require_explicit_date = bool(schedule_config.get("require_explicit_date", True))
    max_future_days = int(schedule_config.get("max_future_days", 30))
    min_lead_minutes = int(schedule_config.get("min_lead_minutes", 0))
    now = datetime.now(timezone_info)

    scheduled_at_text = str(state.get("scheduled_at", "")).strip()
    if scheduled_at_text:
        target = parse_schedule_iso_datetime(scheduled_at_text, timezone_info)
        target = target.astimezone(timezone_info)
    else:
        schedule_time = str(state.get("schedule_time", "")).strip()
        if not schedule_time:
            raise ValueError("Missing schedule time. Pass --schedule-time HH:MM or --scheduled-at ISO_DATETIME.")
        hour, minute = parse_schedule_time(schedule_time)
        schedule_date = str(state.get("schedule_date", "")).strip()
        if publish_mode == "scheduled" and require_explicit_date and not schedule_date:
            raise ValueError(
                "Missing schedule date. TikTok Studio native scheduling needs both "
                "--schedule-date YYYY-MM-DD and --schedule-time HH:MM."
            )
        if schedule_date:
            date_part = datetime.strptime(schedule_date, "%Y-%m-%d").date()
        else:
            date_part = now.date()
        target = datetime(
            date_part.year,
            date_part.month,
            date_part.day,
            hour,
            minute,
            tzinfo=timezone_info,
        )
        if not schedule_date and target <= now + timedelta(minutes=min_lead_minutes):
            target = target + timedelta(days=1)

    auto_extension: dict[str, Any] | None = None
    if publish_mode in {"scheduled", "timed"}:
        target, auto_extension = extend_target_if_within_lead_window(
            target=target,
            now=now,
            threshold_minutes=int(schedule_config.get("auto_extend_if_within_minutes", 0) or 0),
            extension_minutes=int(schedule_config.get("auto_extend_by_minutes", 0) or 0),
            source="scheduler_clock",
        )

    earliest_target = now + timedelta(minutes=min_lead_minutes)
    if target <= earliest_target:
        raise ValueError(
            f"Scheduled publish time must be in the future. "
            f"Now is {now.strftime('%Y-%m-%d %H:%M')} {timezone_name}; "
            f"target is {target.strftime('%Y-%m-%d %H:%M')}."
        )

    max_date = now.date() + timedelta(days=max_future_days)
    if target.date() > max_date:
        raise ValueError(
            f"TikTok Studio only allows scheduling within the next {max_future_days} days. "
            f"Latest allowed date is {max_date.isoformat()} {timezone_name}; "
            f"target date is {target.date().isoformat()}."
        )

    schedule = {
        "timezone": str(timezone_name),
        "target": target,
        "target_iso": target.isoformat(),
        "date_iso": target.strftime("%Y-%m-%d"),
        "date_slash": target.strftime("%m/%d/%Y"),
        "date_day_first": target.strftime("%d/%m/%Y"),
        "time_24": target.strftime("%H:%M"),
        "time_12": target.strftime("%I:%M %p").lstrip("0"),
        "seconds_until": max(0.0, (target - now).total_seconds()),
        "max_future_days": max_future_days,
        "latest_allowed_date": max_date.isoformat(),
        "now_iso": now.isoformat(),
    }
    if auto_extension:
        schedule["auto_extension"] = auto_extension
        schedule["auto_extensions"] = [auto_extension]
    return schedule


def apply_schedule_lead_window(
    schedule: dict[str, Any],
    *,
    now: datetime,
    schedule_config: dict[str, Any],
    source: str,
) -> dict[str, Any]:
    target = schedule["target"]
    adjusted_target, auto_extension = extend_target_if_within_lead_window(
        target=target,
        now=now,
        threshold_minutes=int(schedule_config.get("auto_extend_if_within_minutes", 0) or 0),
        extension_minutes=int(schedule_config.get("auto_extend_by_minutes", 0) or 0),
        source=source,
    )
    if not auto_extension:
        return schedule

    updated = dict(schedule)
    updated.update(
        {
            "target": adjusted_target,
            "target_iso": adjusted_target.isoformat(),
            "date_iso": adjusted_target.strftime("%Y-%m-%d"),
            "date_slash": adjusted_target.strftime("%m/%d/%Y"),
            "date_day_first": adjusted_target.strftime("%d/%m/%Y"),
            "time_24": adjusted_target.strftime("%H:%M"),
            "time_12": adjusted_target.strftime("%I:%M %p").lstrip("0"),
            "seconds_until": max(0.0, (adjusted_target - now.astimezone(adjusted_target.tzinfo)).total_seconds()),
            "now_iso": now.astimezone(adjusted_target.tzinfo).isoformat(),
            "auto_extension": auto_extension,
        }
    )
    previous = list(schedule.get("auto_extensions", []))
    previous.append(auto_extension)
    updated["auto_extensions"] = previous
    return updated


def extend_target_if_within_lead_window(
    *,
    target: datetime,
    now: datetime,
    threshold_minutes: int,
    extension_minutes: int,
    source: str,
) -> tuple[datetime, dict[str, Any] | None]:
    if threshold_minutes <= 0 or extension_minutes <= 0:
        return target, None
    reference_now = now.astimezone(target.tzinfo) if now.tzinfo else now.replace(tzinfo=target.tzinfo)
    seconds_until = (target - reference_now).total_seconds()
    if seconds_until > threshold_minutes * 60:
        return target, None

    adjusted = target + timedelta(minutes=extension_minutes)
    return adjusted, {
        "source": source,
        "reason": "target_within_lead_window",
        "threshold_minutes": threshold_minutes,
        "extension_minutes": extension_minutes,
        "original_target_iso": target.isoformat(),
        "adjusted_target_iso": adjusted.isoformat(),
        "reference_now_iso": reference_now.isoformat(),
        "seconds_until_before": seconds_until,
        "seconds_until_after": (adjusted - reference_now).total_seconds(),
    }


def parse_native_schedule_display(text: str, timezone_name: str) -> dict[str, Any] | None:
    timezone_info = load_timezone(timezone_name)
    now = datetime.now(timezone_info)
    patterns = [
        r"(?P<today>今天)\s*[,，]?\s*(?P<hour>\d{1,2}):(?P<minute>\d{2})",
        r"(?P<tomorrow>明天)\s*[,，]?\s*(?P<hour>\d{1,2}):(?P<minute>\d{2})",
        r"(?P<month>\d{1,2})\s*月\s*(?P<day>\d{1,2})\s*日\s*[,，]?\s*(?P<hour>\d{1,2}):(?P<minute>\d{2})",
        r"(?P<today>今天)\s*[,，]?\s*(?P<hour>\d{1,2}):(?P<minute>\d{2})",
        r"(?P<month>\d{1,2})月(?P<day>\d{1,2})日\s*[,，]?\s*(?P<hour>\d{1,2}):(?P<minute>\d{2})",
    ]
    matches: list[dict[str, Any]] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            if match.groupdict().get("today"):
                date_part = now.date()
            elif match.groupdict().get("tomorrow"):
                date_part = (now + timedelta(days=1)).date()
            else:
                month = int(match.group("month"))
                day = int(match.group("day"))
                date_part = datetime(now.year, month, day, tzinfo=timezone_info).date()
                if date_part < now.date():
                    date_part = datetime(now.year + 1, month, day, tzinfo=timezone_info).date()
            hour = int(match.group("hour"))
            minute = int(match.group("minute"))
            matches.append({
                "date_iso": date_part.isoformat(),
                "hour": hour,
                "minute": minute,
                "time_24": f"{hour:02d}:{minute:02d}",
                "display": match.group(0),
            })
    return matches[-1] if matches else None


def parse_native_schedule_screen(
    screen: dict[str, Any],
    config: dict[str, Any],
    timezone_name: str,
) -> dict[str, Any] | None:
    visible_text = "\n".join(
        part
        for node in screen["nodes"]
        for part in (node.text, node.description)
        if part
    )
    parsed = parse_native_schedule_display(visible_text, timezone_name)
    if parsed:
        return parsed

    try:
        candidates, ocr_text = collect_text_candidates(
            screenshot_path=screen["screenshot_path"],
            nodes=screen["nodes"],
            config=config,
        )
    except Exception:
        return None

    parsed = parse_native_schedule_display(ocr_text, timezone_name)
    if parsed:
        return parsed
    return parse_native_schedule_wheel(candidates, screen["nodes"], timezone_name)


def visible_text_from_nodes(nodes: list[UINode]) -> str:
    return "\n".join(
        part
        for node in nodes
        for part in (node.text, node.description)
        if part
    )


def publish_form_signal(screen: dict[str, Any]) -> dict[str, Any]:
    width, height = screen["screen_size"]
    has_caption_editor = any(
        "EditText" in node.class_name
        and node.center_y is not None
        and node.center_y < int(height * 0.45)
        for node in screen["nodes"]
    )
    has_bottom_action_button = any(
        "Button" in node.class_name
        and node.center_x is not None
        and node.center_y is not None
        and node.center_x >= int(width * 0.45)
        and node.center_y >= int(height * 0.88)
        for node in screen["nodes"]
    )
    return {
        "has_caption_editor": has_caption_editor,
        "has_bottom_action_button": has_bottom_action_button,
        "looks_like_publish_form": has_caption_editor and has_bottom_action_button,
    }


def native_schedule_picker_signal(screen: dict[str, Any]) -> dict[str, Any]:
    seekbar_bounds = [
        bounds
        for node in screen["nodes"]
        if "SeekBar" in node.class_name
        for bounds in [parse_bounds(node.bounds)]
        if bounds
    ]
    seekbar_bounds.sort(key=lambda bounds: (bounds[0] + bounds[2]) // 2)
    return {
        "is_open": len(seekbar_bounds) >= 3,
        "seekbar_count": len(seekbar_bounds),
        "seekbar_bounds": [
            {"left": left, "top": top, "right": right, "bottom": bottom}
            for left, top, right, bottom in seekbar_bounds[:6]
        ],
    }


def scheduled_publish_page_signal(
    screen: dict[str, Any],
    *,
    target_date_iso: str = "",
    target_time_24: str = "",
    target_timezone: str = "",
    time_tolerance_minutes: int = 0,
) -> dict[str, Any]:
    width, height = screen["screen_size"]
    visible_text = visible_text_from_nodes(screen["nodes"])
    normalized_all = normalize_text(visible_text)
    bottom_text = "\n".join(
        part
        for node in screen["nodes"]
        if node.center_y is not None and node.center_y >= int(height * 0.82)
        for part in (node.text, node.description)
        if part
    )
    normalized_bottom = normalize_text(bottom_text)
    scheduled_terms = ["schedule", "\u9884\u7ea6\u53d1\u5e03", "\u5b9a\u65f6\u53d1\u5e03"]
    direct_terms = ["post", "publish", "\u53d1\u5e03", "\u7acb\u5373\u53d1\u5e03"]
    has_scheduled_final_button = any(term in normalized_bottom for term in scheduled_terms)
    has_direct_publish_button = (
        any(term in normalized_bottom for term in direct_terms)
        and not has_scheduled_final_button
    )
    target_tokens = schedule_target_tokens(target_date_iso, target_time_24)
    date_tokens = target_tokens["date_tokens"]
    time_tokens = target_tokens["time_tokens"]
    has_exact_target_date = not date_tokens or any(normalize_text(token) in normalized_all for token in date_tokens)
    has_exact_target_time = not time_tokens or any(normalize_text(token) in normalized_all for token in time_tokens)
    tolerance_match = schedule_time_tolerance_match(
        visible_text=visible_text,
        target_date_iso=target_date_iso,
        target_time_24=target_time_24,
        timezone_name=target_timezone,
        tolerance_minutes=time_tolerance_minutes,
    )
    has_target_date = has_exact_target_date or bool(tolerance_match.get("date_matches"))
    has_target_time = has_exact_target_time or bool(tolerance_match.get("time_within_tolerance"))
    has_target_text = bool(date_tokens or time_tokens) and has_target_date and has_target_time
    return {
        "has_scheduled_final_button": has_scheduled_final_button,
        "has_direct_publish_button": has_direct_publish_button,
        "has_target_text": has_target_text,
        "has_target_date": has_target_date,
        "has_target_time": has_target_time,
        "has_exact_target_date": has_exact_target_date,
        "has_exact_target_time": has_exact_target_time,
        "time_tolerance": tolerance_match,
        "target_tokens": target_tokens,
        "bottom_text": bottom_text,
        "visible_text": visible_text,
        "screen_width": width,
        "screen_height": height,
    }


def schedule_time_tolerance_match(
    *,
    visible_text: str,
    target_date_iso: str,
    target_time_24: str,
    timezone_name: str,
    tolerance_minutes: int,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "enabled": int(tolerance_minutes) > 0,
        "tolerance_minutes": max(0, int(tolerance_minutes)),
        "matched": False,
    }
    if not result["enabled"] or not target_date_iso or not target_time_24:
        return result
    timezone_info = load_timezone(timezone_name or "UTC")
    try:
        hour, minute = parse_schedule_time(target_time_24)
        target = datetime.combine(
            date.fromisoformat(target_date_iso),
            datetime.min.time(),
            tzinfo=timezone_info,
        ).replace(hour=hour, minute=minute)
    except ValueError as exc:
        result["error"] = str(exc)
        return result

    actual = parse_native_schedule_display(visible_text, timezone_name or "UTC")
    if not actual:
        result["reason"] = "no_schedule_datetime_detected"
        return result
    try:
        actual_dt = datetime.combine(
            date.fromisoformat(str(actual["date_iso"])),
            datetime.min.time(),
            tzinfo=timezone_info,
        ).replace(hour=int(actual["hour"]), minute=int(actual["minute"]))
    except (KeyError, TypeError, ValueError) as exc:
        result["actual"] = actual
        result["error"] = str(exc)
        return result

    diff_minutes = abs((actual_dt - target).total_seconds()) / 60.0
    date_matches = actual_dt.date() == target.date()
    time_within_tolerance = date_matches and diff_minutes <= float(result["tolerance_minutes"])
    result.update(
        {
            "matched": time_within_tolerance,
            "date_matches": date_matches,
            "time_within_tolerance": time_within_tolerance,
            "diff_minutes": diff_minutes,
            "actual": {
                "date_iso": actual_dt.date().isoformat(),
                "time_24": actual_dt.strftime("%H:%M"),
                "display": actual.get("display", ""),
            },
            "target": {
                "date_iso": target.date().isoformat(),
                "time_24": target.strftime("%H:%M"),
            },
        }
    )
    return result


def schedule_target_tokens(target_date_iso: str, target_time_24: str) -> dict[str, list[str]]:
    date_tokens: list[str] = []
    time_tokens: list[str] = []
    text_date = str(target_date_iso or "").strip()
    text_time = str(target_time_24 or "").strip()
    if text_date:
        date_tokens.append(text_date)
        try:
            parsed = datetime.fromisoformat(text_date)
            date_tokens.append(f"{parsed.month}\u6708{parsed.day}\u65e5")
        except ValueError:
            pass
    if text_time:
        time_tokens.append(text_time)
        match = re.fullmatch(r"0?(\d{1,2}):(\d{2})", text_time)
        if match:
            time_tokens.append(f"{int(match.group(1))}:{match.group(2)}")
    return {
        "date_tokens": unique_nonempty(date_tokens),
        "time_tokens": unique_nonempty(time_tokens),
    }


def unique_nonempty(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def parse_native_schedule_wheel(
    candidates: list[TextCandidate],
    nodes: list[UINode],
    timezone_name: str,
) -> dict[str, Any] | None:
    wheel_bounds = [
        bounds
        for node in nodes
        if "SeekBar" in node.class_name
        for bounds in [parse_bounds(node.bounds)]
        if bounds
    ]
    if len(wheel_bounds) < 3:
        return None
    wheel_bounds.sort(key=lambda bounds: (bounds[0] + bounds[2]) // 2)
    date_bounds, hour_bounds, minute_bounds = wheel_bounds[:3]
    center_y = (date_bounds[1] + date_bounds[3]) // 2

    hour = selected_wheel_number(candidates, hour_bounds, center_y)
    minute = selected_wheel_number(candidates, minute_bounds, center_y)
    date_part = selected_wheel_date(candidates, date_bounds, center_y, timezone_name)
    if date_part is None or hour is None or minute is None:
        return None

    return {
        "date_iso": date_part.isoformat(),
        "hour": hour,
        "minute": minute,
        "time_24": f"{hour:02d}:{minute:02d}",
        "display": f"wheel:{date_part.isoformat()} {hour:02d}:{minute:02d}",
    }


def selected_wheel_number(
    candidates: list[TextCandidate],
    bounds: tuple[int, int, int, int],
    center_y: int,
) -> int | None:
    left, top, right, bottom = bounds
    numeric: list[tuple[int, int]] = []
    for candidate in candidates:
        if candidate.source != "ocr":
            continue
        if not (left - 30 <= candidate.x <= right + 30 and top <= candidate.y <= bottom):
            continue
        value = normalize_digits(candidate.text)
        if not re.fullmatch(r"\d{1,2}", value):
            continue
        numeric.append((abs(candidate.y - center_y), int(value)))
    if not numeric:
        return None
    numeric.sort(key=lambda item: item[0])
    return numeric[0][1]


def selected_wheel_date(
    candidates: list[TextCandidate],
    bounds: tuple[int, int, int, int],
    center_y: int,
    timezone_name: str,
) -> Any:
    left, top, right, bottom = bounds
    items = [
        candidate
        for candidate in candidates
        if candidate.source == "ocr"
        and left - 60 <= candidate.x <= right + 60
        and top <= candidate.y <= bottom
    ]
    rows = grouped_ocr_rows(items)
    if not rows:
        return None

    timezone_info = load_timezone(timezone_name)
    now = datetime.now(timezone_info)
    parsed_rows: list[dict[str, Any]] = []
    for row in rows:
        text = "".join(candidate.text for candidate in sorted(row, key=lambda item: item.x))
        parsed = parse_wheel_date_text(text, now)
        if parsed:
            parsed_rows.append({"y": sum(item.y for item in row) / len(row), "date": parsed, "text": text})

    if not parsed_rows:
        return None

    row_ys = [sum(item.y for item in row) / len(row) for row in rows]
    step = estimate_row_step(row_ys)
    absolute_rows = [row for row in parsed_rows if row["date"] not in ("today", "tomorrow")]
    inferred: list[tuple[float, Any]] = []
    for row in absolute_rows:
        offset = round((float(row["y"]) - center_y) / step)
        inferred.append((abs(float(row["y"]) - center_y), row["date"] - timedelta(days=offset)))
    if inferred:
        inferred.sort(key=lambda item: item[0])
        return inferred[0][1]

    nearest = min(parsed_rows, key=lambda row: abs(float(row["y"]) - center_y))
    if nearest["date"] == "today":
        return now.date()
    if nearest["date"] == "tomorrow":
        return (now + timedelta(days=1)).date()
    return nearest["date"]


def grouped_ocr_rows(candidates: list[TextCandidate]) -> list[list[TextCandidate]]:
    rows: list[list[TextCandidate]] = []
    for candidate in sorted(candidates, key=lambda item: item.y):
        for row in rows:
            row_y = sum(item.y for item in row) / len(row)
            if abs(row_y - candidate.y) <= 35:
                row.append(candidate)
                break
        else:
            rows.append([candidate])
    return rows


def estimate_row_step(row_ys: list[float]) -> float:
    ordered = sorted(row_ys)
    diffs = [b - a for a, b in zip(ordered, ordered[1:]) if b - a > 20]
    if not diffs:
        return 100.0
    diffs.sort()
    return diffs[len(diffs) // 2]


def parse_wheel_date_text(text: str, now: datetime) -> Any:
    compact = normalize_digits(normalize_text(text))
    if "今天" in compact or "today" in compact:
        return "today"
    if "明天" in compact or "tomorrow" in compact:
        return "tomorrow"
    match = re.search(r"(?P<month>\d{1,2})月(?P<day>\d{1,2})日?", compact)
    if not match:
        return None
    month = int(match.group("month"))
    day = int(match.group("day"))
    result = datetime(now.year, month, day, tzinfo=now.tzinfo).date()
    if result < now.date() - timedelta(days=180):
        result = datetime(now.year + 1, month, day, tzinfo=now.tzinfo).date()
    return result


def normalize_digits(text: str) -> str:
    return text.translate(str.maketrans("０１２３４５６７８９", "0123456789"))


def parse_schedule_time(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"\s*(\d{1,2})(?::(\d{2}))?\s*", value)
    if not match:
        raise ValueError(f"Invalid schedule time: {value!r}. Use HH:MM, for example 08:00.")
    hour = int(match.group(1))
    minute = int(match.group(2) or "0")
    if hour > 23 or minute > 59:
        raise ValueError(f"Invalid schedule time: {value!r}. Use 00:00 through 23:59.")
    return hour, minute


def load_timezone(timezone_name: str) -> timezone:
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        fixed_offsets = {
            "UTC": 0,
            "America/Sao_Paulo": -3,
            "Brazil/East": -3,
            "BRT": -3,
            "Asia/Shanghai": 8,
            "China/Shanghai": 8,
            "CST": 8,
        }
        if timezone_name in fixed_offsets:
            return timezone(timedelta(hours=fixed_offsets[timezone_name]), timezone_name)
        match = re.fullmatch(r"UTC([+-])(\d{1,2})(?::?(\d{2}))?", timezone_name)
        if match:
            sign = 1 if match.group(1) == "+" else -1
            hours = int(match.group(2))
            minutes = int(match.group(3) or "0")
            return timezone(sign * timedelta(hours=hours, minutes=minutes), timezone_name)
        raise


def wait_until_datetime(
    *,
    target: datetime,
    timezone_name: str,
    poll_seconds: float,
    dry_run: bool,
    adb: ADB | None = None,
    keep_awake_keyevent: str = "",
) -> None:
    if dry_run:
        print(f"wait_until_datetime [dry-run]: would wait until {target.isoformat()} ({timezone_name})")
        return

    timezone_info = load_timezone(timezone_name)
    while True:
        now = datetime.now(timezone_info)
        remaining = (target - now).total_seconds()
        if remaining <= 0:
            return
        sleep_for = min(float(poll_seconds), remaining)
        if keep_awake_keyevent and adb is not None:
            adb.shell("input", "keyevent", str(keep_awake_keyevent), check=False, timeout=15)
        print(f"Waiting {int(remaining)}s until scheduled publish time {target.strftime('%Y-%m-%d %H:%M')} {timezone_name}")
        time.sleep(sleep_for)


