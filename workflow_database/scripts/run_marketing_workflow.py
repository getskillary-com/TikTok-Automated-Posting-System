from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = Path(__file__).resolve().parents[2]
UPLOAD_ROOT = PROJECT_ROOT / "upload_system"
STEP_DIR = UPLOAD_ROOT / "workflow_steps"
RUN_ROOT = PROJECT_ROOT / "workflow_database" / "runs"
DEFAULT_PROFILE = PROJECT_ROOT / "workflow_database" / "devices" / "samsung_SM-A5260" / "marketing_tiktok_studio.json"
DEFAULT_CONFIG = UPLOAD_ROOT / "config.json"
DEFAULT_ADB = PROJECT_ROOT / "platform-tools" / "adb.exe"
ANDROID_HOME = PROJECT_ROOT / "storage" / "android_home"
DEFAULT_ADB_SERVER_PORT = "5037"

for import_path in (PROJECT_ROOT, SCRIPT_DIR, STEP_DIR):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

from common import load_or_create_state, load_step_config, make_adb, mark_step_done, tap_ratio, write_trace  # noqa: E402
from device_preflight import DevicePreflightOptions, require_device_ready  # noqa: E402
from shared.windows_process import no_window_subprocess_kwargs  # noqa: E402


STEP_SCRIPTS = {
    "push_video": "push_video.py",
    "open_target_app": "open_target_app.py",
    "tap_upload": "tap_upload.py",
    "refresh_media_picker": "refresh_media_picker.py",
    "select_first_video": "select_first_video.py",
    "tap_next_after_select": "tap_next_after_select.py",
    "tap_next_after_edit": "tap_next_after_edit.py",
    "paste_caption": "paste_caption.py",
    "open_schedule_settings": "open_schedule_settings.py",
    "wait_schedule_picker_open": "wait_schedule_picker_open.py",
    "set_schedule_datetime": "set_schedule_datetime.py",
    "verify_schedule_configured": "verify_schedule_configured.py",
    "tap_scheduled_publish": "tap_scheduled_publish.py",
}

DEFAULT_SCHEDULED_WORKFLOW = [
    {"id": "push_video"},
    {"id": "open_target_app"},
    {"id": "tap_studio_create", "type": "tap_point", "point": "studio_create_entry"},
    {"id": "tap_upload"},
    {"id": "refresh_media_picker"},
    {"id": "select_first_video"},
    {"id": "tap_next_after_select"},
    {"id": "tap_next_after_edit"},
    {"id": "paste_caption"},
    {"id": "open_schedule_settings"},
    {"id": "wait_schedule_picker_open"},
    {"id": "set_schedule_datetime"},
    {"id": "verify_schedule_configured"},
    {"id": "tap_scheduled_publish"},
    {"id": "minimize_tiktok_studio"},
]

SCREEN_GUARD_STEPS = {
    "tap_studio_create",
    "tap_upload",
    "select_first_video",
    "tap_next_after_select",
    "tap_next_after_edit",
    "paste_caption",
    "open_schedule_settings",
    "wait_schedule_picker_open",
    "set_schedule_datetime",
    "verify_schedule_configured",
    "tap_scheduled_publish",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the TikTok Studio marketing scheduled publish workflow.")
    parser.add_argument("video", nargs="?", default="", help="Local video path.")
    parser.add_argument("--profile", default=str(DEFAULT_PROFILE), help="Workflow profile JSON path.")
    parser.add_argument("--config", default="", help="Step config JSON path. Defaults to a run-local profile override.")
    parser.add_argument("--state", default="", help="Pipeline state JSON path.")
    parser.add_argument("--caption", default="", help="Caption text for the publish screen.")
    parser.add_argument("--publish-mode", choices=["scheduled"], default="scheduled", help="Marketing workflow only supports native scheduled publish.")
    parser.add_argument("--schedule-timezone", default="", help="IANA timezone for scheduled_at.")
    parser.add_argument("--scheduled-at", default="", help="Target scheduled datetime in ISO format.")
    parser.add_argument("--adb", default=str(DEFAULT_ADB), help="ADB executable path.")
    parser.add_argument("--serial", default="", help="ADB serial. Defaults to the profile known device.")
    parser.add_argument("--dry-run", action="store_true", help="Run without taps/text entry.")
    parser.add_argument("--no-dry-run", action="store_true", help="Execute even if config dry_run is true.")
    parser.add_argument("--allow-publish", action="store_true", help="Allow the final scheduled publish tap.")
    parser.add_argument("--from-step", default="", help="Step id to start from.")
    parser.add_argument("--to-step", default="", help="Step id to stop after.")
    parser.add_argument("--run-id", default="", help="Optional run id for artifacts when --state is omitted.")
    parser.add_argument("--save-profile-coordinates", action="store_true", help="Record verified tap coordinates back to the source profile.")
    parser.add_argument("--calibration-events", default="", help="Optional JSONL path for calibration events.")
    parser.add_argument("--force-stop-before-open", action="store_true", help="Force-stop TikTok Studio before the open_target_app step.")
    parser.add_argument("--allow-input-text-fallback", action="store_true", help="Fall back to adb shell input text when ADB Keyboard is missing.")
    parser.add_argument("--skip-device-preflight", action="store_true", help="Skip startup wake/readiness preflight.")
    parser.add_argument("--skip-wake", action="store_true", help="Fail instead of waking a sleeping or locked phone.")
    parser.add_argument("--skip-screen-guard", action="store_true", help="Skip lightweight wake/keyguard guard before interactive steps.")
    args = parser.parse_args()

    profile_path = Path(args.profile).resolve()
    profile = read_json(profile_path)
    validate_profile(profile, profile_path)
    if not args.scheduled_at:
        parser.error("--scheduled-at is required for the marketing scheduled workflow.")

    run_dir = resolve_run_dir(args)
    run_dir.mkdir(parents=True, exist_ok=True)
    state_path = Path(args.state).resolve() if args.state else run_dir / "state.json"
    config_path = write_run_config(args, profile, run_dir)
    write_initial_state(args=args, profile=profile, config_path=config_path, state_path=state_path, run_dir=run_dir)
    write_json(run_dir / "profile_snapshot.json", profile)
    write_json(
        run_dir / "context.json",
        {
            "run_dir": str(run_dir),
            "state_path": str(state_path),
            "config_path": str(config_path),
            "profile_path": str(profile_path),
            "video_path": str(Path(args.video).resolve()) if args.video else "",
            "caption": args.caption,
            "publish_mode": args.publish_mode,
            "scheduled_at": args.scheduled_at,
            "schedule_timezone": args.schedule_timezone or default_timezone(profile),
            "allow_publish": bool(args.allow_publish),
            "dry_run": bool(args.dry_run),
            "save_profile_coordinates": bool(args.save_profile_coordinates),
            "calibration_events": str(Path(args.calibration_events).resolve()) if args.calibration_events else "",
            "force_stop_before_open": bool(args.force_stop_before_open),
            "allow_input_text_fallback": bool(args.allow_input_text_fallback),
            "skip_device_preflight": bool(args.skip_device_preflight),
            "skip_wake": bool(args.skip_wake),
            "skip_screen_guard": bool(args.skip_screen_guard),
        },
    )

    if not args.skip_device_preflight:
        preflight = run_device_preflight(
            args=args,
            profile=profile,
            run_dir=run_dir,
            phase="startup",
            startup=not bool(args.from_step),
            check_media_dir=True,
            check_input_injection=True,
            require_ready=True,
        )
        print(f"device_preflight: ready serial={preflight['serial']} phase=startup screen={preflight.get('screen', '')}")

    recorder = build_calibration_recorder(args=args, profile=profile, profile_path=profile_path)
    steps = slice_steps(profile.get("workflow") or DEFAULT_SCHEDULED_WORKFLOW, args.from_step, args.to_step)
    for step in steps:
        step_id = str(step.get("id") or "")
        if should_guard_screen(args, step_id):
            guard = run_device_preflight(
                args=args,
                profile=profile,
                run_dir=run_dir,
                phase=f"guard_{step_id}",
                startup=False,
                check_media_dir=False,
                check_input_injection=False,
                require_ready=True,
            )
            print(f"device_guard: ready serial={guard['serial']} step={step_id}")
        if step_id in STEP_SCRIPTS:
            result = run_step_script(
                step_id=step_id,
                script_name=STEP_SCRIPTS[step_id],
                args=args,
                config_path=config_path,
                state_path=state_path,
            )
            if recorder:
                profile = recorder.record_step_result(
                    profile=profile,
                    state_path=state_path,
                    step=step,
                    success=result == 0,
                )
            if result != 0:
                return result
            wait_after_profile_step(step)
        elif str(step.get("type") or "") in {"tap_point", "tap_profile_point"}:
            try:
                tap_profile_point(args=args, profile=profile, config_path=config_path, state_path=state_path, step=step)
            except Exception as exc:  # noqa: BLE001
                print(f"{step_id}: {exc}", file=sys.stderr)
                if recorder:
                    profile = recorder.record_step_result(
                        profile=profile,
                        state_path=state_path,
                        step=step,
                        success=False,
                    )
                return 1
            if recorder:
                profile = recorder.record_step_result(
                    profile=profile,
                    state_path=state_path,
                    step=step,
                    success=True,
                )
        elif step_id == "minimize_tiktok_studio":
            try:
                minimize_tiktok_studio(args=args, profile=profile, config_path=config_path, state_path=state_path, step=step)
            except Exception as exc:  # noqa: BLE001
                print(f"{step_id}: {exc}", file=sys.stderr)
                if recorder:
                    profile = recorder.record_step_result(
                        profile=profile,
                        state_path=state_path,
                        step=step,
                        success=False,
                    )
                return 1
            if recorder:
                profile = recorder.record_step_result(
                    profile=profile,
                    state_path=state_path,
                    step=step,
                    success=True,
                )
        else:
            raise RuntimeError(f"Unsupported marketing workflow step: {step_id}")

    print("Marketing scheduled workflow completed.")
    print(f"Artifacts: {run_dir}")
    return 0


def run_step_script(
    *,
    step_id: str,
    script_name: str,
    args: argparse.Namespace,
    config_path: Path,
    state_path: Path,
) -> int:
    script_path = STEP_DIR / script_name
    command = [
        sys.executable,
        "-c",
        step_script_wrapper(script_path),
        "--config",
        str(config_path),
        "--state",
        str(state_path),
        "--publish-mode",
        "scheduled",
        "--account-type",
        "marketing",
        "--scheduled-at",
        args.scheduled_at,
        "--schedule-timezone",
        args.schedule_timezone,
    ]
    if args.video:
        command.extend(["--video", args.video])
    if args.allow_publish:
        command.append("--allow-publish")
    if args.dry_run:
        command.append("--dry-run")
    if args.no_dry_run:
        command.append("--no-dry-run")
    if step_id == "paste_caption" and args.caption:
        command.extend(["--caption", args.caption])

    print(f"\n=== {step_id} ===")
    return subprocess.run(
        command,
        cwd=str(UPLOAD_ROOT),
        env=workflow_env(),
        check=False,
        **no_window_subprocess_kwargs(),
    ).returncode


def step_script_wrapper(script_path: Path) -> str:
    return (
        "import runpy, sys; "
        f"sys.path.insert(0, {str(STEP_DIR)!r}); "
        f"sys.path.insert(0, {str(UPLOAD_ROOT)!r}); "
        f"sys.argv[0] = {str(script_path)!r}; "
        f"runpy.run_path({str(script_path)!r}, run_name='__main__')"
    )


def wait_after_profile_step(step: dict[str, Any]) -> None:
    wait_seconds = float(step.get("wait_seconds", 0.0) or 0.0)
    if wait_seconds > 0:
        time.sleep(wait_seconds)


def should_guard_screen(args: argparse.Namespace, step_id: str) -> bool:
    if args.skip_screen_guard:
        return False
    return step_id in SCREEN_GUARD_STEPS


def run_device_preflight(
    *,
    args: argparse.Namespace,
    profile: dict[str, Any],
    run_dir: Path,
    phase: str,
    startup: bool,
    check_media_dir: bool,
    check_input_injection: bool,
    require_ready: bool,
) -> dict[str, Any]:
    config = read_json(run_dir / "upload_config.json")
    serial = args.serial or str(profile.get("device", {}).get("known_adb_serial") or "")
    screen = profile.get("screen", {}) if isinstance(profile.get("screen"), dict) else {}
    options = DevicePreflightOptions(
        serial=serial,
        adb_path=Path(args.adb).resolve(),
        run_dir=run_dir,
        phase=phase,
        app_package=tiktok_studio_package(profile, config),
        remote_video_dir=str(profile.get("inputs", {}).get("remote_video_dir") or "/sdcard/DCIM/Camera"),
        expected_width=int(screen.get("width") or 0),
        expected_height=int(screen.get("height") or 0),
        skip_wake=bool(args.skip_wake),
        startup=startup,
        check_media_dir=check_media_dir,
        check_input_injection=check_input_injection,
        check_screen_size=True,
        check_package=True,
        require_ready=require_ready,
    )
    return require_device_ready(options)


def tap_profile_point(
    *,
    args: argparse.Namespace,
    profile: dict[str, Any],
    config_path: Path,
    state_path: Path,
    step: dict[str, Any],
) -> None:
    step_args = state_args(args, config_path, state_path)
    config = load_step_config(str(config_path))
    state = load_or_create_state(step_args, config)
    adb = make_adb(config)
    step_id = str(step.get("id") or "")
    point_name = str(step.get("point") or "")
    point = profile_point(profile, point_name)

    print(f"\n=== {step_id} ===")
    decision = tap_ratio(
        adb=adb,
        state=state,
        config=config,
        step_name=step_id,
        x_ratio=float(point["x_ratio"]),
        y_ratio=float(point["y_ratio"]),
        label=point_name,
    )
    wait_seconds = float(step.get("wait_seconds", 0.0) or 0.0)
    if wait_seconds:
        adb.wait(wait_seconds)
    mark_step_done(state, step_id, last_decision=decision)


def minimize_tiktok_studio(
    *,
    args: argparse.Namespace,
    profile: dict[str, Any],
    config_path: Path,
    state_path: Path,
    step: dict[str, Any],
) -> None:
    step_args = state_args(args, config_path, state_path)
    config = load_step_config(str(config_path))
    state = load_or_create_state(step_args, config)
    adb = make_adb(config)
    package = tiktok_studio_package(profile, config)
    dry_run = bool(state.get("dry_run", False))
    keyevent = str(step.get("home_keyevent") or "3")
    step_id = str(step.get("id") or "minimize_tiktok_studio")

    decision = {
        "action": "minimize_app",
        "package": package,
        "keyevent": keyevent,
        "dry_run": dry_run,
    }
    print(f"\n=== {step_id} ===")
    print(f"{step_id}: sending HOME keyevent {keyevent}")
    if not dry_run:
        adb.shell("input", "keyevent", keyevent, check=False, timeout=15)
    wait_seconds = float(step.get("wait_seconds", 1.0) or 0.0)
    if wait_seconds:
        adb.wait(wait_seconds)
    write_trace(state, step_id, decision)
    mark_step_done(state, step_id, last_decision=decision)


def state_args(args: argparse.Namespace, config_path: Path, state_path: Path) -> argparse.Namespace:
    return argparse.Namespace(
        config=str(config_path),
        state=str(state_path),
        video=args.video,
        dry_run=args.dry_run,
        no_dry_run=args.no_dry_run,
        allow_publish=args.allow_publish,
        publish_mode="scheduled",
        account_type="marketing",
        product_link="",
        product_name="",
        schedule_time="",
        schedule_date="",
        schedule_timezone=args.schedule_timezone,
        scheduled_at=args.scheduled_at,
    )


def resolve_run_dir(args: argparse.Namespace) -> Path:
    if args.state:
        return Path(args.state).resolve().parent
    return RUN_ROOT / (args.run_id or datetime.now().strftime("marketing-%Y%m%d-%H%M%S"))


def write_initial_state(
    *,
    args: argparse.Namespace,
    profile: dict[str, Any],
    config_path: Path,
    state_path: Path,
    run_dir: Path,
) -> None:
    if state_path.exists():
        return
    config = read_json(config_path)
    schedule_config = config.get("pipeline", {}).get("schedule", {})
    state = {
        "run_id": args.run_id or run_dir.name,
        "run_dir": str(run_dir),
        "state_path": str(state_path),
        "config_path": str(config_path),
        "video_path": str(Path(args.video)) if args.video else "",
        "remote_video_path": "",
        "caption": args.caption,
        "caption_preset": "",
        "caption_file": config.get("pipeline", {}).get("caption_file", "workflow_steps/caption_presets.json"),
        "account_type": "marketing",
        "product_link": "",
        "product_name": "",
        "product_search_title": "",
        "product_publish_name": "",
        "dry_run": bool(args.dry_run and not args.no_dry_run),
        "allow_publish": bool(args.allow_publish),
        "publish_mode": "scheduled",
        "schedule_time": "",
        "schedule_date": "",
        "schedule_timezone": args.schedule_timezone or str(schedule_config.get("timezone") or default_timezone(profile)),
        "scheduled_at": args.scheduled_at,
        "completed_steps": [],
    }
    write_json(state_path, state)


def build_calibration_recorder(
    *,
    args: argparse.Namespace,
    profile: dict[str, Any],
    profile_path: Path,
) -> Any | None:
    if not args.save_profile_coordinates and not args.calibration_events:
        return None
    from marketing_calibration import CalibrationRecorder

    device = profile.get("device", {}) if isinstance(profile.get("device"), dict) else {}
    event_path = Path(args.calibration_events).resolve() if args.calibration_events else None
    return CalibrationRecorder(
        profile_path=profile_path,
        save_profile_coordinates=bool(args.save_profile_coordinates),
        event_path=event_path,
        serial=args.serial or str(device.get("known_adb_serial") or ""),
        model=str(device.get("model") or ""),
    )


def write_run_config(args: argparse.Namespace, profile: dict[str, Any], run_dir: Path) -> Path:
    path = run_dir / "upload_config.json"
    base_config = {}
    if args.config:
        configured_path = Path(args.config).resolve()
        if configured_path.exists():
            base_config = read_json(configured_path)
    serial = args.serial or str(profile.get("device", {}).get("known_adb_serial") or "")
    profile_config = {
        "recognition": {
            "provider": "none",
        },
        "adb": {
            "path": str(Path(args.adb).resolve()),
            "serial": serial,
            "app_package": tiktok_studio_package(profile, {}),
            "remote_video_dir": str(profile.get("inputs", {}).get("remote_video_dir") or "/sdcard/DCIM/Camera"),
        },
        "pipeline": {
            "state_dir": str(run_dir),
            "launch": {
                "force_stop_before_open": bool(args.force_stop_before_open),
                "after_force_stop_seconds": 1.0,
            },
            "schedule": {
                "timezone": args.schedule_timezone or default_timezone(profile),
                "auto_extend_if_within_minutes": 40,
                "auto_extend_by_minutes": 30,
            },
            "media_store": {
                "pre_push_cleanup": {
                    "enabled": True,
                    "scope": "remote_video_dir",
                    "display_name_patterns": [
                        "20??????_??????_*.mp4",
                        "20??????-??????_*.mp4",
                        "gcs_*.mp4"
                    ],
                    "include_filesystem_scan": True,
                    "delete_files": True,
                    "delete_media_store_records": True,
                    "scan_after_delete": True,
                    "max_delete_count": 200
                },
                "require_top_after_push": True,
                "top_timeout_seconds": 45,
                "top_poll_seconds": 1,
                "top_query_limit": 10,
            },
            "media_picker": {
                "refresh_by_reopen": False,
                "refresh_reopen_attempts": 1,
                "after_close_seconds": 1.0,
                "after_reopen_seconds": 2.5,
                "refresh_wait_attempts": 8,
                "refresh_wait_seconds": 1.0,
            },
            "native_schedule": {
                "strategy": "phone_clock_wheel",
                "wheel": {
                    "initial_source": "phone_clock",
                    "initial_offset_minutes": 30,
                    "initial_rounding": "ceil_minute",
                    "clock_boundary_guard_seconds": 5,
                    "linear_no_wrap": True,
                    "verify_after_confirm": False,
                    "verification_retries": 0,
                },
                "picker_open_wait_attempts": 8,
                "picker_open_wait_seconds": 0.75,
                "configured_wait_attempts": 6,
                "configured_wait_seconds": 0.75,
                "configured_time_tolerance_minutes": 30,
                "require_target_text_after_confirm": True,
            },
            "gallery_cleanup": {
                "enabled": False,
            },
            "paste": {
                "fallback_to_input_text": bool(args.allow_input_text_fallback),
            },
        },
    }
    config = deep_merge(base_config, profile_config)
    apply_profile_points(config, profile)
    write_json(path, config)
    return path


def apply_profile_points(config: dict[str, Any], profile: dict[str, Any]) -> None:
    points = profile.get("coordinate_points", {})
    pipeline = config.setdefault("pipeline", {})
    fallbacks = pipeline.setdefault("fallbacks", {})

    if all(name in points for name in ("upload_button_center", "upload_button_icon", "upload_button_text")):
        pipeline.setdefault("upload", {})["fallback_points"] = [
            ratio_fallback("upload_button_center", points["upload_button_center"]),
            ratio_fallback("upload_button_icon", points["upload_button_icon"]),
            ratio_fallback("upload_button_text", points["upload_button_text"]),
        ]
    if "caption_field" in points:
        fallbacks["caption_field"] = point_ratio(points["caption_field"])
    if "schedule_entry" in points:
        schedule_entry = points["schedule_entry"]
        fallbacks["schedule_entry"] = point_ratio(schedule_entry)
        if str(schedule_entry.get("tap_preference") or "").lower() in {"prefer_point", "force_point"}:
            pipeline.setdefault("native_schedule", {})["prefer_schedule_entry_fallback"] = True
    if "schedule_publish_button" in points:
        fallbacks["schedule_publish"] = point_ratio(points["schedule_publish_button"])
    if "next_after_select" in points:
        fallbacks["next_after_select"] = point_ratio(points["next_after_select"])
    elif "next_bottom_right" in points:
        fallbacks["next_after_select"] = point_ratio(points["next_bottom_right"])
    if "next_after_edit" in points:
        fallbacks["next_after_edit"] = point_ratio(points["next_after_edit"])
    elif "next_bottom_right" in points:
        fallbacks["next_after_edit"] = point_ratio(points["next_bottom_right"])

    media_picker = pipeline.setdefault("media_picker", {})
    if "media_picker_album_dropdown" in points:
        media_picker["album_dropdown_x_ratio"] = float(points["media_picker_album_dropdown"]["x_ratio"])
        media_picker["album_dropdown_y_ratio"] = float(points["media_picker_album_dropdown"]["y_ratio"])
    if "media_picker_selection_control" in points:
        media_picker["selection_x_ratio"] = float(points["media_picker_selection_control"]["x_ratio"])
        media_picker["selection_y_ratio"] = float(points["media_picker_selection_control"]["y_ratio"])
    if "media_picker_first_video" in points:
        media_picker["open_fallback"] = point_ratio(points["media_picker_first_video"])
        fallbacks["first_video"] = point_ratio(points["media_picker_first_video"])

    wheel = pipeline.setdefault("native_schedule", {}).setdefault("wheel", {})
    mapping = {
        "native_schedule_date_column": ("date_x_ratio", None),
        "native_schedule_hour_column": ("hour_x_ratio", None),
        "native_schedule_minute_column": ("minute_x_ratio", None),
        "native_schedule_increase_start": (None, "increase_start_y_ratio"),
        "native_schedule_increase_end": (None, "increase_end_y_ratio"),
        "schedule_confirm": ("confirm_x_ratio", "confirm_y_ratio"),
    }
    for point_name, (x_key, y_key) in mapping.items():
        if point_name not in points:
            continue
        point = points[point_name]
        if x_key:
            wheel[x_key] = float(point["x_ratio"])
        if y_key:
            wheel[y_key] = float(point["y_ratio"])


def ratio_fallback(label: str, point: dict[str, Any]) -> dict[str, Any]:
    return {"label": label, **point_ratio(point)}


def point_ratio(point: dict[str, Any]) -> dict[str, float]:
    return {
        "x_ratio": float(point["x_ratio"]),
        "y_ratio": float(point["y_ratio"]),
    }


def profile_point(profile: dict[str, Any], point_name: str) -> dict[str, Any]:
    try:
        point = profile["coordinate_points"][point_name]
    except KeyError as exc:
        raise RuntimeError(f"Profile point is not configured: {point_name}") from exc
    return dict(point)


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def tiktok_studio_package(profile: dict[str, Any], config: dict[str, Any]) -> str:
    configured = ""
    if config:
        configured = str(config.get("adb", {}).get("app_package") or "")
    return configured or str(profile.get("apps", {}).get("tiktok_studio", {}).get("package") or "com.ss.android.tt.creator")


def default_timezone(profile: dict[str, Any]) -> str:
    return str(profile.get("inputs", {}).get("schedule_timezone") or "America/Sao_Paulo").replace("task.schedule_timezone ||", "").strip() or "America/Sao_Paulo"


def validate_profile(profile: dict[str, Any], profile_path: Path) -> None:
    runner = profile.get("runner", {}) if isinstance(profile.get("runner"), dict) else {}
    account_type = str(profile.get("account_type") or runner.get("default_account_type") or "marketing")
    if account_type != "marketing":
        raise ValueError(f"{profile_path} is not a marketing workflow profile.")
    if "tiktok_studio" not in profile.get("apps", {}):
        raise ValueError(f"{profile_path} does not configure apps.tiktok_studio.")


def slice_steps(steps: list[dict[str, Any]], from_step: str, to_step: str) -> list[dict[str, Any]]:
    selected = list(steps)
    if from_step:
        start = next((index for index, step in enumerate(selected) if step.get("id") == from_step), None)
        if start is None:
            raise ValueError(f"from-step not found: {from_step}")
        selected = selected[start:]
    if to_step:
        end = next((index for index, step in enumerate(selected) if step.get("id") == to_step), None)
        if end is None:
            raise ValueError(f"to-step not found: {to_step}")
        selected = selected[: end + 1]
    return selected


def workflow_env() -> dict[str, str]:
    ANDROID_HOME.mkdir(parents=True, exist_ok=True)
    android_dir = ANDROID_HOME / ".android"
    android_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["ANDROID_USER_HOME"] = str(ANDROID_HOME)
    env["USERPROFILE"] = str(ANDROID_HOME)
    env["HOME"] = str(ANDROID_HOME)
    env["ADB_VENDOR_KEYS"] = str(android_dir / "adbkey")
    env["ADB_SERVER_PORT"] = os.environ.get("GROUP_CONTROL_ADB_SERVER_PORT", DEFAULT_ADB_SERVER_PORT)
    return env


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"JSON file must contain an object: {path}")
    return data


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
