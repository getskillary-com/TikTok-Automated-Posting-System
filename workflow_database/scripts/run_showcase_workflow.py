from __future__ import annotations

import argparse
import base64
import fnmatch
import json
import os
import re
import shlex
import subprocess
import sys
import time
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_PROFILE = PROJECT_ROOT / "workflow_database" / "devices" / "redmi_2312DRA50G" / "showcase_tiktok_app.json"
DEFAULT_ADB = PROJECT_ROOT / "platform-tools" / "adb.exe"
RUN_ROOT = PROJECT_ROOT / "workflow_database" / "runs"
ADB_KEYBOARD_IME = "com.android.adbkeyboard/.AdbIME"
UPLOAD_SYSTEM_ROOT = PROJECT_ROOT / "upload_system"
WORKFLOW_STEPS_ROOT = UPLOAD_SYSTEM_ROOT / "workflow_steps"
DEFAULT_PRODUCT_MATCH_PREFIX_CHARS = 25
DEFAULT_PRODUCT_SEARCH_TITLE_MAX_CHARS = DEFAULT_PRODUCT_MATCH_PREFIX_CHARS
DEFAULT_CAPTION_VERIFY_PREFIX_CHARS = 25
DEFAULT_CAPTION_VERIFY_MIN_CHARS = 8
SKIPPED_PRODUCT_SEARCH_STEP_IDS = {
    "tap_product_search",
    "input_product_search_title",
    "confirm_product_search_editor_code",
}
SKIPPED_RUNTIME_STEP_IDS = SKIPPED_PRODUCT_SEARCH_STEP_IDS | {
    "wait_schedule_picker_open",
}
FINAL_PUBLISH_STEP_IDS = {"tap_scheduled_publish", "tap_publish"}

for import_path in (PROJECT_ROOT, SCRIPT_DIR, UPLOAD_SYSTEM_ROOT, WORKFLOW_STEPS_ROOT):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

from common import capture_screen, load_step_config, native_schedule_picker_signal, resolve_publish_schedule  # noqa: E402
from device_preflight import DevicePreflightOptions, require_device_ready  # noqa: E402
from shared.windows_process import no_window_subprocess_kwargs  # noqa: E402
from set_schedule_datetime import run_wheel_strategy, schedule_state_for_verified_page  # noqa: E402
from upload_system.ai import PaddleOCRButtonFinder, TextCandidate, paddle_ocr_runtime_available, png_size  # noqa: E402


class WorkflowError(RuntimeError):
    pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Run or calibrate a TikTok showcase workflow profile.")
    parser.add_argument("--profile", default=str(DEFAULT_PROFILE), help="Workflow profile JSON path.")
    parser.add_argument("--config", default=str(UPLOAD_SYSTEM_ROOT / "config.json"), help="Upload pipeline config JSON path.")
    parser.add_argument("--adb", default=str(DEFAULT_ADB), help="ADB executable path.")
    parser.add_argument("--serial", default="", help="ADB serial. Defaults to the profile known device if omitted.")
    parser.add_argument("--video", default="", help="Local video path to push before publishing.")
    parser.add_argument("--caption", default="", help="Caption text for the publish screen.")
    parser.add_argument("--product-search-title", default="", help="Product title prefix used for XML/OCR matching.")
    parser.add_argument("--product-publish-name", default="", help="Product name to fill after product selection.")
    parser.add_argument("--publish-mode", choices=["immediate", "scheduled"], default="immediate", help="Showcase publish mode.")
    parser.add_argument("--schedule-timezone", default="", help="IANA timezone for scheduled showcase publish.")
    parser.add_argument("--scheduled-at", default="", help="Full scheduled datetime in ISO format.")
    parser.add_argument("--section", choices=["publish", "cleanup", "all"], default="publish")
    parser.add_argument("--from-step", default="", help="Step id to start from.")
    parser.add_argument("--to-step", default="", help="Step id to stop after.")
    parser.add_argument("--state", default="", help="Pipeline state JSON path to update for queue integration.")
    parser.add_argument("--run-dir", default="", help="Artifact directory. Defaults to workflow_database/runs/<run-id>.")
    parser.add_argument("--dry-run", action="store_true", help="Capture and log actions without tapping/pasting.")
    parser.add_argument(
        "--capture-mode",
        choices=["all", "errors", "none"],
        default="all",
        help="Capture screenshots/XML before every step, only on errors, or not at all.",
    )
    parser.add_argument(
        "--wait-scale",
        type=float,
        default=1.0,
        help="Multiplier for fixed wait_seconds values. Use <1 after calibration to speed up the workflow.",
    )
    parser.add_argument("--allow-publish", action="store_true", help="Allow the final publish tap.")
    parser.add_argument(
        "--stop-before-final-publish",
        action="store_true",
        help="Run real steps until the final publish tap, then stop without publishing.",
    )
    parser.add_argument("--skip-device-preflight", action="store_true", help="Skip startup wake/readiness preflight.")
    parser.add_argument("--skip-wake", action="store_true", help="Fail instead of waking a sleeping or locked phone.")
    parser.add_argument("--run-id", default="", help="Optional run id for artifacts.")
    args = parser.parse_args()

    profile = read_json(Path(args.profile))
    config = load_step_config(args.config)
    if args.schedule_timezone:
        config.setdefault("pipeline", {}).setdefault("schedule", {})["timezone"] = args.schedule_timezone
    ensure_showcase_schedule_config(config, args.schedule_timezone)
    apply_showcase_schedule_points(config, profile)
    run_dir = Path(args.run_dir).resolve() if args.run_dir else RUN_ROOT / (args.run_id or datetime.now().strftime("%Y%m%d-%H%M%S"))
    run_dir.mkdir(parents=True, exist_ok=True)
    state_path = Path(args.state).resolve() if args.state else run_dir / "state.json"
    completed_steps: list[str] = []

    serial = args.serial or str(profile.get("device", {}).get("known_adb_serial") or "")
    adb = ADB(args.adb, serial)
    context = build_context(args, profile, run_dir)
    write_json(run_dir / "profile_snapshot.json", profile)
    write_json(run_dir / "context.json", redact_context(context))
    write_showcase_state(state_path, args=args, context=context, completed_steps=completed_steps)

    if not args.skip_device_preflight:
        preflight = run_device_preflight(args=args, profile=profile, run_dir=run_dir, context=context, serial=serial)
        print(f"device_preflight: ready serial={preflight['serial']} phase=startup screen={preflight.get('screen', '')}")

    verify_device(adb, profile, run_dir)
    grant_media_permissions(adb, profile)
    if args.video:
        cleanup_trace = cleanup_workflow_videos_before_push(
            adb=adb,
            remote_dir=context["remote_video_dir"],
            run_dir=run_dir,
            dry_run=args.dry_run,
            delete_all_videos=managed_gallery_video_dir(profile),
            max_delete_count=max_gallery_video_delete_count(profile),
        )
        write_trace(run_dir, "pre_push_gallery_cleanup", cleanup_trace)
        completed_steps.append("pre_push_gallery_cleanup")
        write_showcase_state(state_path, args=args, context=context, completed_steps=completed_steps)
        remote_path = push_video(adb, Path(args.video), context["remote_video_dir"])
        context["remote_video_path"] = remote_path
        completed_steps.append("push_video")
        write_json(run_dir / "context.json", redact_context(context))
        write_showcase_state(state_path, args=args, context=context, completed_steps=completed_steps)
        verify_trace = verify_current_gallery_video(
            adb=adb,
            remote_dir=context["remote_video_dir"],
            remote_path=remote_path,
            run_dir=run_dir,
            dry_run=args.dry_run,
            require_single_video=managed_gallery_video_dir(profile),
        )
        write_trace(run_dir, "verify_current_gallery_video", verify_trace)
        if verify_trace.get("error"):
            raise WorkflowError(str(verify_trace["error"]))
        completed_steps.append("verify_current_gallery_video")
        write_showcase_state(state_path, args=args, context=context, completed_steps=completed_steps)

    steps: list[dict[str, Any]] = []
    if args.section in {"publish", "all"}:
        steps.extend(profile.get("workflow", []))
    if args.section in {"cleanup", "all"}:
        print("Gallery cleanup steps are disabled; skipping cleanup section.")

    steps = filter_steps_for_publish_mode(steps, args.publish_mode)
    steps = slice_steps(steps, args.from_step, args.to_step)
    for step in steps:
        step_id = str(step.get("id") or "")
        if context.get("stop_before_final_publish") and step_id in FINAL_PUBLISH_STEP_IDS:
            context["stopped_before_final_publish"] = True
            context["stopped_before_final_publish_step"] = step_id
            stop_trace = {
                "step": step.get("step"),
                "id": step_id,
                "type": step.get("type"),
                "action": "stop_before_final_publish",
                "publish_mode": context.get("publish_mode", ""),
                "schedule_configured_verified": bool(context.get("schedule_configured_verified", False)),
                "completed_steps_before_stop": completed_steps,
                "stopped_at": datetime.now().isoformat(timespec="seconds"),
            }
            write_trace(run_dir, step_id, stop_trace)
            write_json(run_dir / "context.json", redact_context(context))
            write_showcase_state(state_path, args=args, context=context, completed_steps=completed_steps)
            print(f"{step_id}: stop_before_final_publish")
            break
        run_step(adb=adb, profile=profile, config=config, context=context, step=step, dry_run=args.dry_run, run_dir=run_dir, state_path=state_path)
        completed_steps.append(step_id)
        write_showcase_state(state_path, args=args, context=context, completed_steps=completed_steps)

    print(f"Workflow run artifacts: {run_dir}")
    return 0


class ADB:
    def __init__(self, adb_path: str, serial: str = "") -> None:
        self.adb_path = adb_path
        self.serial = serial

    def base(self) -> list[str]:
        args = [self.adb_path]
        if self.serial:
            args.extend(["-s", self.serial])
        return args

    def run(self, args: list[str], *, check: bool = True, text: bool = True, timeout: int = 60) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        android_home = PROJECT_ROOT / "storage" / "android_home"
        (android_home / ".android").mkdir(parents=True, exist_ok=True)
        env["ANDROID_USER_HOME"] = str(android_home)
        env["USERPROFILE"] = str(android_home)
        env["HOME"] = str(android_home)
        env["ADB_VENDOR_KEYS"] = str(android_home / ".android" / "adbkey")
        command = self.base() + args
        result: subprocess.CompletedProcess | None = None
        for attempt in range(3):
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=text,
                encoding="utf-8" if text else None,
                errors="replace" if text else None,
                timeout=timeout,
                env=env,
                **no_window_subprocess_kwargs(),
            )
            if result.returncode == 0 or attempt == 2 or not adb_transient_failure(result):
                break
            time.sleep(1.0)
        assert result is not None
        if check and result.returncode != 0:
            stderr = result.stderr if isinstance(result.stderr, str) else result.stderr.decode("utf-8", "replace")
            raise WorkflowError(f"ADB command failed: {' '.join(command)}\n{stderr.strip()}")
        return result

    def shell(self, *args: str, check: bool = True, timeout: int = 60) -> str:
        return self.run(["shell", *args], check=check, timeout=timeout).stdout

    def shell_command(self, command: str, *, check: bool = True, timeout: int = 60) -> str:
        return self.run(["shell", command], check=check, timeout=timeout).stdout

    def wait(self, seconds: float) -> None:
        time.sleep(max(0.0, float(seconds)))

    def tap(self, x: int, y: int) -> None:
        self.shell("input", "tap", str(x), str(y), timeout=15)

    def long_press(self, x: int, y: int, duration_ms: int) -> None:
        self.shell("input", "swipe", str(x), str(y), str(x), str(y), str(duration_ms), timeout=15)

    def swipe(self, start_x: int, start_y: int, end_x: int, end_y: int, duration_ms: int) -> None:
        self.shell(
            "input",
            "swipe",
            str(start_x),
            str(start_y),
            str(end_x),
            str(end_y),
            str(duration_ms),
            timeout=15,
        )

    def screenshot(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        result = self.run(["exec-out", "screencap", "-p"], check=False, text=False, timeout=90)
        if result.returncode == 0 and result.stdout.startswith(b"\x89PNG") and len(result.stdout) > 1024:
            path.write_bytes(result.stdout)
            return path

        remote_path = "/sdcard/workflow_screen.png"
        self.shell("screencap", "-p", remote_path, timeout=90)
        pull = self.run(["pull", remote_path, str(path)], check=False, timeout=120)
        if pull.returncode == 0 and path.exists() and path.read_bytes().startswith(b"\x89PNG"):
            return path
        self.pull_file_base64(remote_path, path)
        return path

    def pull_file_base64(self, remote_path: str, local_path: Path, *, chunk_size: int = 6144) -> None:
        size_output = self.shell_command("wc -c < " + shlex.quote(remote_path), timeout=15).strip()
        try:
            size = int(size_output.split()[0])
        except (IndexError, ValueError) as exc:
            raise WorkflowError(f"Cannot read remote file size for {remote_path}: {size_output}") from exc
        chunks = bytearray()
        for skip in range(0, size, chunk_size):
            command = (
                "dd if="
                + shlex.quote(remote_path)
                + f" bs={chunk_size} skip={skip // chunk_size} count=1 2>/dev/null | base64"
            )
            decoded = b""
            last_error = ""
            for attempt in range(3):
                result = self.run(["shell", command], check=False, timeout=30)
                stdout = result.stdout if isinstance(result.stdout, str) else (result.stdout or b"").decode("utf-8", "replace")
                stderr = result.stderr if isinstance(result.stderr, str) else (result.stderr or b"").decode("utf-8", "replace")
                encoded = "".join(stdout.split())
                last_error = stderr.strip() or stdout[:120].strip()
                try:
                    decoded = base64.b64decode(encoded, validate=True) if encoded else b""
                except Exception as exc:  # noqa: BLE001
                    last_error = str(exc)
                    decoded = b""
                if result.returncode == 0 and decoded:
                    break
                time.sleep(0.5)
            if not decoded:
                raise WorkflowError(f"Failed to read screenshot chunk at byte {skip}: {last_error}")
            chunks.extend(decoded)
        local_path.write_bytes(bytes(chunks))

    def dump_xml(self, path: Path) -> str:
        path.parent.mkdir(parents=True, exist_ok=True)
        remote_path = "/sdcard/window.xml"
        self.shell("rm", "-f", remote_path, check=False, timeout=10)
        dump = self.run(["shell", "uiautomator", "dump", remote_path], check=False, timeout=30)
        if dump.returncode != 0:
            path.write_text("", encoding="utf-8")
            return ""
        result = self.run(["exec-out", "cat", remote_path], check=False, text=False, timeout=30)
        text = result.stdout.decode("utf-8", "replace") if result.stdout else ""
        if "<hierarchy" not in text:
            text = ""
        path.write_text(text, encoding="utf-8")
        return text

    def dump_ui_xml(self) -> str:
        remote_path = "/sdcard/window.xml"
        self.shell("rm", "-f", remote_path, check=False, timeout=10)
        dump = self.run(["shell", "uiautomator", "dump", remote_path], check=False, timeout=30)
        if dump.returncode != 0:
            return ""
        result = self.run(["exec-out", "cat", remote_path], check=False, text=False, timeout=30)
        text = result.stdout.decode("utf-8", "replace") if result.stdout else ""
        return text if "<hierarchy" in text else ""

    def foreground_package(self) -> str:
        output = self.shell("dumpsys", "window", check=False, timeout=15)
        for pattern in (
            r"mCurrentFocus=Window\{[^ ]+ u\d+ ([^/\s]+)",
            r"mFocusedApp=ActivityRecord\{[^ ]+ u\d+ ([^/\s]+)",
        ):
            match = re.search(pattern, output)
            if match:
                return match.group(1)
        return ""

    def open_app(self, package: str) -> None:
        self.run(["shell", "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1"], timeout=30)

    def force_stop(self, package: str) -> None:
        self.shell("am", "force-stop", package, check=False, timeout=15)

    def editor_code(self, code: int) -> None:
        self.shell("am", "broadcast", "-a", "ADB_EDITOR_CODE", "--ei", "code", str(code), check=False, timeout=15)


def run_step(
    *,
    adb: ADB,
    profile: dict[str, Any],
    config: dict[str, Any],
    context: dict[str, Any],
    step: dict[str, Any],
    dry_run: bool,
    run_dir: Path,
    state_path: Path,
) -> None:
    step_id = str(step["id"])
    step_type = str(step["type"])
    trace: dict[str, Any] = {
        "step": step.get("step"),
        "id": step_id,
        "type": step_type,
        "dry_run": dry_run,
        "started_at": datetime.now().isoformat(timespec="seconds"),
    }
    started = time.monotonic()
    ocr_required = bool(step.get("ocr_required", False))
    screen = maybe_capture(
        adb,
        run_dir,
        step_id,
        context,
        need_xml=step_type
        in {
            "optional_tap_text_or_point",
            "tap_text_or_point",
            "tap_point_until_any_text",
            "assert_media_picker_item_count",
            "assert_product_link_attached",
            "ensure_showcase_product_attached",
            "optional_remove_added_music",
            "tap_first_matching_node",
            "wait_for_any_text",
            "wait_for_edit_text_at_point",
            "verify_schedule_configured",
        },
        need_screenshot=ocr_required or step_type == "ensure_showcase_product_attached",
    )
    if screen["screenshot"]:
        trace["screenshot"] = str(screen["screenshot"])
    if screen["xml"]:
        trace["xml"] = str(screen["xml"])

    try:
        if step_type in {
            "tap_point",
            "tap_text_or_point",
            "optional_tap_text_or_point",
            "tap_point_until_any_text",
            "tap_first_matching_node",
        }:
            screen, permission_dialogs = clear_android_permission_dialogs(
                adb=adb,
                run_dir=run_dir,
                step_id=step_id,
                screen=screen,
                dry_run=dry_run,
                wait_seconds=1.0 * float(context.get("wait_scale") or 1.0),
            )
            if permission_dialogs:
                trace["permission_dialogs"] = permission_dialogs
                if screen.get("screenshot"):
                    trace["screenshot"] = str(screen["screenshot"])
                if screen.get("xml"):
                    trace["xml"] = str(screen["xml"])

        required_app = str(step.get("required_app") or "")
        if not required_app and step_type == "tap_point_until_any_text":
            required_app = "tiktok"
        if required_app:
            required_package = app_package(profile, required_app)
            app_check = confirm_expected_package(
                adb=adb,
                run_dir=run_dir,
                step_id=step_id,
                expected_package=required_package,
                initial_xml_text=screen["xml_text"],
                phase="before_step",
            )
            trace["required_app_check"] = app_check
            if not app_check["found"]:
                current = app_check.get("foreground_package") or ", ".join(app_check.get("xml_packages") or []) or "none"
                raise WorkflowError(
                    f"{step_id}: expected {required_package} in foreground, current package: {current}."
                )

        if step_type == "open_app":
            package = app_package(profile, str(step["app"]))
            trace.update({"action": "open_app", "package": package})
            if not dry_run:
                adb.force_stop(package)
                adb.open_app(package)
                foreground_found, foreground_attempts = wait_for_package_foreground(
                    adb=adb,
                    run_dir=run_dir,
                    step_id=step_id,
                    package=package,
                    timeout_seconds=float(step.get("foreground_timeout_seconds") or 10.0),
                    poll_seconds=float(step.get("foreground_poll_seconds") or 0.75),
                )
                trace["foreground_package_found"] = foreground_found
                trace["foreground_attempts"] = foreground_attempts
                if not foreground_found:
                    raise WorkflowError(f"{step_id}: opened {package}, but it did not reach the foreground.")

        elif step_type == "adb_editor_code":
            code = int(step["code"])
            trace.update({"action": "adb_editor_code", "code": code})
            if not dry_run:
                adb.editor_code(code)

        elif step_type == "assert_media_picker_item_count":
            expected_count = int(step.get("expected_count", 1))
            count_mode = str(step.get("count_mode") or "exact")
            timeout_seconds = float(step.get("timeout_seconds", 6.0))
            found, count, attempts = wait_for_media_picker_item_count(
                adb=adb,
                run_dir=run_dir,
                step_id=step_id,
                expected_count=expected_count,
                count_mode=count_mode,
                timeout_seconds=timeout_seconds,
                initial_xml=screen["xml_text"],
                initial_screenshot=screen["screenshot"],
                step=step,
            )
            trace.update(
                {
                    "action": "assert_media_picker_item_count",
                    "expected_count": expected_count,
                    "count_mode": count_mode,
                    "found_count": count,
                    "found": found,
                    "attempts": attempts,
                }
            )
            if not found:
                raise WorkflowError(
                    f"{step_id}: expected {media_picker_count_expectation_text(expected_count, count_mode)} "
                    f"media picker video item(s), found {count}."
                )

        elif step_type == "tap_first_matching_node":
            selector = dict(step.get("selector") or {})
            timeout_seconds = float(step.get("timeout_seconds", 3.0))
            match, attempts = wait_for_first_matching_node(
                adb=adb,
                run_dir=run_dir,
                step_id=step_id,
                selector=selector,
                timeout_seconds=timeout_seconds,
                initial_xml=screen["xml_text"],
            )
            trace.update(
                {
                    "action": "tap_first_matching_node",
                    "selector": selector,
                    "attempts": attempts,
                    "found": bool(match),
                }
            )
            if not match:
                raise WorkflowError(f"{step_id}: matching XML node was not found.")
            trace.update(match)
            if not dry_run:
                adb.tap(int(match["x"]), int(match["y"]))

        elif step_type == "ensure_showcase_product_attached":
            product_trace = ensure_showcase_product_attached(
                adb=adb,
                profile=profile,
                step=step,
                context=context,
                run_dir=run_dir,
                dry_run=dry_run,
                initial_screen=screen,
            )
            trace.update({"action": "ensure_showcase_product_attached", **product_trace})

        elif step_type == "optional_remove_added_music":
            music_trace = optional_remove_added_music(
                adb=adb,
                run_dir=run_dir,
                step_id=step_id,
                step=step,
                context=context,
                screen=screen,
                dry_run=dry_run,
            )
            trace.update({"action": "optional_remove_added_music", **music_trace})

        elif step_type in {"tap_point", "tap_text_or_point", "optional_tap_text_or_point"}:
            if step_id == "open_gallery_from_camera":
                video_mode_trace = ensure_camera_video_mode_before_gallery(
                    adb=adb,
                    run_dir=run_dir,
                    step=step,
                    screen=screen,
                    dry_run=dry_run,
                    wait_scale=float(context.get("wait_scale") or 1.0),
                )
                trace["camera_video_mode"] = video_mode_trace
                if video_mode_trace.get("tapped"):
                    screen = capture(adb, run_dir, f"{step_id}_after_video_mode")
                    trace["camera_video_mode"]["after_tap_screenshot"] = str(screen.get("screenshot") or "")
                    trace["camera_video_mode"]["after_tap_xml"] = str(screen.get("xml") or "")
            point = point_from_step(profile, step)
            tap_already_performed = False
            match_mode = str(step.get("xml_match_mode") or "contains")
            if step_type == "optional_tap_text_or_point":
                optional_texts = step.get("texts", [])
                xml_found = xml_has_any_text(screen["xml_text"], optional_texts, match_mode=match_mode)
                trace.update({"optional_xml_found": xml_found, "ocr_required": ocr_required})
                if ocr_required:
                    ocr_gate = paddle_ocr_optional_gate(
                        adb=adb,
                        run_dir=run_dir,
                        step_id=step_id,
                        step=step,
                        screen=screen,
                        trace=trace,
                    )
                    if not ocr_gate["present"]:
                        trace.update(
                            {
                                "action": "skip_absent",
                                "reason": "PaddleOCR gate did not find the optional popup text.",
                            }
                        )
                        wait_seconds = float(step.get("wait_seconds", 0.0)) * float(context.get("wait_scale") or 1.0)
                        if wait_seconds:
                            time.sleep(wait_seconds)
                        trace["duration_seconds"] = round(time.monotonic() - started, 3)
                        trace["completed_at"] = datetime.now().isoformat(timespec="seconds")
                        write_trace(run_dir, step_id, trace)
                        print_step_result(trace)
                        return
                    ocr_tap = ocr_gate.get("tap")
                    if ocr_tap:
                        trace.update(
                            {
                                "action": "tap_ocr_button",
                                "reason": "PaddleOCR gate found the optional popup and confirm button.",
                                "x": int(ocr_tap["x"]),
                                "y": int(ocr_tap["y"]),
                                "ocr_button_matched_text": str(ocr_tap.get("matched_text") or ""),
                                "ocr_button_candidate_text": str(ocr_tap.get("candidate_text") or ""),
                                "ocr_button_candidate_confidence": float(ocr_tap.get("confidence") or 0.0),
                            }
                        )
                        if not dry_run:
                            adb.tap(int(ocr_tap["x"]), int(ocr_tap["y"]))
                            if step_id == "optional_confirm_add_product":
                                time.sleep(float(step.get("wait_after_tap_seconds") or 1.0) * float(context.get("wait_scale") or 1.0))
                                after_screen = capture(adb, run_dir, f"{step_id}_after_tap")
                                trace["after_tap_screenshot"] = str(after_screen.get("screenshot") or "")
                                trace["after_tap_xml"] = str(after_screen.get("xml") or "")
                                after_xml = str(after_screen.get("xml_text") or "")
                                if centered_modal_dialog_visible(after_xml) and not product_publish_name_page_visible(
                                    after_xml,
                                    product_texts_with_defaults(step.get("add_button_texts"), DEFAULT_PRODUCT_ADD_TEXTS),
                                ):
                                    raise WorkflowError(f"{step_id}: confirmation dialog is still visible after tapping confirm.")
                        tap_already_performed = True
                    else:
                        trace.update(
                            {
                                "action": "tap_calibrated_point",
                                "reason": "PaddleOCR gate found the optional popup; confirm button OCR was unavailable, using calibrated point.",
                                **point_log(point),
                            }
                        )
                        if not dry_run:
                            adb.tap(int(point["x"]), int(point["y"]))
                            if step_id == "optional_confirm_add_product":
                                time.sleep(float(step.get("wait_after_tap_seconds") or 1.0) * float(context.get("wait_scale") or 1.0))
                                after_screen = capture(adb, run_dir, f"{step_id}_after_tap")
                                trace["after_tap_screenshot"] = str(after_screen.get("screenshot") or "")
                                trace["after_tap_xml"] = str(after_screen.get("xml") or "")
                                after_xml = str(after_screen.get("xml_text") or "")
                                if centered_modal_dialog_visible(after_xml) and not product_publish_name_page_visible(
                                    after_xml,
                                    product_texts_with_defaults(step.get("add_button_texts"), DEFAULT_PRODUCT_ADD_TEXTS),
                                ):
                                    raise WorkflowError(f"{step_id}: confirmation dialog is still visible after tapping confirm.")
                        tap_already_performed = True
                elif not xml_found:
                    if step.get("point_fallback_when_text_missing"):
                        trace.update(
                            {
                                "action": "optional_point_fallback",
                                "reason": "No optional target text found; using explicit point fallback.",
                            }
                        )
                        if not dry_run:
                            adb.tap(int(point["x"]), int(point["y"]))
                    else:
                        trace.update({"action": "skip_optional", "reason": "No optional target text found."})
                    wait_seconds = float(step.get("wait_seconds", 0.0)) * float(context.get("wait_scale") or 1.0)
                    if wait_seconds:
                        time.sleep(wait_seconds)
                    trace["duration_seconds"] = round(time.monotonic() - started, 3)
                    trace["completed_at"] = datetime.now().isoformat(timespec="seconds")
                    write_trace(run_dir, step_id, trace)
                    print_step_result(trace)
                    return

            if step.get("requires_allow_publish") and not context.get("allow_publish") and not dry_run:
                raise WorkflowError(f"{step_id}: final publish is blocked. Re-run with --allow-publish.")
            if not tap_already_performed and step.get("prefer_xml_selector"):
                selector = dict(step.get("selector") or {})
                if selector:
                    selector_timeout_seconds = float(step.get("selector_timeout_seconds") or 0.0)
                    if selector_timeout_seconds > 0:
                        selector_tap, selector_attempts = wait_for_first_matching_node(
                            adb=adb,
                            run_dir=run_dir,
                            step_id=step_id,
                            selector=selector,
                            timeout_seconds=selector_timeout_seconds,
                            initial_xml=screen["xml_text"],
                        )
                    else:
                        selector_tap = first_matching_node_center(screen["xml_text"], selector)
                        selector_attempts = [{"attempt": 0, "found": bool(selector_tap), "xml": ""}]
                    trace["xml_selector"] = {
                        "selector": selector,
                        "attempts": selector_attempts,
                        "found": bool(selector_tap),
                    }
                    if selector_tap:
                        trace.update({"action": "tap_xml_selector", **selector_tap})
                        if not dry_run:
                            adb.tap(int(selector_tap["x"]), int(selector_tap["y"]))
                        tap_already_performed = True
            if not tap_already_performed:
                text_tap = None
                if step_type == "tap_text_or_point":
                    text_tap = first_text_match_center(
                        screen["xml_text"],
                        step.get("texts", []),
                        match_mode=match_mode,
                    )
                if text_tap:
                    trace.update({"action": "tap_text", **text_tap})
                    if not dry_run:
                        adb.tap(int(text_tap["x"]), int(text_tap["y"]))
                else:
                    if step_type == "tap_text_or_point" and step.get("require_text_match"):
                        trace.update(
                            {
                                "action": "text_match_required_missing",
                                "target_texts": step.get("texts", []),
                                "text_match_mode": match_mode,
                            }
                        )
                        raise WorkflowError(f"{step_id}: required XML text target was not found; refusing point fallback.")
                    tap_trace(adb, point, dry_run=dry_run, trace=trace)
            if step.get("secondary_point"):
                secondary = coordinate(profile, str(step["secondary_point"]))
                trace["secondary_tap"] = point_log(secondary)
                if not dry_run:
                    adb.tap(int(secondary["x"]), int(secondary["y"]))

        elif step_type == "tap_point_until_any_text":
            point = point_from_step(profile, step)
            found = tap_until_any_text(
                adb=adb,
                run_dir=run_dir,
                step_id=step_id,
                point=point,
                texts=step.get("texts", []),
                tap_texts=step.get("tap_texts", []),
                max_taps=int(step.get("max_taps", 4)),
                wait_between_seconds=float(step.get("wait_between_seconds", 1.2)) * float(context.get("wait_scale") or 1.0),
                match_mode=str(step.get("xml_match_mode") or "contains"),
                tap_match_mode=str(step.get("tap_text_match_mode") or "contains"),
                dry_run=dry_run,
                trace=trace,
                initial_xml=screen["xml_text"],
                required_package=app_package(profile, required_app) if required_app else "",
            )
            if not found:
                raise WorkflowError(f"{step_id}: target page was not reached after repeated taps.")

        elif step_type == "tap_point_and_paste":
            point = point_from_step(profile, step)
            tap_trace(adb, point, dry_run=dry_run, trace=trace)
            input_key = str(step["input"])
            value = context_value(context, input_key)
            if input_key == "caption":
                value, appended_trailing_space = caption_with_trailing_space(value)
                trace["caption_trailing_space_appended"] = appended_trailing_space
            trace["input_key"] = input_key
            trace["input_preview"] = value[:80]
            trace["clear_existing_text"] = bool(step.get("clear_existing_text", False))
            if not dry_run:
                if step.get("clear_existing_text", False):
                    clear_focused_text(adb, int(step.get("clear_max_chars", 160)))
                paste_text(adb, value)
                if step.get("hide_keyboard_after_paste", False):
                    time.sleep(0.5)
                    adb.shell("input", "keyevent", "4", check=False, timeout=15)
                if input_key == "caption":
                    verify_caption_pasted_or_raise(
                        adb=adb,
                        run_dir=run_dir,
                        step_id=step_id,
                        step=step,
                        context=context,
                        point=point,
                        caption=value,
                        trace=trace,
                    )

        elif step_type == "paste_text":
            input_key = str(step["input"])
            value = context_value(context, input_key)
            if input_key == "caption":
                value, appended_trailing_space = caption_with_trailing_space(value)
                trace["caption_trailing_space_appended"] = appended_trailing_space
            trace.update(
                {
                    "action": "paste_text",
                    "input_key": input_key,
                    "input_preview": value[:80],
                    "clear_existing_text": bool(step.get("clear_existing_text", False)),
                }
            )
            if not dry_run:
                if step.get("clear_existing_text", False):
                    clear_focused_text(adb, int(step.get("clear_max_chars", 200)))
                paste_text(adb, value)
                if step.get("press_enter_after_paste", False):
                    adb.shell("input", "keyevent", "66", check=False, timeout=15)

        elif step_type == "long_press_point":
            point = point_from_step(profile, step)
            duration_ms = int(step.get("duration_ms", 800))
            trace.update({"action": "long_press", **point_log(point), "duration_ms": duration_ms})
            if not dry_run:
                adb.long_press(int(point["x"]), int(point["y"]), duration_ms)

        elif step_type == "wait_for_any_text":
            match_mode = str(step.get("xml_match_mode") or "contains")
            found = xml_has_any_text(screen["xml_text"], step.get("texts", []), match_mode=match_mode)
            if not found:
                found = wait_for_any_text(
                    adb,
                    run_dir,
                    step_id,
                    step.get("texts", []),
                    float(step.get("timeout_seconds", 5.0)),
                    match_mode=match_mode,
                )
            trace.update({"action": "wait_for_any_text", "found": found})
            if not found:
                raise WorkflowError(f"{step_id}: expected text was not found.")

        elif step_type == "wait_schedule_picker_open":
            picker_trace = wait_for_schedule_picker_open(
                adb=adb,
                config=config,
                context=context,
                run_dir=run_dir,
                state_path=state_path,
                step_id=step_id,
                timeout_seconds=float(step.get("timeout_seconds", 8.0)),
                poll_seconds=float(step.get("poll_seconds", 0.75)),
                dry_run=dry_run,
            )
            trace.update({"action": "wait_schedule_picker_open", **picker_trace})
            if not picker_trace.get("is_open"):
                raise WorkflowError(f"{step_id}: schedule picker was not detected; refusing to apply wheel swipes.")

        elif step_type == "assert_product_link_attached":
            found, match, attempts, keywords = wait_for_product_link_attached(
                adb=adb,
                run_dir=run_dir,
                step_id=step_id,
                context=context,
                timeout_seconds=float(step.get("timeout_seconds", 5.0)),
                min_prefix_chars=int(step.get("min_prefix_chars", 10)),
                initial_xml=screen["xml_text"],
            )
            trace.update(
                {
                    "action": "assert_product_link_attached",
                    "found": found,
                    "match": match,
                    "keywords": keywords,
                    "attempts": attempts,
                }
            )
            if not found:
                expected_name = str(context.get("product_publish_name") or context.get("product_search_title") or "")
                raise WorkflowError(
                    f"{step_id}: product link is not attached before publish; refusing to publish without product link. "
                    f"expected={expected_name!r}"
                )

        elif step_type == "set_schedule_datetime":
            schedule_trace = set_showcase_schedule_datetime(
                adb=adb,
                config=config,
                context=context,
                state_path=state_path,
                run_dir=run_dir,
                dry_run=dry_run,
            )
            trace.update(
                {
                    "action": "set_schedule_datetime",
                    "target_iso": context.get("scheduled_at", ""),
                    "date_iso": context.get("schedule_date", ""),
                    "time_24": context.get("schedule_time", ""),
                    "timezone": context.get("schedule_timezone", ""),
                    "schedule_trace": schedule_trace,
                }
            )

        elif step_type == "verify_schedule_configured":
            if not context.get("schedule_configured"):
                raise WorkflowError(f"{step_id}: schedule has not been configured.")
            schedule_verification = verify_showcase_schedule_configured_from_current_ui(
                adb=adb,
                run_dir=run_dir,
                step_id=step_id,
                step=step,
                initial_screen=screen,
                context=context,
                dry_run=dry_run,
            )
            context["schedule_configured_verified"] = True
            trace.update(
                {
                    "action": "verify_schedule_configured",
                    "schedule_configured_verified": True,
                    "ui_verification": schedule_verification,
                    "target_iso": context.get("scheduled_at", ""),
                    "date_iso": context.get("schedule_date", ""),
                    "time_24": context.get("schedule_time", ""),
                    "timezone": context.get("schedule_timezone", ""),
                }
            )

        elif step_type == "minimize_app":
            package = app_package(profile, str(step.get("app") or "tiktok"))
            keyevent = str(step.get("home_keyevent") or "3")
            trace.update({"action": "minimize_app", "package": package, "keyevent": keyevent})
            if not dry_run:
                adb.shell("input", "keyevent", keyevent, check=False, timeout=15)

        elif step_type == "wait_for_edit_text_at_point":
            point = point_from_step(profile, step)
            tolerance_px = int(step.get("tolerance_px", 160))
            found = wait_for_edit_text_at_point(
                adb,
                run_dir,
                step_id,
                point,
                float(step.get("timeout_seconds", 20.0)),
                tolerance_px,
            )
            trace.update({"action": "wait_for_edit_text_at_point", **point_log(point), "tolerance_px": tolerance_px, "found": found})
            if not found and step.get("back_on_timeout") and not dry_run:
                adb.shell("input", "keyevent", "4", check=False, timeout=15)
                wait_after_back_seconds = float(step.get("wait_after_back_seconds", 1.0))
                if wait_after_back_seconds:
                    time.sleep(wait_after_back_seconds)
                retry_timeout_seconds = float(step.get("retry_timeout_seconds_after_back", step.get("timeout_seconds", 20.0)))
                found = wait_for_edit_text_at_point(
                    adb,
                    run_dir,
                    f"{step_id}_after_back",
                    point,
                    retry_timeout_seconds,
                    tolerance_px,
                )
                trace.update(
                    {
                        "back_on_timeout": True,
                        "wait_after_back_seconds": wait_after_back_seconds,
                        "retry_timeout_seconds_after_back": retry_timeout_seconds,
                        "found_after_back": found,
                        "found": found,
                    }
                )
            if not found:
                raise WorkflowError(f"{step_id}: expected EditText near target point was not found.")

        elif step_type == "wait_for_publish_success_then_close_app":
            found = wait_for_any_text(
                adb,
                run_dir,
                step_id,
                step.get("texts", []),
                float(step.get("timeout_seconds", 90.0)),
                match_mode=str(step.get("xml_match_mode") or "contains"),
            )
            package = app_package(profile, str(step["app"]))
            trace.update({"action": "wait_publish_success_then_close", "found": found, "package": package})
            if not found:
                raise WorkflowError(f"{step_id}: publish success markers were not found.")
            if not dry_run:
                adb.force_stop(package)

        elif step_type == "open_recents_and_tap_point":
            point = point_from_step(profile, step)
            trace.update({"action": "open_recents_and_tap", **point_log(point)})
            if not dry_run:
                adb.shell("input", "keyevent", "187", check=False, timeout=15)
                time.sleep(1.0)
                adb.tap(int(point["x"]), int(point["y"]))

        else:
            raise WorkflowError(f"Unsupported step type: {step_type}")

        wait_seconds = float(step.get("wait_seconds", 0.0)) * float(context.get("wait_scale") or 1.0)
        if wait_seconds:
            time.sleep(wait_seconds)
        trace["duration_seconds"] = round(time.monotonic() - started, 3)
        trace["completed_at"] = datetime.now().isoformat(timespec="seconds")
        write_trace(run_dir, step_id, trace)
        print_step_result(trace)
    except Exception as exc:
        trace["error"] = str(exc)
        trace["duration_seconds"] = round(time.monotonic() - started, 3)
        trace["failed_at"] = datetime.now().isoformat(timespec="seconds")
        capture_failure(adb, run_dir, step_id, context, trace)
        write_trace(run_dir, step_id, trace)
        raise


def tap_trace(adb: ADB, point: dict[str, Any], *, dry_run: bool, trace: dict[str, Any]) -> None:
    trace.update({"action": "tap", **point_log(point)})
    if not dry_run:
        adb.tap(int(point["x"]), int(point["y"]))


def wait_for_package_foreground(
    *,
    adb: ADB,
    run_dir: Path,
    step_id: str,
    package: str,
    timeout_seconds: float,
    poll_seconds: float,
) -> tuple[bool, list[dict[str, Any]]]:
    deadline = time.monotonic() + max(0.1, timeout_seconds)
    attempts: list[dict[str, Any]] = []
    attempt_no = 0
    while time.monotonic() <= deadline:
        attempt_no += 1
        foreground_package = adb.foreground_package()
        if foreground_package == package:
            attempts.append(
                {
                    "attempt": attempt_no,
                    "found": True,
                    "foreground_package": foreground_package,
                    "packages": [],
                    "source": "dumpsys_window",
                }
            )
            return True, attempts
        xml_path = run_dir / "xml" / f"{step_id}_foreground_{attempt_no:02d}.xml"
        xml_text = adb.dump_xml(xml_path)
        packages = xml_packages(xml_text)
        found = package in packages
        attempts.append(
            {
                "attempt": attempt_no,
                "found": found,
                "foreground_package": foreground_package,
                "packages": packages,
                "xml": str(xml_path),
                "source": "xml",
            }
        )
        if found:
            return True, attempts
        time.sleep(max(0.1, poll_seconds))
    foreground_package = adb.foreground_package()
    if foreground_package == package:
        attempts.append(
            {
                "attempt": attempt_no + 1,
                "found": True,
                "foreground_package": foreground_package,
                "packages": [],
                "source": "dumpsys_window_final",
            }
        )
        return True, attempts
    return False, attempts


def confirm_expected_package(
    *,
    adb: ADB,
    run_dir: Path,
    step_id: str,
    expected_package: str,
    initial_xml_text: str = "",
    phase: str = "",
    retry_count: int = 2,
    retry_wait_seconds: float = 0.6,
) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    xml_text = initial_xml_text
    for attempt_no in range(1, max(1, retry_count + 1) + 1):
        packages = xml_packages(xml_text) if xml_text else []
        foreground_package = "" if expected_package in packages else adb.foreground_package()
        found = expected_package in packages or foreground_package == expected_package
        attempts.append(
            {
                "attempt": attempt_no,
                "found": found,
                "xml_packages": packages,
                "foreground_package": foreground_package,
                "phase": phase,
                "source": "initial" if attempt_no == 1 and xml_text == initial_xml_text else "redump",
            }
        )
        if found:
            return {
                "expected": expected_package,
                "found": True,
                "xml_packages": packages,
                "foreground_package": foreground_package,
                "attempts": attempts,
                "xml_text": xml_text,
            }
        if attempt_no > retry_count:
            break
        time.sleep(max(0.1, retry_wait_seconds))
        xml_path = run_dir / "xml" / f"{step_id}_{phase or 'package'}_foreground_retry_{attempt_no:02d}.xml"
        xml_text = adb.dump_xml(xml_path)
        attempts[-1]["next_retry_xml"] = str(xml_path)
    last = attempts[-1] if attempts else {}
    return {
        "expected": expected_package,
        "found": False,
        "xml_packages": last.get("xml_packages") or [],
        "foreground_package": last.get("foreground_package") or "",
        "attempts": attempts,
        "xml_text": xml_text,
    }


def point_log(point: dict[str, Any]) -> dict[str, Any]:
    return {
        "x": int(point["x"]),
        "y": int(point["y"]),
        "x_ratio": float(point["x_ratio"]),
        "y_ratio": float(point["y_ratio"]),
        "point_description": str(point.get("description") or ""),
        "calibration_status": str(point.get("calibration_status") or ""),
    }


def capture(adb: ADB, run_dir: Path, step_id: str) -> dict[str, Any]:
    screenshot = adb.screenshot(run_dir / "screenshots" / f"{step_id}.png")
    xml_path = run_dir / "xml" / f"{step_id}.xml"
    xml_text = adb.dump_xml(xml_path)
    return {"screenshot": screenshot, "xml": xml_path, "xml_text": xml_text}


def maybe_capture(
    adb: ADB,
    run_dir: Path,
    step_id: str,
    context: dict[str, Any],
    *,
    need_xml: bool,
    need_screenshot: bool = False,
) -> dict[str, Any]:
    capture_mode = str(context.get("capture_mode") or "all")
    if capture_mode == "all" or need_screenshot:
        return capture(adb, run_dir, step_id)
    if need_xml:
        xml_path = run_dir / "xml" / f"{step_id}.xml"
        xml_text = adb.dump_xml(xml_path)
        return {"screenshot": "", "xml": xml_path, "xml_text": xml_text}
    return {"screenshot": "", "xml": "", "xml_text": ""}


def clear_android_permission_dialogs(
    *,
    adb: ADB,
    run_dir: Path,
    step_id: str,
    screen: dict[str, Any],
    dry_run: bool,
    wait_seconds: float,
    max_dialogs: int = 2,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []
    current_screen = screen
    permission_packages = {"com.android.permissioncontroller", "com.google.android.permissioncontroller"}
    allow_selector = {
        "resource_id": [
            "com.android.permissioncontroller:id/permission_allow_button",
            "com.google.android.permissioncontroller:id/permission_allow_button",
        ],
        "class": "android.widget.Button",
        "clickable": "true",
        "enabled": "true",
    }

    if not str(current_screen.get("xml_text") or ""):
        current_screen = capture(adb, run_dir, f"{step_id}_permission_check")

    for index in range(1, max_dialogs + 1):
        xml_text = str(current_screen.get("xml_text") or "")
        packages = set(xml_packages(xml_text))
        if not packages.intersection(permission_packages):
            break

        allow_button = first_matching_node_center(xml_text, allow_selector)
        if not allow_button:
            events.append(
                {
                    "dialog": index,
                    "action": "permission_dialog_detected_no_allow_button",
                    "packages": sorted(packages),
                    "xml": str(current_screen.get("xml") or ""),
                    "screenshot": str(current_screen.get("screenshot") or ""),
                }
            )
            break

        event = {
            "dialog": index,
            "action": "tap_permission_allow",
            "packages": sorted(packages),
            "xml": str(current_screen.get("xml") or ""),
            "screenshot": str(current_screen.get("screenshot") or ""),
            **allow_button,
        }
        events.append(event)
        if dry_run:
            break

        adb.tap(int(allow_button["x"]), int(allow_button["y"]))
        time.sleep(wait_seconds)
        current_screen = capture(adb, run_dir, f"{step_id}_after_permission_{index:02d}")
        event["after_xml"] = str(current_screen.get("xml") or "")
        event["after_screenshot"] = str(current_screen.get("screenshot") or "")

    return current_screen, events


def capture_failure(adb: ADB, run_dir: Path, step_id: str, context: dict[str, Any], trace: dict[str, Any]) -> None:
    if str(context.get("capture_mode") or "all") == "none":
        return
    try:
        screen = capture(adb, run_dir, f"{step_id}_error")
    except Exception as capture_exc:  # noqa: BLE001
        trace["error_capture_failed"] = str(capture_exc)
        return
    trace["error_screenshot"] = str(screen["screenshot"])
    trace["error_xml"] = str(screen["xml"])
    trace["error_xml_packages"] = xml_packages(str(screen.get("xml_text") or ""))
    try:
        trace["error_foreground_package"] = adb.foreground_package()
    except Exception as exc:  # noqa: BLE001
        trace["error_foreground_package_error"] = str(exc)


def ensure_camera_video_mode_before_gallery(
    *,
    adb: ADB,
    run_dir: Path,
    step: dict[str, Any],
    screen: dict[str, Any],
    dry_run: bool,
    wait_scale: float,
) -> dict[str, Any]:
    xml_text = str(screen.get("xml_text") or "")
    mode_texts = clean_texts(step.get("camera_video_mode_texts")) or [
        "60 \u79d2",
        "60\u79d2",
        "15 \u79d2",
        "15\u79d2",
        "60s",
        "15s",
    ]
    if not xml_has_any_text(xml_text, ["\u7167\u7247", "Photo"], match_mode="contains"):
        return {"enabled": True, "tapped": False, "reason": "photo_mode_not_visible"}
    tap = first_text_match_center(xml_text, mode_texts, match_mode="contains")
    if not tap:
        return {"enabled": True, "tapped": False, "reason": "video_mode_text_not_found", "mode_texts": mode_texts}
    if not dry_run:
        adb.tap(int(tap["x"]), int(tap["y"]))
        time.sleep(float(step.get("wait_after_video_mode_seconds") or 1.0) * wait_scale)
    return {"enabled": True, "tapped": True, "mode_texts": mode_texts, "tap": tap}


def paddle_ocr_optional_gate(
    *,
    adb: ADB,
    run_dir: Path,
    step_id: str,
    step: dict[str, Any],
    screen: dict[str, Any],
    trace: dict[str, Any],
) -> dict[str, Any]:
    presence_texts = clean_texts(step.get("ocr_presence_texts") or step.get("ocr_texts") or step.get("texts") or [])
    confirm_texts = clean_texts(step.get("ocr_confirm_texts") or [])
    trace["ocr_provider"] = "paddle_ocr"
    trace["ocr_texts"] = presence_texts
    trace["ocr_presence_texts"] = presence_texts
    trace["ocr_confirm_texts"] = confirm_texts
    if not presence_texts:
        trace["ocr_found"] = False
        trace["action"] = "fail_ocr_error"
        trace["ocr_reason"] = "No OCR presence texts configured."
        raise WorkflowError(f"{step_id}: required PaddleOCR gate has no presence texts configured.")

    screenshot = screen.get("screenshot")
    if not screenshot:
        screenshot = adb.screenshot(run_dir / "screenshots" / f"{step_id}_ocr.png")
        trace["ocr_screenshot"] = str(screenshot)
    else:
        trace["ocr_screenshot"] = str(screenshot)

    config = {
        "recognition": {
            "provider": "paddle_ocr",
            "paddle": {
                "lang": str(step.get("ocr_lang") or "ch"),
                "ocr_version": str(step.get("ocr_version") or "PP-OCRv4"),
                "device": str(step.get("ocr_device") or "cpu"),
                "use_gpu": bool(step.get("ocr_use_gpu", False)),
                "use_angle_cls": bool(step.get("ocr_use_angle_cls", False)),
                "show_log": bool(step.get("ocr_show_log", False)),
                "enable_mkldnn": bool(step.get("ocr_enable_mkldnn", False)),
                "min_confidence": float(step.get("ocr_min_confidence", 0.0) or 0.0),
                "text_rec_score_thresh": float(step.get("ocr_text_rec_score_thresh", 0.3) or 0.3),
            },
        },
    }
    try:
        finder = PaddleOCRButtonFinder(config)
        candidates = finder._detect_candidates(Path(screenshot))
    except Exception as exc:  # noqa: BLE001
        trace["ocr_found"] = False
        trace["action"] = "fail_ocr_error"
        trace["ocr_error"] = str(exc)
        raise WorkflowError(f"{step_id}: required PaddleOCR failed: {exc}") from exc

    trace["ocr_candidate_count"] = len(candidates)
    trace["ocr_candidate_preview"] = ocr_candidate_preview(candidates, limit=20)

    match_mode = str(step.get("ocr_match_mode") or "contains")
    min_confidence = float(step.get("ocr_min_confidence", 0.0) or 0.0)
    presence_match = first_ocr_text_match(
        candidates,
        presence_texts,
        match_mode=match_mode,
        min_confidence=min_confidence,
    )
    if not presence_match:
        if step_id == "optional_confirm_add_product":
            popup_tap = optional_add_product_confirm_popup_tap(
                candidates=candidates,
                screenshot_path=screenshot,
                xml_text=str(screen.get("xml_text") or ""),
                min_confidence=min_confidence,
            )
            if popup_tap:
                trace.update(
                    {
                        "ocr_found": True,
                        "ocr_structural_popup_found": True,
                        "ocr_structural_popup_reason": popup_tap["reason"],
                        "ocr_structural_cancel_y": popup_tap["cancel_y"],
                    }
                )
                return {
                    "present": True,
                    "tap": {
                        "x": int(popup_tap["x"]),
                        "y": int(popup_tap["y"]),
                        "matched_text": "structural_add_product_confirm",
                        "candidate_text": str(popup_tap.get("candidate_text") or ""),
                        "confidence": float(popup_tap.get("confidence") or 0.0),
                    },
                }
        trace["ocr_found"] = False
        return {"present": False}
    presence_candidate, matched_text = presence_match
    trace.update(
        {
            "ocr_found": True,
            "ocr_matched_text": matched_text,
            "ocr_candidate_text": str(getattr(presence_candidate, "text", "")),
            "ocr_candidate_confidence": float(getattr(presence_candidate, "confidence", 0.0) or 0.0),
            "ocr_candidate_x": int(getattr(presence_candidate, "x", 0) or 0),
            "ocr_candidate_y": int(getattr(presence_candidate, "y", 0) or 0),
        }
    )

    button_match = None
    if confirm_texts:
        confirm_match_mode = str(step.get("ocr_confirm_match_mode") or "exact")
        trace["ocr_confirm_match_mode"] = confirm_match_mode
        button_match = first_ocr_text_match(
            candidates,
            confirm_texts,
            match_mode=confirm_match_mode,
            min_confidence=min_confidence,
            min_y=int(getattr(presence_candidate, "y", 0) or 0),
        )
    if button_match:
        button_candidate, button_text = button_match
        return {
            "present": True,
            "tap": {
                "x": int(getattr(button_candidate, "x", 0) or 0),
                "y": int(getattr(button_candidate, "y", 0) or 0),
                "matched_text": str(button_text),
                "candidate_text": str(getattr(button_candidate, "text", "")),
                "confidence": float(getattr(button_candidate, "confidence", 0.0) or 0.0),
            },
        }

    trace["ocr_confirm_found"] = False
    if bool(step.get("ocr_allow_calibrated_point_fallback", True)):
        return {"present": True}
    trace["action"] = "fail_ocr_error"
    raise WorkflowError(f"{step_id}: optional popup was found by OCR, but confirm button OCR text was not found.")


def optional_add_product_confirm_popup_tap(
    *,
    candidates: list[Any],
    screenshot_path: Any,
    xml_text: str = "",
    min_confidence: float,
) -> dict[str, Any] | None:
    try:
        width, height = png_size(screenshot_path)
    except Exception:  # noqa: BLE001
        width, height = 0, 0
    if width <= 0 or height <= 0:
        return None

    xml_tap = optional_add_product_confirm_popup_tap_from_xml(xml_text, screen_width=width, screen_height=height)
    if xml_tap:
        return xml_tap

    center_x = width / 2
    centered: list[Any] = []
    for candidate in candidates:
        confidence = float(getattr(candidate, "confidence", 0.0) or 0.0)
        if confidence < max(0.75, min_confidence):
            continue
        x = int(getattr(candidate, "x", 0) or 0)
        y = int(getattr(candidate, "y", 0) or 0)
        left = int(getattr(candidate, "left", 0) or 0)
        right = int(getattr(candidate, "right", 0) or 0)
        if abs(x - center_x) > width * 0.12:
            continue
        if y < height * 0.45 or y > height * 0.68:
            continue
        if right <= left or (right - left) > width * 0.28:
            continue
        centered.append(candidate)

    centered.sort(key=lambda item: int(getattr(item, "y", 0) or 0))
    for upper, lower in zip(centered, centered[1:]):
        upper_y = int(getattr(upper, "y", 0) or 0)
        lower_y = int(getattr(lower, "y", 0) or 0)
        upper_x = int(getattr(upper, "x", 0) or 0)
        lower_x = int(getattr(lower, "x", 0) or 0)
        gap = lower_y - upper_y
        if not (height * 0.035 <= gap <= height * 0.085):
            continue
        if abs(upper_x - lower_x) > width * 0.08:
            continue
        return {
            "x": upper_x,
            "y": upper_y,
            "cancel_y": lower_y,
            "candidate_text": str(getattr(upper, "text", "")),
            "confidence": float(getattr(upper, "confidence", 0.0) or 0.0),
            "reason": "centered_two_button_add_cancel_popup",
        }
    return None


def optional_add_product_confirm_popup_tap_from_xml(
    xml_text: str,
    *,
    screen_width: int,
    screen_height: int,
) -> dict[str, Any] | None:
    if not xml_text:
        return None
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None

    root_bounds = parse_bounds(root.attrib.get("bounds", ""))
    if root_bounds:
        screen_width = max(screen_width, root_bounds[2])
        screen_height = max(screen_height, root_bounds[3])
    if screen_width <= 0 or screen_height <= 0:
        return None

    all_bounds: list[tuple[int, int, int, int]] = []
    for node in root.iter("node"):
        bounds = parse_bounds(node.attrib.get("bounds", ""))
        if bounds:
            all_bounds.append(bounds)

    center_x = screen_width / 2
    candidate_popups: list[tuple[int, tuple[int, int, int, int]]] = []
    for bounds in all_bounds:
        left, top, right, bottom = bounds
        popup_width = right - left
        popup_height = bottom - top
        if popup_width <= 0 or popup_height <= 0:
            continue
        if not (screen_width * 0.45 <= popup_width <= screen_width * 0.9):
            continue
        if not (screen_height * 0.12 <= popup_height <= screen_height * 0.35):
            continue
        if abs(((left + right) / 2) - center_x) > screen_width * 0.12:
            continue
        if top < screen_height * 0.25 or bottom > screen_height * 0.82:
            continue

        dividers: list[int] = []
        for divider_bounds in all_bounds:
            d_left, d_top, d_right, d_bottom = divider_bounds
            divider_width = d_right - d_left
            divider_height = d_bottom - d_top
            if divider_height > 3 or divider_width < popup_width * 0.75:
                continue
            if d_top <= top or d_bottom >= bottom:
                continue
            if d_left > left + 20 or d_right < right - 20:
                continue
            dividers.append((d_top + d_bottom) // 2)
        dividers = sorted(set(dividers))
        if len(dividers) < 2:
            continue

        button_y = (dividers[0] + dividers[1]) // 2
        if not (top < button_y < bottom):
            continue
        candidate_popups.append((popup_width * popup_height, bounds))

    if not candidate_popups:
        return None

    _, popup_bounds = max(candidate_popups, key=lambda item: item[0])
    left, top, right, bottom = popup_bounds
    dividers = []
    popup_width = right - left
    for divider_bounds in all_bounds:
        d_left, d_top, d_right, d_bottom = divider_bounds
        divider_width = d_right - d_left
        divider_height = d_bottom - d_top
        if divider_height <= 3 and divider_width >= popup_width * 0.75 and top < d_top and d_bottom < bottom:
            if d_left <= left + 20 and d_right >= right - 20:
                dividers.append((d_top + d_bottom) // 2)
    dividers = sorted(set(dividers))
    if len(dividers) < 2:
        return None
    return {
        "x": (left + right) // 2,
        "y": (dividers[0] + dividers[1]) // 2,
        "cancel_y": (dividers[1] + bottom) // 2,
        "candidate_text": "",
        "confidence": 1.0,
        "reason": "xml_centered_add_cancel_popup",
    }


def clean_texts(values: Any) -> list[str]:
    return [str(text) for text in (values or []) if str(text or "").strip()]


def ocr_candidate_preview(candidates: list[Any], *, limit: int = 20) -> list[dict[str, Any]]:
    preview: list[dict[str, Any]] = []
    for candidate in candidates[:limit]:
        preview.append(
            {
                "text": str(getattr(candidate, "text", "")),
                "confidence": float(getattr(candidate, "confidence", 0.0) or 0.0),
                "x": int(getattr(candidate, "x", 0) or 0),
                "y": int(getattr(candidate, "y", 0) or 0),
                "left": int(getattr(candidate, "left", 0) or 0),
                "top": int(getattr(candidate, "top", 0) or 0),
                "right": int(getattr(candidate, "right", 0) or 0),
                "bottom": int(getattr(candidate, "bottom", 0) or 0),
            }
        )
    return preview


def first_ocr_text_match(
    candidates: list[Any],
    texts: list[str],
    *,
    match_mode: str = "contains",
    min_confidence: float = 0.0,
    min_y: int | None = None,
    max_y: int | None = None,
) -> tuple[Any, str] | None:
    for candidate in candidates:
        confidence = float(getattr(candidate, "confidence", 0.0) or 0.0)
        if confidence < min_confidence:
            continue
        candidate_y = int(getattr(candidate, "y", 0) or 0)
        if min_y is not None and candidate_y < min_y:
            continue
        if max_y is not None and candidate_y > max_y:
            continue
        candidate_text = normalize(str(getattr(candidate, "text", "")))
        if not candidate_text:
            continue
        for text in texts:
            target = normalize(text)
            if not target:
                continue
            if match_mode == "exact" and candidate_text == target:
                return candidate, str(text)
            if match_mode != "exact" and target in candidate_text:
                return candidate, str(text)
    return None


DEFAULT_PRODUCT_ADD_TEXTS = ["添加", "Add"]
DEFAULT_PRODUCT_ALREADY_ADDED_TEXTS = ["已添加", "Added"]
DEFAULT_PRODUCT_BOTTOM_TEXTS = [
    "No more",
    "No more results",
    "You've reached the end",
    "没有更多",
    "已到底",
    "暂无更多",
]
DEFAULT_ADD_MORE_PRODUCTS_TEXTS = [
    "添加更多商品",
    "添加更多",
    "Add more products",
    "Add more",
]
DEFAULT_PRODUCT_CONFIRM_PRESENCE_TEXTS = [
    "添加商品？",
    "添加商品?",
    "是否添加商品",
    "确认添加",
    "Add product?",
    "Add product",
]
DEFAULT_TIKTOK_SHOP_TEXTS = ["TikTok Shop", "Shop", "商城"]
DEFAULT_SHOWCASE_TEXTS = ["Your showcase", "From showcase", "Showcase", "我的橱窗", "橱窗"]
DEFAULT_SHOP_SEARCH_TEXTS = ["搜索商品", "搜索", "Search products", "Search"]


def ensure_showcase_product_attached(
    *,
    adb: ADB,
    profile: dict[str, Any],
    step: dict[str, Any],
    context: dict[str, Any],
    run_dir: Path,
    dry_run: bool,
    initial_screen: dict[str, Any],
) -> dict[str, Any]:
    target_text = str(context.get("product_search_title") or "").strip()
    if not target_text:
        raise WorkflowError("ensure_showcase_product_attached: product_search_title is required.")

    match_prefix_chars = product_match_prefix_chars(step)
    target_key = product_match_key(target_text, match_prefix_chars=match_prefix_chars)
    if not target_key:
        raise WorkflowError("ensure_showcase_product_attached: normalized product_search_title is empty.")

    trace: dict[str, Any] = {
        "target_text": target_text,
        "target_key": target_key,
        "match_prefix_chars": match_prefix_chars,
        "showcase": {},
        "shop": {},
    }
    expected_package = app_package(profile, str(step.get("required_app") or "tiktok"))

    showcase_result = search_visible_product_list(
        adb=adb,
        run_dir=run_dir,
        step=step,
        area="showcase",
        target_key=target_key,
        dry_run=dry_run,
        initial_screen=initial_screen,
        max_swipes=int(step.get("max_showcase_swipes") or 8),
        expected_package=expected_package,
    )
    showcase_last_screen = dict(showcase_result.pop("_last_screen", {}) or {})
    trace["showcase"] = showcase_result
    if showcase_result.get("status") == "matched":
        return {**trace, "result": "attached_from_showcase"}
    if dry_run:
        raise WorkflowError(
            "ensure_showcase_product_attached: product was not found in current showcase during dry-run; "
            "TikTok Shop supplement requires live navigation."
        )

    supplement_result = add_product_from_tiktok_shop(
        adb=adb,
        profile=profile,
        step=step,
        context=context,
        run_dir=run_dir,
        target_key=target_key,
        starting_screen=showcase_last_screen,
    )
    trace["shop"] = supplement_result
    if supplement_result.get("result") == "selected_from_tiktok_shop":
        trace["result"] = "selected_from_tiktok_shop"
        return trace

    verify_screen = capture(adb, run_dir, "ensure_showcase_product_attached_showcase_verify_start")
    verify_result = search_visible_product_list(
        adb=adb,
        run_dir=run_dir,
        step=step,
        area="showcase_verify",
        target_key=target_key,
        dry_run=dry_run,
        initial_screen=verify_screen,
        max_swipes=int(step.get("max_showcase_swipes") or 8),
        expected_package=expected_package,
    )
    verify_result.pop("_last_screen", None)
    trace["showcase_verify"] = verify_result
    if verify_result.get("status") != "matched":
        raise WorkflowError(
            "ensure_showcase_product_attached: product was added from TikTok Shop but could not be verified "
            "and attached from the showcase list."
        )
    trace["result"] = "attached_after_shop_supplement"
    return trace


def add_product_from_tiktok_shop(
    *,
    adb: ADB,
    profile: dict[str, Any],
    step: dict[str, Any],
    context: dict[str, Any],
    run_dir: Path,
    target_key: str,
    starting_screen: dict[str, Any],
) -> dict[str, Any]:
    trace: dict[str, Any] = {"action": "add_product_from_tiktok_shop"}
    add_more = tap_text_or_configured_point(
        adb=adb,
        profile=profile,
        run_dir=run_dir,
        step_id="ensure_showcase_product_attached_add_more_products",
        screen=starting_screen,
        texts=product_texts_with_defaults(step.get("add_more_products_texts"), DEFAULT_ADD_MORE_PRODUCTS_TEXTS),
        point_name=str(step.get("add_more_products_point") or "add_more_products_entry"),
        point_ratio=step.get("add_more_products_point_ratio") or {"x_ratio": 0.5, "y_ratio": 0.935},
        reason="Open Add more products.",
    )
    trace["add_more_products"] = add_more
    time.sleep(float(step.get("wait_after_tap_seconds") or 1.5) * float(context.get("wait_scale") or 1.0))

    shop_screen = capture(adb, run_dir, "ensure_showcase_product_attached_shop_tab")
    shop_tab = tap_text_or_configured_point(
        adb=adb,
        profile=profile,
        run_dir=run_dir,
        step_id="ensure_showcase_product_attached_tiktok_shop_tab",
        screen=shop_screen,
        texts=product_texts_with_defaults(step.get("tiktok_shop_tab_texts"), DEFAULT_TIKTOK_SHOP_TEXTS),
        point_name=str(step.get("tiktok_shop_tab_point") or "tiktok_shop_tab"),
        point_ratio=step.get("tiktok_shop_tab_point_ratio") or {"x_ratio": 0.25, "y_ratio": 0.935},
        reason="Open TikTok Shop tab.",
    )
    trace["tiktok_shop_tab"] = shop_tab
    time.sleep(float(step.get("wait_after_tap_seconds") or 1.5) * float(context.get("wait_scale") or 1.0))

    trace["shop_scan_mode"] = "direct_list_scan_without_search"

    shop_result = search_visible_product_list(
        adb=adb,
        run_dir=run_dir,
        step=step,
        area="tiktok_shop",
        target_key=target_key,
        dry_run=False,
        initial_screen=capture(adb, run_dir, "ensure_showcase_product_attached_shop_scan_start"),
        max_swipes=int(step.get("max_shop_swipes") or 8),
        expected_package=app_package(profile, str(step.get("required_app") or "tiktok")),
    )
    shop_result.pop("_last_screen", None)
    trace["shop_search"] = shop_result
    if shop_result.get("status") not in {"matched", "already_added"}:
        reason = str(shop_result.get("reason") or shop_result.get("status") or "not_found")
        raise WorkflowError(f"ensure_showcase_product_attached: target product was not found in TikTok Shop; reason={reason}.")

    if shop_result.get("status") == "matched":
        time.sleep(float(step.get("wait_after_tap_seconds") or 1.5) * float(context.get("wait_scale") or 1.0))
        confirm_trace = confirm_shop_add_dialog_if_present(
            adb=adb,
            profile=profile,
            run_dir=run_dir,
            step=step,
            wait_scale=float(context.get("wait_scale") or 1.0),
        )
        trace["shop_confirm_add"] = confirm_trace
        time.sleep(float(step.get("wait_after_shop_add_seconds") or 2.0) * float(context.get("wait_scale") or 1.0))
        post_add_screen = capture(adb, run_dir, "ensure_showcase_product_attached_shop_post_add")
        expected_package = app_package(profile, str(step.get("required_app") or "tiktok"))
        post_add_state = classify_shop_post_add_screen(
            screen=post_add_screen,
            add_texts=product_texts_with_defaults(step.get("add_button_texts"), DEFAULT_PRODUCT_ADD_TEXTS),
            already_added_texts=product_texts_with_defaults(step.get("already_added_texts"), DEFAULT_PRODUCT_ALREADY_ADDED_TEXTS),
            expected_package=expected_package,
        )
        trace["shop_post_add"] = post_add_state
        if post_add_state["status"] == "ordinary_search_results":
            raise WorkflowError(
                "ensure_showcase_product_attached: TikTok Shop add landed on ordinary TikTok search results; "
                "refusing to continue."
            )
        if post_add_state["status"] == "outside_expected_app":
            foreground_package = adb.foreground_package()
            trace["shop_post_add"]["foreground_package"] = foreground_package
            raise WorkflowError(
                "ensure_showcase_product_attached: TikTok Shop add left TikTok; "
                f"foreground_package={foreground_package or 'unknown'}."
            )
        if post_add_state["status"] == "product_list":
            return_trace = return_to_showcase_product_list(
                adb=adb,
                profile=profile,
                step=step,
                run_dir=run_dir,
                wait_scale=float(context.get("wait_scale") or 1.0),
            )
            trace["return_to_showcase"] = return_trace
            trace["result"] = "returned_to_showcase_after_shop_add"
            return trace
        trace["result"] = "selected_from_tiktok_shop"
        return trace

    trace["shop_confirm_add"] = {"status": "skipped", "reason": "product_already_added_in_shop"}
    return_trace = return_to_showcase_product_list(
        adb=adb,
        profile=profile,
        step=step,
        run_dir=run_dir,
        wait_scale=float(context.get("wait_scale") or 1.0),
    )
    trace["return_to_showcase"] = return_trace
    trace["result"] = "returned_to_showcase_after_already_added"
    return trace


def classify_shop_post_add_screen(
    *,
    screen: dict[str, Any],
    add_texts: list[str],
    already_added_texts: list[str],
    expected_package: str,
) -> dict[str, Any]:
    xml_text = str(screen.get("xml_text") or "")
    packages = xml_packages(xml_text) if xml_text else []
    result = {
        "status": "unknown",
        "screenshot": str(screen.get("screenshot") or ""),
        "xml": str(screen.get("xml") or ""),
        "packages": packages,
        "action_button_count": 0,
    }
    if expected_package and packages and expected_package not in packages:
        result["status"] = "outside_expected_app"
        return result
    if product_publish_name_page_visible(xml_text, add_texts):
        result["status"] = "product_publish_name_page"
        return result
    if ordinary_tiktok_search_results_visible(xml_text):
        result["status"] = "ordinary_search_results"
        return result
    action_button_count = product_action_button_count(xml_text, add_texts, already_added_texts)
    result["action_button_count"] = action_button_count
    if action_button_count > 0:
        result["status"] = "product_list"
        return result
    return result


def product_publish_name_page_visible(xml_text: str, add_texts: list[str]) -> bool:
    if not xml_text:
        return False
    if xml_has_any_text(xml_text, ["\u5546\u54c1\u540d", "Product name", "Add product name"], match_mode="contains"):
        return True
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return False
    screen_left, screen_top, screen_right, screen_bottom = root_bounds(root)
    screen_width = max(1, screen_right - screen_left)
    screen_height = max(1, screen_bottom - screen_top)
    has_name_edit = False
    has_bottom_add = False
    for node in root.iter("node"):
        bounds = parse_bounds(node.attrib.get("bounds", ""))
        if not bounds:
            continue
        left, top, right, bottom = bounds
        if node.attrib.get("class") == "android.widget.EditText":
            text_value = str(node.attrib.get("text") or "").strip()
            focused = node.attrib.get("focused") == "true"
            below_header = top >= screen_top + int(screen_height * 0.25)
            if text_value and (focused or below_header):
                has_name_edit = True
        if not product_button_node_matches(node, add_texts, []):
            continue
        button_width = max(0, right - left)
        near_bottom = top >= screen_top + int(screen_height * 0.75)
        wide_button = button_width >= int(screen_width * 0.55)
        if near_bottom and wide_button:
            has_bottom_add = True
    return has_name_edit and has_bottom_add


def ordinary_tiktok_search_results_visible(xml_text: str) -> bool:
    if not xml_text:
        return False
    search_tab_texts = [
        "\u7efc\u5408",
        "\u7528\u6237",
        "\u8d2d\u7269",
        "\u89c6\u9891",
        "\u8bdd\u9898\u6807\u7b7e",
        "\u76f4\u64ad",
        "Top",
        "Users",
        "Shop",
        "Videos",
        "LIVE",
    ]
    values = xml_text_values(xml_text)
    normalized_values = {normalize(value) for value in values if normalize(value)}
    tab_hits = 0
    for text in search_tab_texts:
        needle = normalize(text)
        if any(needle and needle in value for value in normalized_values):
            tab_hits += 1
    return tab_hits >= 4


def optional_remove_added_music(
    *,
    adb: ADB,
    run_dir: Path,
    step_id: str,
    step: dict[str, Any],
    context: dict[str, Any],
    screen: dict[str, Any],
    dry_run: bool,
) -> dict[str, Any]:
    xml_text = str(screen.get("xml_text") or "")
    close_tap = added_music_close_tap(xml_text, step)
    if not close_tap:
        return {"status": "absent", "removed": False}

    result: dict[str, Any] = {"status": "present", "removed": False, "close_tap": close_tap}
    if dry_run:
        result["dry_run"] = True
        return result

    adb.tap(int(close_tap["x"]), int(close_tap["y"]))
    wait_seconds = float(step.get("wait_after_tap_seconds") or 0.8) * float(context.get("wait_scale") or 1.0)
    if wait_seconds:
        time.sleep(wait_seconds)
    after_screen = capture(adb, run_dir, f"{step_id}_after_remove")
    result.update(
        {
            "removed": True,
            "after_tap_screenshot": str(after_screen.get("screenshot") or ""),
            "after_tap_xml": str(after_screen.get("xml") or ""),
            "still_present_after_tap": bool(added_music_close_tap(str(after_screen.get("xml_text") or ""), step)),
        }
    )
    return result


def added_music_close_tap(xml_text: str, step: dict[str, Any] | None = None) -> dict[str, Any] | None:
    if not xml_text:
        return None
    step = step or {}
    close_texts = [normalize(text) for text in clean_texts(step.get("close_texts")) + ["\u5173\u95ed", "Close"]]
    music_texts = [
        normalize(text)
        for text in clean_texts(step.get("music_texts"))
        + ["\u539f\u58f0", "Original sound", "original sound", "music", "Music", "sound", "Sound"]
    ]
    add_music_texts = [
        normalize(text)
        for text in clean_texts(step.get("add_music_texts")) + ["\u6dfb\u52a0\u97f3\u4e50", "Add music", "Add sound"]
    ]
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None

    screen_left, screen_top, screen_right, screen_bottom = root_bounds(root)
    screen_width = max(1, screen_right - screen_left)
    screen_height = max(1, screen_bottom - screen_top)
    min_x = screen_left + int(screen_width * float(step.get("min_x_ratio") or 0.25))
    max_x = screen_left + int(screen_width * float(step.get("max_x_ratio") or 0.85))
    max_y = screen_top + int(screen_height * float(step.get("top_y_max_ratio") or 0.18))

    music_labels: list[dict[str, Any]] = []
    close_candidates: list[dict[str, Any]] = []
    for node in root.iter("node"):
        bounds = parse_bounds(node.attrib.get("bounds", ""))
        if not bounds:
            continue
        left, top, right, bottom = bounds
        if right <= left or bottom <= top:
            continue
        center_x = (left + right) // 2
        center_y = (top + bottom) // 2
        if center_y > max_y:
            continue
        values = [node.attrib.get("text", ""), node.attrib.get("content-desc", "")]
        normalized_values = [normalize(value) for value in values if normalize(value)]
        if not normalized_values:
            continue

        is_add_music = any(any(add_text and add_text in value for add_text in add_music_texts) for value in normalized_values)
        if not is_add_music and any(
            any(music_text and music_text in value for music_text in music_texts) for value in normalized_values
        ):
            music_labels.append(
                {
                    "text": node.attrib.get("text", ""),
                    "content_desc": node.attrib.get("content-desc", ""),
                    "bounds": [left, top, right, bottom],
                    "center_x": center_x,
                    "center_y": center_y,
                }
            )
        if min_x <= center_x <= max_x and any(value in close_texts for value in normalized_values):
            close_candidates.append(
                {
                    "x": center_x,
                    "y": center_y,
                    "bounds": [left, top, right, bottom],
                    "matched_text": node.attrib.get("text", ""),
                    "matched_content_desc": node.attrib.get("content-desc", ""),
                    "matched_resource_id": node.attrib.get("resource-id", ""),
                    "matched_class": node.attrib.get("class", ""),
                }
            )

    if not music_labels or not close_candidates:
        return None
    for label in music_labels:
        label_left, label_top, label_right, label_bottom = label["bounds"]
        for close in close_candidates:
            close_left, close_top, close_right, close_bottom = close["bounds"]
            vertically_aligned = not (close_bottom < label_top or close_top > label_bottom)
            near_label = close_left >= label_left and close_left <= label_right + int(screen_width * 0.18)
            if vertically_aligned and near_label:
                return {**close, "matched_music_label": label}
    close = sorted(close_candidates, key=lambda item: item["x"], reverse=True)[0]
    return {**close, "matched_music_label": music_labels[0], "match_reason": "top_music_region"}


def confirm_shop_add_dialog_if_present(
    *,
    adb: ADB,
    profile: dict[str, Any],
    run_dir: Path,
    step: dict[str, Any],
    wait_scale: float,
) -> dict[str, Any]:
    screen = capture(adb, run_dir, "ensure_showcase_product_attached_shop_confirm_add")
    point = configured_point_or_ratio(
        profile,
        str(step.get("shop_confirm_point") or "product_optional_confirm_add"),
        step.get("shop_confirm_point_ratio") or {"x_ratio": 0.5, "y_ratio": 0.5392},
        screen,
    )
    ocr_step = {
        "ocr_presence_texts": product_texts_with_defaults(
            step.get("shop_confirm_presence_texts"),
            DEFAULT_PRODUCT_CONFIRM_PRESENCE_TEXTS,
        ),
        "ocr_confirm_texts": product_texts_with_defaults(
            step.get("shop_confirm_confirm_texts"),
            [*DEFAULT_PRODUCT_ADD_TEXTS, "Confirm add", "Add"],
        ),
        "ocr_confirm_match_mode": "exact",
        "ocr_match_mode": "contains",
        "ocr_allow_calibrated_point_fallback": True,
        "ocr_min_confidence": float(step.get("shop_confirm_ocr_min_confidence") or 0.3),
        "ocr_text_rec_score_thresh": float(step.get("ocr_text_rec_score_thresh") or 0.3),
    }
    ocr_trace: dict[str, Any] = {}
    modal_visible = centered_modal_dialog_visible(str(screen.get("xml_text") or ""))
    try:
        gate = paddle_ocr_optional_gate(
            adb=adb,
            run_dir=run_dir,
            step_id="ensure_showcase_product_attached_shop_confirm_add",
            step=ocr_step,
            screen=screen,
            trace=ocr_trace,
        )
    except Exception as exc:  # noqa: BLE001
        gate = {"present": False}
        ocr_trace["ocr_error"] = str(exc)
    if gate.get("present") or modal_visible:
        dynamic_tap = gate.get("tap")
        dynamic_method = "ocr_button"
        if not dynamic_tap:
            popup_tap = optional_add_product_confirm_popup_tap(
                candidates=[],
                screenshot_path=screen.get("screenshot"),
                xml_text=str(screen.get("xml_text") or ""),
                min_confidence=float(ocr_step["ocr_min_confidence"]),
            )
            if popup_tap:
                dynamic_tap = popup_tap
                dynamic_method = str(popup_tap.get("reason") or "dynamic_popup_geometry")
        tap_point = dynamic_tap or point
        adb.tap(int(tap_point["x"]), int(tap_point["y"]))
        time.sleep(float(step.get("wait_after_tap_seconds") or 1.5) * wait_scale)
        after_screen = capture(adb, run_dir, "ensure_showcase_product_attached_shop_confirm_add_after_tap")
        after_xml = str(after_screen.get("xml_text") or "")
        if centered_modal_dialog_visible(after_xml) and not product_publish_name_page_visible(
            after_xml,
            product_texts_with_defaults(step.get("add_button_texts"), DEFAULT_PRODUCT_ADD_TEXTS),
        ):
            raise WorkflowError(
                "ensure_showcase_product_attached: shop add confirmation dialog is still visible after tapping confirm."
            )
        return {
            "status": "confirmed",
            "method": dynamic_method if dynamic_tap else ("ocr_presence_fixed_point" if gate.get("present") else "centered_modal_fixed_point"),
            "screenshot": str(screen.get("screenshot") or ""),
            "xml": str(screen.get("xml") or ""),
            "after_tap_screenshot": str(after_screen.get("screenshot") or ""),
            "after_tap_xml": str(after_screen.get("xml") or ""),
            "modal_visible": modal_visible,
            "used_dynamic_tap": bool(dynamic_tap),
            "fallback_point": point,
            "x": int(tap_point["x"]),
            "y": int(tap_point["y"]),
            "ocr": ocr_trace,
        }
    return {
        "status": "absent",
        "screenshot": str(screen.get("screenshot") or ""),
        "xml": str(screen.get("xml") or ""),
        "modal_visible": modal_visible,
        "ocr": ocr_trace,
    }


def search_visible_product_list(
    *,
    adb: ADB,
    run_dir: Path,
    step: dict[str, Any],
    area: str,
    target_key: str,
    dry_run: bool,
    initial_screen: dict[str, Any],
    max_swipes: int,
    expected_package: str = "",
) -> dict[str, Any]:
    min_confidence = float(step.get("ocr_min_confidence", 0.85) or 0.85)
    same_screen_limit = int(step.get("same_screen_limit") or 2)
    add_texts = product_texts_with_defaults(step.get("add_button_texts"), DEFAULT_PRODUCT_ADD_TEXTS)
    already_added_texts = product_texts_with_defaults(step.get("already_added_texts"), DEFAULT_PRODUCT_ALREADY_ADDED_TEXTS)
    bottom_texts = product_texts_with_defaults(step.get("bottom_texts"), DEFAULT_PRODUCT_BOTTOM_TEXTS)
    add_more_texts = product_texts_with_defaults(step.get("add_more_products_texts"), DEFAULT_ADD_MORE_PRODUCTS_TEXTS)
    seen_titles: set[str] = set()
    previous_signature = ""
    same_screen_count = 0
    attempts: list[dict[str, Any]] = []
    screen = ensure_screen(adb, run_dir, f"ensure_showcase_product_attached_{area}_scan_00", initial_screen)
    ensure_product_scan_screen_in_expected_package(
        adb=adb,
        screen=screen,
        expected_package=expected_package,
        area=area,
        phase="start",
    )
    screen, ready_attempts = wait_for_product_list_ready(
        adb=adb,
        run_dir=run_dir,
        step=step,
        area=area,
        screen=screen,
        add_texts=add_texts,
        already_added_texts=already_added_texts,
        dry_run=dry_run,
    )
    ensure_product_scan_screen_in_expected_package(
        adb=adb,
        screen=screen,
        expected_package=expected_package,
        area=area,
        phase="ready",
    )
    reset_trace: dict[str, Any] = {"enabled": False, "reason": "disabled"}
    reset_swipes = int(step.get("reset_to_top_swipes") if step.get("reset_to_top_swipes") is not None else 0)
    if reset_swipes > 0 and not dry_run and ready_attempts and ready_attempts[-1].get("ready"):
        reset_trace = reset_product_list_to_top(
            adb=adb,
            run_dir=run_dir,
            step=step,
            area=area,
            screen=screen,
            swipes=reset_swipes,
            expected_package=expected_package,
        )
        screen = dict(reset_trace.pop("_last_screen", screen) or screen)
    elif reset_swipes > 0 and dry_run:
        reset_trace = {"enabled": False, "reason": "dry_run"}
    elif reset_swipes > 0:
        reset_trace = {"enabled": False, "reason": "list_not_ready"}
    setup_trace = {"ready_attempts": ready_attempts, "reset_to_top": reset_trace}
    last_screen_summary: dict[str, Any] = {}
    last_screen_data: dict[str, Any] = {}

    def finish(status: str, **extra: Any) -> dict[str, Any]:
        result = {"status": status, "area": area, **setup_trace, "attempts": attempts, **extra}
        write_product_scan_progress(
            run_dir=run_dir,
            area=area,
            target_key=target_key,
            setup_trace=setup_trace,
            attempts=attempts,
            status=status,
            reason=str(extra.get("reason") or ""),
        )
        return result

    for swipe_index in range(max(0, max_swipes) + 1):
        if swipe_index > 0:
            screen = capture(adb, run_dir, f"ensure_showcase_product_attached_{area}_scan_{swipe_index:02d}")
        ensure_product_scan_screen_in_expected_package(
            adb=adb,
            screen=screen,
            expected_package=expected_package,
            area=area,
            phase=f"scan_{swipe_index:02d}",
        )
        detail_recovery: dict[str, Any] = {"detected": False}
        if product_detail_page_visible(str(screen.get("xml_text") or "")):
            detail_recovery = recover_from_product_detail_page(
                adb=adb,
                run_dir=run_dir,
                area=area,
                swipe_index=swipe_index,
                screen=screen,
                dry_run=dry_run,
                expected_package=expected_package,
            )
            if detail_recovery.get("recovered"):
                screen = dict(detail_recovery.get("_screen") or screen)
            else:
                return finish(
                    "not_product_list",
                    reason="product_detail_page_visible",
                    product_detail_recovery=strip_private_trace(detail_recovery),
                    last_screen={
                        "screenshot": str(screen.get("screenshot") or ""),
                        "xml": str(screen.get("xml") or ""),
                    },
                    _last_screen={**screen, "xml_text": str(screen.get("xml_text") or "")},
                )
        ocr_candidates = detect_product_ocr_candidates(
            screenshot_path=screen.get("screenshot"),
            step=step,
            step_id=f"ensure_showcase_product_attached_{area}_ocr_{swipe_index:02d}",
        )
        cards = extract_product_cards(
            xml_text=str(screen.get("xml_text") or ""),
            ocr_candidates=ocr_candidates,
            add_texts=add_texts,
            already_added_texts=already_added_texts,
            min_ocr_confidence=min_confidence,
        )
        title_wait_attempts: list[dict[str, Any]] = []
        if cards and not product_cards_have_readable_titles(cards) and not dry_run:
            screen, ocr_candidates, cards, title_wait_attempts = wait_for_product_card_titles(
                adb=adb,
                run_dir=run_dir,
                step=step,
                area=area,
                screen=screen,
                cards=cards,
                add_texts=add_texts,
                already_added_texts=already_added_texts,
                min_confidence=min_confidence,
                expected_package=expected_package,
                swipe_index=swipe_index,
            )
        matches = matching_product_cards(cards, target_key)
        current_titles = {str(card.get("normalized_title") or "") for card in cards if card.get("normalized_title")}
        new_titles = sorted(current_titles - seen_titles)
        signature = product_page_signature(cards)
        bottom_found = product_bottom_reached(
            xml_text=str(screen.get("xml_text") or ""),
            ocr_candidates=ocr_candidates,
            bottom_texts=bottom_texts,
            min_ocr_confidence=min_confidence,
        )
        showcase_add_more_visible = area == "showcase" and product_bottom_reached(
            xml_text=str(screen.get("xml_text") or ""),
            ocr_candidates=ocr_candidates,
            bottom_texts=add_more_texts,
            min_ocr_confidence=min_confidence,
        )
        ordinary_search_visible = ordinary_tiktok_search_results_visible(str(screen.get("xml_text") or ""))
        if previous_signature and signature == previous_signature and not new_titles:
            same_screen_count += 1
        else:
            same_screen_count = 0
        seen_titles.update(current_titles)

        attempt = {
            "area": area,
            "swipe_index": swipe_index,
            "screenshot": str(screen.get("screenshot") or ""),
            "xml": str(screen.get("xml") or ""),
            "card_count": len(cards),
            "titled_card_count": product_titled_card_count(cards),
            "ocr_candidate_count": len(ocr_candidates),
            "ocr_available": product_title_ocr_available(),
            "title_wait_attempts": title_wait_attempts,
            "cards": product_card_trace_preview(cards),
            "match_count": len(matches),
            "new_title_count": len(new_titles),
            "same_screen_count": same_screen_count,
            "bottom_found": bottom_found,
            "add_more_products_visible": showcase_add_more_visible,
            "ordinary_search_results_visible": ordinary_search_visible,
            "product_detail_recovery": strip_private_trace(detail_recovery),
        }
        attempts.append(attempt)
        last_screen_summary = {
            "screenshot": str(screen.get("screenshot") or ""),
            "xml": str(screen.get("xml") or ""),
        }
        last_screen_data = {**last_screen_summary, "xml_text": str(screen.get("xml_text") or "")}
        write_product_scan_progress(
            run_dir=run_dir,
            area=area,
            target_key=target_key,
            setup_trace=setup_trace,
            attempts=attempts,
            status="scanning",
        )

        if len(matches) > 1:
            raise WorkflowError(
                f"ensure_showcase_product_attached: multiple {area} products matched prefix {target_key!r}; refusing to choose."
            )
        if ordinary_search_visible and not cards:
            return finish(
                "not_product_list",
                reason="ordinary_tiktok_search_results",
                last_screen=last_screen_summary,
                _last_screen=last_screen_data,
            )
        if cards and not product_cards_have_readable_titles(cards):
            reason = "titles_unreadable_ocr_unavailable" if not product_title_ocr_available() else "product_titles_not_ready"
            return finish(
                "titles_unreadable",
                reason=reason,
                last_screen=last_screen_summary,
                _last_screen=last_screen_data,
            )
        if len(matches) == 1:
            match = matches[0]
            attempt["selected"] = product_card_trace_preview([match])[0]
            if dry_run:
                return finish(
                    "matched",
                    dry_run=True,
                    selected=attempt["selected"],
                    last_screen=last_screen_summary,
                    _last_screen=last_screen_data,
                )
            if bool(match.get("already_added")) and area == "tiktok_shop":
                return finish(
                    "already_added",
                    selected=attempt["selected"],
                    last_screen=last_screen_summary,
                    _last_screen=last_screen_data,
                )
            if product_add_button_near_bottom_obstruction(
                match=match,
                xml_text=str(screen.get("xml_text") or ""),
                screenshot_path=screen.get("screenshot"),
                step=step,
            ):
                attempt["selected_unsafe"] = True
                attempt["unsafe_reason"] = "add_button_near_bottom_obstruction"
                if swipe_index >= max_swipes:
                    return finish(
                        "not_found",
                        reason="matched_add_button_near_bottom_obstruction",
                        selected=attempt["selected"],
                        last_screen=last_screen_summary,
                        _last_screen=last_screen_data,
                    )
                swipe_product_list(
                    adb=adb,
                    xml_text=str(screen.get("xml_text") or ""),
                    screenshot_path=screen.get("screenshot"),
                    step=step,
                )
                time.sleep(float(step.get("wait_after_swipe_seconds") or 1.0))
                previous_signature = ""
                continue
            adb.tap(int(match["add_x"]), int(match["add_y"]))
            if expected_package:
                time.sleep(float(step.get("post_add_foreground_wait_seconds") or 1.0))
                post_tap_screen = capture(
                    adb,
                    run_dir,
                    f"ensure_showcase_product_attached_{area}_post_add_{swipe_index:02d}",
                )
                attempt["post_add_screen"] = {
                    "screenshot": str(post_tap_screen.get("screenshot") or ""),
                    "xml": str(post_tap_screen.get("xml") or ""),
                }
                ensure_product_scan_screen_in_expected_package(
                    adb=adb,
                    screen=post_tap_screen,
                    expected_package=expected_package,
                    area=area,
                    phase=f"post_add_{swipe_index:02d}",
                )
            return finish(
                "matched",
                selected=attempt["selected"],
                last_screen=last_screen_summary,
                _last_screen=last_screen_data,
            )

        if bottom_found:
            return finish(
                "not_found",
                reason="bottom_text_found",
                last_screen=last_screen_summary,
                _last_screen=last_screen_data,
            )
        if showcase_add_more_visible:
            return finish(
                "not_found",
                reason="add_more_products_visible",
                last_screen=last_screen_summary,
                _last_screen=last_screen_data,
            )
        if swipe_index >= max_swipes:
            return finish(
                "not_found",
                reason="max_swipes_reached",
                last_screen=last_screen_summary,
                _last_screen=last_screen_data,
            )
        if same_screen_count >= same_screen_limit:
            return finish(
                "not_found",
                reason="same_screen_limit_reached",
                last_screen=last_screen_summary,
                _last_screen=last_screen_data,
            )
        if dry_run:
            return finish(
                "not_found",
                reason="dry_run_no_swipe",
                last_screen=last_screen_summary,
                _last_screen=last_screen_data,
            )

        swipe_product_list(
            adb=adb,
            xml_text=str(screen.get("xml_text") or ""),
            screenshot_path=screen.get("screenshot"),
            step=step,
        )
        time.sleep(float(step.get("wait_after_swipe_seconds") or 1.0))
        previous_signature = signature

    return finish(
        "not_found",
        reason="loop_exhausted",
        last_screen=last_screen_summary,
        _last_screen=last_screen_data,
    )


def wait_for_product_list_ready(
    *,
    adb: ADB,
    run_dir: Path,
    step: dict[str, Any],
    area: str,
    screen: dict[str, Any],
    add_texts: list[str],
    already_added_texts: list[str],
    dry_run: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    attempts: list[dict[str, Any]] = []
    max_attempts = int(step.get("product_list_ready_attempts") or 4)
    wait_seconds = float(step.get("product_list_ready_wait_seconds") or 1.0)
    current = screen
    for attempt_index in range(max(1, max_attempts)):
        xml_text = str(current.get("xml_text") or "")
        ready_reason = product_list_ready_reason(
            xml_text=xml_text,
            add_texts=add_texts,
            already_added_texts=already_added_texts,
            add_more_texts=product_texts_with_defaults(step.get("add_more_products_texts"), DEFAULT_ADD_MORE_PRODUCTS_TEXTS),
        )
        attempts.append(
            {
                "attempt": attempt_index,
                "ready": bool(ready_reason),
                "reason": ready_reason,
                "screenshot": str(current.get("screenshot") or ""),
                "xml": str(current.get("xml") or ""),
            }
        )
        if ready_reason or dry_run or attempt_index >= max_attempts - 1:
            return current, attempts
        time.sleep(wait_seconds)
        current = capture(adb, run_dir, f"ensure_showcase_product_attached_{area}_ready_{attempt_index + 1:02d}")
    return current, attempts


def wait_for_product_card_titles(
    *,
    adb: ADB,
    run_dir: Path,
    step: dict[str, Any],
    area: str,
    screen: dict[str, Any],
    cards: list[dict[str, Any]],
    add_texts: list[str],
    already_added_texts: list[str],
    min_confidence: float,
    expected_package: str,
    swipe_index: int,
) -> tuple[dict[str, Any], list[TextCandidate], list[dict[str, Any]], list[dict[str, Any]]]:
    max_attempts = int(step.get("product_title_ready_attempts") or 6)
    wait_seconds = float(step.get("product_title_ready_wait_seconds") or step.get("product_list_ready_wait_seconds") or 1.0)
    current = screen
    current_cards = cards
    current_ocr: list[TextCandidate] = []
    attempts: list[dict[str, Any]] = []
    for attempt_index in range(1, max(0, max_attempts) + 1):
        if product_cards_have_readable_titles(current_cards):
            break
        time.sleep(wait_seconds)
        current = capture(
            adb,
            run_dir,
            f"ensure_showcase_product_attached_{area}_title_ready_{swipe_index:02d}_{attempt_index:02d}",
        )
        ensure_product_scan_screen_in_expected_package(
            adb=adb,
            screen=current,
            expected_package=expected_package,
            area=area,
            phase=f"title_ready_{swipe_index:02d}_{attempt_index:02d}",
        )
        current_ocr = detect_product_ocr_candidates(
            screenshot_path=current.get("screenshot"),
            step=step,
            step_id=f"ensure_showcase_product_attached_{area}_title_ocr_{swipe_index:02d}_{attempt_index:02d}",
        )
        current_cards = extract_product_cards(
            xml_text=str(current.get("xml_text") or ""),
            ocr_candidates=current_ocr,
            add_texts=add_texts,
            already_added_texts=already_added_texts,
            min_ocr_confidence=min_confidence,
        )
        attempts.append(
            {
                "attempt": attempt_index,
                "screenshot": str(current.get("screenshot") or ""),
                "xml": str(current.get("xml") or ""),
                "card_count": len(current_cards),
                "titled_card_count": product_titled_card_count(current_cards),
                "ocr_candidate_count": len(current_ocr),
                "ocr_available": product_title_ocr_available(),
            }
        )
        if product_cards_have_readable_titles(current_cards) or not current_cards:
            break
    return current, current_ocr, current_cards, attempts


def product_cards_have_readable_titles(cards: list[dict[str, Any]]) -> bool:
    return product_titled_card_count(cards) > 0


def product_titled_card_count(cards: list[dict[str, Any]]) -> int:
    return sum(1 for card in cards if str(card.get("normalized_title") or ""))


def product_title_ocr_available() -> bool:
    return paddle_ocr_runtime_available()


def product_list_ready_reason(
    *,
    xml_text: str,
    add_texts: list[str],
    already_added_texts: list[str],
    add_more_texts: list[str],
) -> str:
    action_button_count = product_action_button_count(xml_text, add_texts, already_added_texts)
    if action_button_count > 0:
        return "product_action_button"
    if xml_has_any_text(xml_text, add_more_texts, match_mode="contains"):
        return "add_more_products"
    return ""


def product_action_button_count(xml_text: str, add_texts: list[str], already_added_texts: list[str]) -> int:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return 0
    return sum(1 for node in root.iter("node") if product_button_node_matches(node, add_texts, already_added_texts))


def reset_product_list_to_top(
    *,
    adb: ADB,
    run_dir: Path,
    step: dict[str, Any],
    area: str,
    screen: dict[str, Any],
    swipes: int,
    expected_package: str = "",
) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    current = screen
    wait_seconds = float(step.get("wait_after_reset_swipe_seconds") or step.get("wait_after_swipe_seconds") or 1.0)
    for swipe_index in range(1, max(0, swipes) + 1):
        reverse_swipe_product_list(
            adb=adb,
            xml_text=str(current.get("xml_text") or ""),
            screenshot_path=current.get("screenshot"),
            step=step,
        )
        time.sleep(wait_seconds)
        current = capture(adb, run_dir, f"ensure_showcase_product_attached_{area}_reset_top_{swipe_index:02d}")
        ensure_product_scan_screen_in_expected_package(
            adb=adb,
            screen=current,
            expected_package=expected_package,
            area=area,
            phase=f"reset_top_{swipe_index:02d}",
        )
        attempts.append(
            {
                "swipe": swipe_index,
                "screenshot": str(current.get("screenshot") or ""),
                "xml": str(current.get("xml") or ""),
            }
        )
    return {"enabled": True, "swipes": len(attempts), "attempts": attempts, "_last_screen": current}


def ensure_product_scan_screen_in_expected_package(
    *,
    adb: ADB,
    screen: dict[str, Any],
    expected_package: str,
    area: str,
    phase: str,
) -> None:
    if not expected_package:
        return
    check = confirm_expected_package(
        adb=adb,
        run_dir=screen_run_dir(screen),
        step_id=f"ensure_product_scan_{area}_{phase}",
        expected_package=expected_package,
        initial_xml_text=str(screen.get("xml_text") or ""),
        phase=f"{area}_{phase}",
    )
    if check["found"]:
        return
    current = check.get("foreground_package") or ", ".join(check.get("xml_packages") or []) or "none"
    raise WorkflowError(
        "ensure_showcase_product_attached: product scan left the expected app "
        f"during {area}/{phase}; expected {expected_package}, current package: {current}."
    )


def screen_run_dir(screen: dict[str, Any]) -> Path:
    xml_path = str(screen.get("xml") or "")
    if xml_path:
        path = Path(xml_path)
        if path.parent.name == "xml":
            return path.parent.parent
    screenshot_path = str(screen.get("screenshot") or "")
    if screenshot_path:
        path = Path(screenshot_path)
        if path.parent.name == "screenshots":
            return path.parent.parent
    return RUN_ROOT


def product_texts_with_defaults(configured: Any, defaults: list[str]) -> list[str]:
    values: list[str] = []
    for text in list(defaults) + clean_texts(configured):
        if text not in values:
            values.append(text)
    return values


def write_product_scan_progress(
    *,
    run_dir: Path,
    area: str,
    target_key: str,
    setup_trace: dict[str, Any],
    attempts: list[dict[str, Any]],
    status: str,
    reason: str = "",
) -> None:
    write_trace(
        run_dir,
        f"ensure_showcase_product_attached_{area}_scan",
        {
            "area": area,
            "target_key": target_key,
            "status": status,
            "reason": reason,
            **setup_trace,
            "attempts": attempts,
        },
    )


def ensure_screen(adb: ADB, run_dir: Path, step_id: str, screen: dict[str, Any]) -> dict[str, Any]:
    if screen.get("xml_text") and screen.get("screenshot"):
        return screen
    return capture(adb, run_dir, step_id)


def detect_product_ocr_candidates(*, screenshot_path: Any, step: dict[str, Any], step_id: str) -> list[TextCandidate]:
    if not screenshot_path:
        return []
    config = {
        "recognition": {
            "provider": "paddle_ocr",
            "paddle": {
                "lang": str(step.get("ocr_lang") or "ch"),
                "ocr_version": str(step.get("ocr_version") or "PP-OCRv4"),
                "device": str(step.get("ocr_device") or "cpu"),
                "use_gpu": bool(step.get("ocr_use_gpu", False)),
                "use_angle_cls": bool(step.get("ocr_use_angle_cls", False)),
                "show_log": bool(step.get("ocr_show_log", False)),
                "enable_mkldnn": bool(step.get("ocr_enable_mkldnn", False)),
                "min_confidence": float(step.get("ocr_min_confidence", 0.0) or 0.0),
                "text_rec_score_thresh": float(step.get("ocr_text_rec_score_thresh", 0.3) or 0.3),
            },
        },
    }
    try:
        return PaddleOCRButtonFinder(config)._detect_candidates(Path(screenshot_path))
    except Exception as exc:  # noqa: BLE001
        if paddle_ocr_unavailable(exc):
            return []
        raise WorkflowError(f"{step_id}: PaddleOCR failed while reading product titles: {exc}") from exc


def paddle_ocr_unavailable(exc: Exception) -> bool:
    text = str(exc)
    return "PaddleOCR is not installed" in text or "No module named 'paddleocr'" in text


def extract_product_cards(
    *,
    xml_text: str,
    ocr_candidates: list[TextCandidate],
    add_texts: list[str],
    already_added_texts: list[str],
    min_ocr_confidence: float,
) -> list[dict[str, Any]]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    parent_map = build_parent_map(root)
    screen_bounds = root_bounds(root)
    cards: list[dict[str, Any]] = []
    for node in root.iter("node"):
        if not product_button_node_matches(node, add_texts, already_added_texts):
            continue
        button_bounds = parse_bounds(node.attrib.get("bounds", ""))
        if not button_bounds:
            continue
        left, top, right, bottom = button_bounds
        if right <= left or bottom <= top:
            continue
        card_bounds = product_card_bounds_for_button(node, parent_map, screen_bounds)
        text_items = product_card_text_items(
            root=root,
            card_bounds=card_bounds,
            button_bounds=button_bounds,
            ocr_candidates=ocr_candidates,
            add_texts=add_texts + already_added_texts,
            min_ocr_confidence=min_ocr_confidence,
        )
        first_text_item = product_first_line_text_item(text_items)
        title_text = str((first_text_item or {}).get("text") or "").strip()
        normalized_title = normalize_product_match_text(title_text)
        cards.append(
            {
                "card_index": len(cards),
                "title_text": title_text,
                "text_variants": [title_text] if title_text else [],
                "normalized_title": normalized_title,
                "normalized_variants": [normalized_title] if normalized_title else [],
                "text_source": str((first_text_item or {}).get("source") or ""),
                "confidence": round(float((first_text_item or {}).get("confidence") or 0.0), 4),
                "add_x": (left + right) // 2,
                "add_y": (top + bottom) // 2,
                "add_button_bounds": [left, top, right, bottom],
                "card_bounds": list(card_bounds),
                "add_button_text": node.attrib.get("text", ""),
                "add_button_content_desc": node.attrib.get("content-desc", ""),
                "already_added": product_button_already_added(node, already_added_texts),
            }
        )
    cards = dedupe_product_cards(cards)
    cards.sort(key=lambda card: (card["card_bounds"][1], card["card_bounds"][0], card["add_y"]))
    for index, card in enumerate(cards):
        card["card_index"] = index
    return cards


def product_card_text_items(
    *,
    root: ET.Element,
    card_bounds: tuple[int, int, int, int],
    button_bounds: tuple[int, int, int, int],
    ocr_candidates: list[TextCandidate],
    add_texts: list[str],
    min_ocr_confidence: float,
) -> list[dict[str, Any]]:
    card_left, card_top, card_right, card_bottom = card_bounds
    button_left, button_top, _button_right, button_bottom = button_bounds
    items: list[dict[str, Any]] = []
    for node in root.iter("node"):
        bounds = parse_bounds(node.attrib.get("bounds", ""))
        if not bounds:
            continue
        left, top, right, bottom = bounds
        center_x = (left + right) // 2
        center_y = (top + bottom) // 2
        if not (card_left <= center_x <= card_right and card_top <= center_y <= card_bottom):
            continue
        if center_x >= button_left:
            continue
        for attr in ("text", "content-desc"):
            raw_text = str(node.attrib.get(attr) or "").strip()
            if not raw_text or product_text_is_button(raw_text, add_texts):
                continue
            items.append(
                {
                    "text": raw_text,
                    "source": "xml",
                    "confidence": 1.0,
                    "left": left,
                    "top": top,
                    "right": right,
                    "bottom": bottom,
                }
            )
    for candidate in ocr_candidates:
        confidence = float(candidate.confidence or 0.0)
        if confidence < min_ocr_confidence:
            continue
        center_x = int(candidate.x)
        center_y = int(candidate.y)
        if not (card_left <= center_x <= card_right and card_top <= center_y <= card_bottom):
            continue
        if center_x >= button_left:
            continue
        if candidate.bottom > button_bottom + 10:
            continue
        raw_text = str(candidate.text or "").strip()
        if not raw_text or product_text_is_button(raw_text, add_texts):
            continue
        items.append(
            {
                "text": raw_text,
                "source": "ocr",
                "confidence": confidence,
                "left": int(candidate.left),
                "top": int(candidate.top),
                "right": int(candidate.right),
                "bottom": int(candidate.bottom),
            }
        )
    items = dedupe_product_text_items(items)
    items.sort(key=lambda item: (int(item["top"]), int(item["left"])))
    return items


def product_first_line_text_item(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    for item in items:
        if str(item.get("text") or "").strip():
            return item
    return None


def add_unique(values: list[str], value: str) -> None:
    clean = value.strip()
    if clean and clean not in values:
        values.append(clean)


def product_text_source(items: list[dict[str, Any]]) -> str:
    sources = {str(item.get("source") or "") for item in items if item.get("source")}
    if len(sources) > 1:
        return "mixed"
    return next(iter(sources), "")


def product_text_confidence(items: list[dict[str, Any]]) -> float:
    values = [float(item.get("confidence") or 0.0) for item in items]
    if not values:
        return 0.0
    return round(min(values), 4)


def dedupe_product_text_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int]] = set()
    for item in items:
        key = (normalize_product_match_text(item.get("text")), int(item.get("top") or 0) // 8, int(item.get("left") or 0) // 8)
        if not key[0] or key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def dedupe_product_cards(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for card in cards:
        try:
            center_key = (int(card.get("add_x") or 0) // 8, int(card.get("add_y") or 0) // 8)
        except (TypeError, ValueError):
            center_key = (0, 0)
        key = (
            tuple(card.get("card_bounds") or []),
            str(card.get("normalized_title") or ""),
            center_key,
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(card)
    return deduped


def product_text_is_button(text: str, add_texts: list[str]) -> bool:
    normalized = normalize_product_match_text(text)
    return any(normalized == normalize_product_match_text(add_text) for add_text in add_texts)


def product_button_node_matches(node: ET.Element, add_texts: list[str], already_added_texts: list[str]) -> bool:
    class_name = node.attrib.get("class", "")
    if class_name in {"android.widget.TextView", "android.widget.EditText", "android.widget.ImageView"}:
        return False
    if (
        "Button" not in class_name
        and "ViewGroup" not in class_name
        and node.attrib.get("clickable") != "true"
        and node.attrib.get("focusable") != "true"
    ):
        return False
    values = [node.attrib.get("text", ""), node.attrib.get("content-desc", "")]
    texts = add_texts + already_added_texts
    return any(
        normalize_product_match_text(text) in normalize_product_match_text(value)
        for value in values
        for text in texts
        if normalize_product_match_text(text) and normalize_product_match_text(value)
    )


def product_button_already_added(node: ET.Element, already_added_texts: list[str]) -> bool:
    values = [node.attrib.get("text", ""), node.attrib.get("content-desc", "")]
    return any(
        normalize_product_match_text(text) in normalize_product_match_text(value)
        for value in values
        for text in already_added_texts
        if normalize_product_match_text(text) and normalize_product_match_text(value)
    )


def product_card_bounds_for_button(
    button: ET.Element,
    parent_map: dict[ET.Element, ET.Element],
    screen_bounds: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    button_bounds = parse_bounds(button.attrib.get("bounds", "")) or (0, 0, 0, 0)
    button_left, button_top, button_right, button_bottom = button_bounds
    button_width = max(1, button_right - button_left)
    button_height = max(1, button_bottom - button_top)
    screen_left, screen_top, screen_right, screen_bottom = screen_bounds
    screen_width = max(1, screen_right - screen_left)
    screen_height = max(1, screen_bottom - screen_top)

    parent = parent_map.get(button)
    while parent is not None:
        bounds = parse_bounds(parent.attrib.get("bounds", ""))
        if bounds:
            left, top, right, bottom = bounds
            width = right - left
            height = bottom - top
            if (
                width >= min(screen_width * 0.45, button_width * 2.0)
                and height >= button_height * 1.8
                and height <= screen_height * 0.45
                and top <= button_top <= bottom
                and left <= button_left <= right
            ):
                return bounds
        parent = parent_map.get(parent)

    fallback_top = max(screen_top, button_top - int(screen_height * 0.14))
    fallback_bottom = min(screen_bottom, button_bottom + int(screen_height * 0.03))
    return (screen_left, fallback_top, screen_right, fallback_bottom)


def build_parent_map(root: ET.Element) -> dict[ET.Element, ET.Element]:
    parent_map: dict[ET.Element, ET.Element] = {}
    for parent in root.iter():
        for child in list(parent):
            parent_map[child] = parent
    return parent_map


def root_bounds(root: ET.Element) -> tuple[int, int, int, int]:
    bounds = parse_bounds(root.attrib.get("bounds", ""))
    if bounds:
        return bounds
    max_right = 0
    max_bottom = 0
    for node in root.iter("node"):
        node_bounds = parse_bounds(node.attrib.get("bounds", ""))
        if not node_bounds:
            continue
        max_right = max(max_right, node_bounds[2])
        max_bottom = max(max_bottom, node_bounds[3])
    return (0, 0, max(max_right, 1), max(max_bottom, 1))


def matching_product_cards(cards: list[dict[str, Any]], target_key: str) -> list[dict[str, Any]]:
    return [card for card in cards if product_card_matches(card, target_key)]


def product_card_matches(card: dict[str, Any], target_key: str) -> bool:
    if not target_key:
        return False
    variant = str(card.get("normalized_title") or "")
    if len(variant) < len(target_key):
        return False
    return variant[: len(target_key)] == target_key


def product_add_button_near_bottom_obstruction(*, match: dict[str, Any], xml_text: str, screenshot_path: Any, step: dict[str, Any]) -> bool:
    width, height = screen_size_from_xml_or_png(xml_text, screenshot_path)
    _ = width
    safe_bottom_ratio = float(step.get("add_button_safe_bottom_ratio") or 0.90)
    safe_bottom_y = int(height * safe_bottom_ratio)
    bounds = match.get("add_button_bounds") or []
    try:
        button_bottom = int(bounds[3])
    except (TypeError, ValueError, IndexError):
        button_bottom = int(match.get("add_y") or 0)
    return button_bottom >= safe_bottom_y


def product_match_key(text: str, *, match_prefix_chars: int) -> str:
    normalized = normalize_product_match_text(text)
    if match_prefix_chars <= 0:
        return normalized
    return normalized[:match_prefix_chars]


def product_match_prefix_chars(step: dict[str, Any]) -> int:
    try:
        configured = int(step.get("match_prefix_chars") or DEFAULT_PRODUCT_MATCH_PREFIX_CHARS)
    except (TypeError, ValueError):
        configured = DEFAULT_PRODUCT_MATCH_PREFIX_CHARS
    if configured <= 0:
        return DEFAULT_PRODUCT_MATCH_PREFIX_CHARS
    return min(configured, DEFAULT_PRODUCT_MATCH_PREFIX_CHARS)


def truncate_text_to_normalized_prefix(value: str, max_normalized_chars: int) -> str:
    text = str(value or "")
    if max_normalized_chars <= 0:
        return text
    chars: list[str] = []
    for char in text:
        chars.append(char)
        if len(normalize_product_match_text("".join(chars))) >= max_normalized_chars:
            break
    return "".join(chars)


def normalize_product_match_text(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).casefold()
    chars: list[str] = []
    for char in text:
        category = unicodedata.category(char)
        if category[0] in {"C", "M", "P", "S", "Z"}:
            continue
        chars.append(char)
    return "".join(chars)


def product_page_signature(cards: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for card in cards:
        title = str(card.get("normalized_title") or "")
        bounds = ",".join(str(item) for item in (card.get("add_button_bounds") or []))
        parts.append(f"{title}:{bounds}")
    return "|".join(parts)


def product_bottom_reached(
    *,
    xml_text: str,
    ocr_candidates: list[TextCandidate],
    bottom_texts: list[str],
    min_ocr_confidence: float,
) -> bool:
    if xml_has_any_text(xml_text, bottom_texts, match_mode="contains"):
        return True
    needles = [normalize_product_match_text(text) for text in bottom_texts if normalize_product_match_text(text)]
    for candidate in ocr_candidates:
        if float(candidate.confidence or 0.0) < min_ocr_confidence:
            continue
        value = normalize_product_match_text(candidate.text)
        if any(needle in value for needle in needles):
            return True
    return False


def centered_modal_dialog_visible(xml_text: str) -> bool:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return False
    screen_left, screen_top, screen_right, screen_bottom = root_bounds(root)
    screen_width = max(1, screen_right - screen_left)
    screen_height = max(1, screen_bottom - screen_top)
    for node in root.iter("node"):
        bounds = parse_bounds(node.attrib.get("bounds", ""))
        if not bounds:
            continue
        left, top, right, bottom = bounds
        width = right - left
        height = bottom - top
        if width <= 0 or height <= 0:
            continue
        width_ratio = width / screen_width
        height_ratio = height / screen_height
        center_x = (left + right) / 2
        center_y = (top + bottom) / 2
        if not (0.55 <= width_ratio <= 0.9 and 0.2 <= height_ratio <= 0.45):
            continue
        if abs(center_x - (screen_left + screen_width / 2)) > screen_width * 0.15:
            continue
        if not (screen_top + screen_height * 0.35 <= center_y <= screen_top + screen_height * 0.75):
            continue
        return True
    return False


def product_card_trace_preview(cards: list[dict[str, Any]], *, limit: int = 20) -> list[dict[str, Any]]:
    preview: list[dict[str, Any]] = []
    for card in cards[:limit]:
        preview.append(
            {
                "card_index": card.get("card_index"),
                "title_text": str(card.get("title_text") or "")[:160],
                "normalized_title": str(card.get("normalized_title") or "")[:160],
                "normalized_match_prefix": str(card.get("normalized_title") or "")[:DEFAULT_PRODUCT_MATCH_PREFIX_CHARS],
                "text_source": card.get("text_source"),
                "confidence": card.get("confidence"),
                "add_x": card.get("add_x"),
                "add_y": card.get("add_y"),
                "add_button_bounds": card.get("add_button_bounds"),
                "card_bounds": card.get("card_bounds"),
                "already_added": bool(card.get("already_added")),
                "text_variants": [str(item)[:160] for item in (card.get("text_variants") or [])[:4]],
            }
        )
    return preview


def strip_private_trace(trace: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in trace.items() if not str(key).startswith("_")}


def product_detail_page_visible(xml_text: str) -> bool:
    values = set(xml_text_values(xml_text))
    if {"概述", "评论", "描述", "推荐"}.issubset(values):
        return True
    detail_markers = [
        "加入购物车",
        "立即购买",
        "此商品已被移除",
        "店铺",
        "聊天",
        "已售出",
        "在线售出",
        "fretegratis",
    ]
    marker_count = sum(1 for marker in detail_markers if xml_has_any_text(xml_text, [marker], match_mode="contains"))
    return marker_count >= 2 and xml_has_any_text(xml_text, ["概述", "评论", "描述", "推荐"], match_mode="exact")


def recover_from_product_detail_page(
    *,
    adb: ADB,
    run_dir: Path,
    area: str,
    swipe_index: int,
    screen: dict[str, Any],
    dry_run: bool,
    expected_package: str,
) -> dict[str, Any]:
    trace: dict[str, Any] = {
        "detected": True,
        "screenshot": str(screen.get("screenshot") or ""),
        "xml": str(screen.get("xml") or ""),
    }
    if dry_run:
        trace["recovered"] = False
        trace["reason"] = "dry_run"
        return trace
    adb.shell("input", "keyevent", "4", check=False, timeout=15)
    time.sleep(1.0)
    recovered_screen = capture(
        adb,
        run_dir,
        f"ensure_showcase_product_attached_{area}_detail_back_{swipe_index:02d}",
    )
    trace["after_back_screenshot"] = str(recovered_screen.get("screenshot") or "")
    trace["after_back_xml"] = str(recovered_screen.get("xml") or "")
    try:
        ensure_product_scan_screen_in_expected_package(
            adb=adb,
            screen=recovered_screen,
            expected_package=expected_package,
            area=area,
            phase=f"detail_back_{swipe_index:02d}",
        )
    except WorkflowError as exc:
        trace["recovered"] = False
        trace["reason"] = str(exc)
        trace["_screen"] = recovered_screen
        return trace
    add_texts = product_texts_with_defaults([], DEFAULT_PRODUCT_ADD_TEXTS)
    already_added_texts = product_texts_with_defaults([], DEFAULT_PRODUCT_ALREADY_ADDED_TEXTS)
    recovered = (
        not product_detail_page_visible(str(recovered_screen.get("xml_text") or ""))
        and product_list_ready_reason(
            xml_text=str(recovered_screen.get("xml_text") or ""),
            add_texts=add_texts,
            already_added_texts=already_added_texts,
            add_more_texts=product_texts_with_defaults([], DEFAULT_ADD_MORE_PRODUCTS_TEXTS),
        )
        != ""
    )
    trace["recovered"] = recovered
    trace["reason"] = "returned_to_product_list" if recovered else "returned_page_not_product_list"
    trace["_screen"] = recovered_screen
    return trace


def swipe_product_list(*, adb: ADB, xml_text: str, screenshot_path: Any, step: dict[str, Any]) -> None:
    list_bounds = scrollable_product_list_bounds(xml_text)
    if not list_bounds:
        width, height = screen_size_from_xml_or_png(xml_text, screenshot_path)
        list_bounds = (0, int(height * 0.18), width, int(height * 0.92))
    left, top, right, bottom = list_bounds
    width = max(1, right - left)
    height = max(1, bottom - top)
    start_ratio = float(step.get("swipe_start_ratio") or 0.82)
    end_ratio = float(step.get("swipe_end_ratio") or 0.30)
    x_ratio = float(step.get("product_swipe_x_ratio") or 0.04)
    x_ratio = min(0.96, max(0.02, x_ratio))
    start_x = left + int(width * x_ratio)
    end_x = start_x
    start_y = top + int(height * start_ratio)
    end_y = top + int(height * end_ratio)
    adb.swipe(start_x, start_y, end_x, end_y, int(step.get("swipe_duration_ms") or 500))


def reverse_swipe_product_list(*, adb: ADB, xml_text: str, screenshot_path: Any, step: dict[str, Any]) -> None:
    list_bounds = scrollable_product_list_bounds(xml_text)
    if not list_bounds:
        width, height = screen_size_from_xml_or_png(xml_text, screenshot_path)
        list_bounds = (0, int(height * 0.18), width, int(height * 0.92))
    left, top, right, bottom = list_bounds
    width = max(1, right - left)
    height = max(1, bottom - top)
    start_ratio = float(step.get("reset_swipe_start_ratio") or 0.30)
    end_ratio = float(step.get("reset_swipe_end_ratio") or 0.76)
    start_x = left + width // 2
    end_x = start_x
    start_y = top + int(height * start_ratio)
    end_y = top + int(height * end_ratio)
    adb.swipe(start_x, start_y, end_x, end_y, int(step.get("reset_swipe_duration_ms") or step.get("swipe_duration_ms") or 500))


def scrollable_product_list_bounds(xml_text: str) -> tuple[int, int, int, int] | None:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    candidates: list[tuple[int, int, int, int]] = []
    for node in root.iter("node"):
        class_name = node.attrib.get("class", "")
        if "RecyclerView" not in class_name and "ScrollView" not in class_name and node.attrib.get("scrollable") != "true":
            continue
        bounds = parse_bounds(node.attrib.get("bounds", ""))
        if not bounds:
            continue
        left, top, right, bottom = bounds
        if right <= left or bottom <= top:
            continue
        candidates.append(bounds)
    if not candidates:
        return None
    candidates.sort(key=lambda bounds: (bounds[3] - bounds[1]) * (bounds[2] - bounds[0]), reverse=True)
    return candidates[0]


def screen_size_from_xml_or_png(xml_text: str, screenshot_path: Any) -> tuple[int, int]:
    try:
        root = ET.fromstring(xml_text)
        bounds = root_bounds(root)
        if bounds[2] > 1 and bounds[3] > 1:
            return bounds[2], bounds[3]
    except ET.ParseError:
        pass
    if screenshot_path:
        try:
            return png_size(Path(screenshot_path))
        except Exception:
            pass
    return 1080, 2400


def tap_text_or_configured_point(
    *,
    adb: ADB,
    profile: dict[str, Any],
    run_dir: Path,
    step_id: str,
    screen: dict[str, Any],
    texts: list[str],
    point_name: str,
    point_ratio: Any,
    reason: str,
) -> dict[str, Any]:
    screen = ensure_screen(adb, run_dir, step_id, screen)
    text_tap = first_text_match_center(str(screen.get("xml_text") or ""), texts, match_mode="contains")
    if text_tap:
        adb.tap(int(text_tap["x"]), int(text_tap["y"]))
        return {"action": "tap_text", "reason": reason, **text_tap}
    point = configured_point_or_ratio(profile, point_name, point_ratio, screen)
    adb.tap(int(point["x"]), int(point["y"]))
    return {"action": "tap_point", "reason": reason, **point}


def configured_point_or_ratio(
    profile: dict[str, Any],
    point_name: str,
    point_ratio: Any,
    screen: dict[str, Any],
) -> dict[str, Any]:
    points = profile.get("coordinate_points", {}) if isinstance(profile.get("coordinate_points"), dict) else {}
    if point_name and isinstance(points.get(point_name), dict):
        return point_log(dict(points[point_name]))
    if not isinstance(point_ratio, dict):
        raise WorkflowError(f"Point not configured in profile and no ratio fallback was provided: {point_name}")
    width, height = screen_size_from_xml_or_png(str(screen.get("xml_text") or ""), screen.get("screenshot"))
    x_ratio = float(point_ratio.get("x_ratio") or 0.5)
    y_ratio = float(point_ratio.get("y_ratio") or 0.5)
    return {
        "x": int(width * x_ratio),
        "y": int(height * y_ratio),
        "x_ratio": x_ratio,
        "y_ratio": y_ratio,
        "point_description": f"ratio fallback for {point_name}",
        "calibration_status": "ratio_fallback",
    }


def return_to_showcase_product_list(
    *,
    adb: ADB,
    profile: dict[str, Any],
    step: dict[str, Any],
    run_dir: Path,
    wait_scale: float,
) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    showcase_texts = product_texts_with_defaults(step.get("showcase_tab_texts"), DEFAULT_SHOWCASE_TEXTS)
    for attempt in range(1, int(step.get("return_to_showcase_max_attempts") or 4) + 1):
        screen = capture(adb, run_dir, f"ensure_showcase_product_attached_return_{attempt:02d}")
        visible = xml_has_any_text(str(screen.get("xml_text") or ""), showcase_texts, match_mode="contains")
        attempts.append({"attempt": attempt, "xml": str(screen.get("xml") or ""), "showcase_visible": visible})
        if visible:
            return {"status": "showcase_visible", "attempts": attempts}
        text_tap = first_text_match_center(str(screen.get("xml_text") or ""), showcase_texts, match_mode="contains")
        if text_tap:
            adb.tap(int(text_tap["x"]), int(text_tap["y"]))
        else:
            points = profile.get("coordinate_points", {}) if isinstance(profile.get("coordinate_points"), dict) else {}
            point_name = str(step.get("showcase_tab_point") or "showcase_tab")
            if isinstance(points.get(point_name), dict):
                point = dict(points[point_name])
                adb.tap(int(point["x"]), int(point["y"]))
            else:
                adb.shell("input", "keyevent", "4", check=False, timeout=15)
        time.sleep(float(step.get("wait_after_tap_seconds") or 1.5) * wait_scale)
    raise WorkflowError("ensure_showcase_product_attached: could not return to the showcase product list after TikTok Shop add.")


WORKFLOW_VIDEO_NAME_PATTERNS = [
    "20??????_??????_*.mp4",
    "20??????-??????_*.mp4",
    "gcs_*.mp4",
]

VIDEO_EXTENSIONS = (".mp4", ".mov", ".m4v", ".webm", ".3gp", ".3gpp", ".mkv", ".avi")
MEDIA_URIS = (
    "content://media/external_primary/video/media",
    "content://media/external/video/media",
    "content://media/external_primary/file",
    "content://media/external/file",
)


def cleanup_workflow_videos_before_push(
    adb: ADB,
    remote_dir: str,
    run_dir: Path,
    *,
    dry_run: bool,
    delete_all_videos: bool = False,
    max_delete_count: int = 200,
) -> dict[str, Any]:
    paths = list_gallery_video_paths(adb, remote_dir, include_all_videos=delete_all_videos)
    if len(paths) > max_delete_count:
        raise WorkflowError(
            f"pre_push_gallery_cleanup: found {len(paths)} gallery videos, "
            f"which exceeds max_delete_count={max_delete_count}."
        )

    file_errors: list[str] = []
    record_outputs: list[dict[str, str]] = []
    if not dry_run:
        for path in paths:
            output = adb.shell("rm", "-f", path, check=False, timeout=30).strip()
            if output:
                file_errors.append(f"rm {path}: {output}")
            record_outputs.extend(delete_media_records_for_path(adb, path))
        if paths:
            scan_media_volumes(adb)

    trace = {
        "step": "pre_push_gallery_cleanup",
        "id": "pre_push_gallery_cleanup",
        "type": "pre_push_gallery_cleanup",
        "action": "delete_gallery_videos_before_push" if delete_all_videos else "delete_workflow_videos_before_push",
        "dry_run": dry_run,
        "delete_all_videos": delete_all_videos,
        "remote_dir": remote_dir,
        "candidate_count": len(paths),
        "file_delete_count": 0 if dry_run else len(paths),
        "paths": paths,
        "record_outputs": record_outputs,
        "errors": file_errors,
        "run_dir": str(run_dir),
        "completed_at": datetime.now().isoformat(timespec="seconds"),
    }
    print(f"pre_push_gallery_cleanup: removed {trace['file_delete_count']} gallery video(s)")
    return trace


def list_workflow_video_paths(adb: ADB, remote_dir: str) -> list[str]:
    return list_gallery_video_paths(adb, remote_dir, include_all_videos=False)


def list_gallery_video_paths(adb: ADB, remote_dir: str, *, include_all_videos: bool) -> list[str]:
    output = adb.shell("find", remote_dir, "-type", "f", check=False, timeout=120)
    paths: list[str] = []
    seen: set[str] = set()
    for raw_line in output.splitlines():
        path = raw_line.strip()
        if not path or path.startswith("find:"):
            continue
        if not path_is_safe_gallery_path(path) or not path_is_under(path, remote_dir):
            continue
        name = PurePosixPath(path).name
        if not name.casefold().endswith(VIDEO_EXTENSIONS):
            continue
        if not include_all_videos and not any(fnmatch.fnmatchcase(name, pattern) for pattern in WORKFLOW_VIDEO_NAME_PATTERNS):
            continue
        normalized = normalize_storage_path(path)
        if normalized in seen:
            continue
        seen.add(normalized)
        paths.append(path)
    paths.sort()
    return paths


def verify_current_gallery_video(
    adb: ADB,
    remote_dir: str,
    remote_path: str,
    run_dir: Path,
    *,
    dry_run: bool,
    require_single_video: bool,
) -> dict[str, Any]:
    if not dry_run:
        scan_media_volumes(adb)
    paths = list_gallery_video_paths(adb, remote_dir, include_all_videos=require_single_video)
    normalized_expected = normalize_storage_path(remote_path)
    normalized_paths = [normalize_storage_path(path) for path in paths]
    expected_present = normalized_expected in normalized_paths
    trace = {
        "step": "verify_current_gallery_video",
        "id": "verify_current_gallery_video",
        "type": "verify_current_gallery_video",
        "action": "verify_current_gallery_video",
        "dry_run": dry_run,
        "remote_dir": remote_dir,
        "remote_video_path": remote_path,
        "require_single_video": require_single_video,
        "paths": paths,
        "file_count": len(paths),
        "expected_present": expected_present,
        "run_dir": str(run_dir),
        "completed_at": datetime.now().isoformat(timespec="seconds"),
    }
    error = ""
    if not expected_present:
        error = f"verify_current_gallery_video: pushed video is not visible in {remote_dir}: {remote_path}"
    elif require_single_video and len(paths) != 1:
        error = f"verify_current_gallery_video: expected 1 gallery video, found {len(paths)}."
    if error:
        trace["error"] = error
    print(f"verify_current_gallery_video: {len(paths)} video(s), current present={expected_present}")
    return trace


def delete_media_records_for_path(adb: ADB, path: str) -> list[dict[str, str]]:
    outputs: list[dict[str, str]] = []
    normalized = normalize_storage_path(path)
    where = f"_data='{sql_literal(normalized)}' OR _data='{sql_literal(path)}'"
    for uri in MEDIA_URIS:
        command = "content delete --uri " + shlex.quote(uri) + " --where " + shlex.quote(where)
        output = adb.shell_command(command, check=False, timeout=30).strip()
        outputs.append({"uri": uri, "path": path, "output": output})
    return outputs


def scan_media_volumes(adb: ADB) -> None:
    for volume in ("external_primary", "external"):
        adb.shell("cmd", "media", "scan-volume", volume, check=False, timeout=120)


def path_is_safe_gallery_path(path: str) -> bool:
    return normalize_storage_path(path).startswith("/storage/emulated/0/")


def path_is_under(path: str, parent: str) -> bool:
    normalized_path = normalize_storage_path(path).rstrip("/")
    normalized_parent = normalize_storage_path(parent).rstrip("/")
    return normalized_path == normalized_parent or normalized_path.startswith(normalized_parent + "/")


def normalize_storage_path(path: str) -> str:
    if path.startswith("/sdcard/"):
        return "/storage/emulated/0/" + path.removeprefix("/sdcard/")
    return path


def sql_literal(value: str) -> str:
    return value.replace("'", "''")


def push_video(adb: ADB, local_path: Path, remote_dir: str) -> str:
    if not local_path.exists():
        raise FileNotFoundError(f"Video not found: {local_path}")
    safe_name = safe_remote_filename(local_path.name)
    remote_name = datetime.now().strftime("gcs_%Y%m%d-%H%M%S_") + safe_name
    remote_path = str(PurePosixPath(remote_dir) / remote_name)
    adb.shell("mkdir", "-p", remote_dir, check=False, timeout=15)
    adb.run(["push", str(local_path), remote_path], timeout=600)
    adb.shell("touch", remote_path, check=False, timeout=15)
    adb.shell("cmd", "media", "scan-file", remote_path, check=False, timeout=30)
    adb.shell_command(
        "am broadcast --receiver-include-background -a android.intent.action.MEDIA_SCANNER_SCAN_FILE -d "
        + shlex.quote(f"file://{remote_path}"),
        check=False,
        timeout=30,
    )
    print(f"Pushed video: {remote_path}")
    return remote_path


def safe_remote_filename(name: str) -> str:
    stem, ext = os.path.splitext(name)
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-") or "video"
    ext = re.sub(r"[^A-Za-z0-9.]+", "", ext) or ".mp4"
    return f"{stem}{ext}"


def verify_device(adb: ADB, profile: dict[str, Any], run_dir: Path) -> None:
    state = adb.run(["get-state"], timeout=15).stdout.strip()
    size_output = adb.shell("wm", "size", timeout=15)
    density_output = adb.shell("wm", "density", timeout=15)
    model = adb.shell("getprop", "ro.product.model", timeout=15).strip()
    width, height = parse_wm_size(size_output)
    expected = profile["screen"]
    report = {
        "adb_state": state,
        "wm_size": size_output.strip(),
        "wm_density": density_output.strip(),
        "model": model,
        "expected_width": expected["width"],
        "expected_height": expected["height"],
    }
    write_json(run_dir / "device_check.json", report)
    if state != "device":
        raise WorkflowError(f"ADB device is not online: {state}")
    if profile.get("safety", {}).get("stop_if_screen_size_mismatch", True):
        if width != int(expected["width"]) or height != int(expected["height"]):
            raise WorkflowError(f"Screen size mismatch: got {width}x{height}, expected {expected['width']}x{expected['height']}")


def grant_media_permissions(adb: ADB, profile: dict[str, Any]) -> None:
    package = app_package(profile, "tiktok")
    permissions = [
        "android.permission.READ_EXTERNAL_STORAGE",
        "android.permission.READ_MEDIA_IMAGES",
        "android.permission.READ_MEDIA_VIDEO",
    ]
    for permission in permissions:
        adb.shell("pm", "grant", package, permission, check=False, timeout=15)
    adb.shell("appops", "set", package, "READ_EXTERNAL_STORAGE", "allow", check=False, timeout=15)


def managed_gallery_video_dir(profile: dict[str, Any]) -> bool:
    return bool(profile.get("safety", {}).get("managed_gallery_video_dir", False))


def max_gallery_video_delete_count(profile: dict[str, Any]) -> int:
    value = profile.get("safety", {}).get("max_gallery_video_delete_count", 200)
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return 200


def parse_wm_size(output: str) -> tuple[int, int]:
    match = re.search(r"Physical size:\s*(\d+)x(\d+)", output)
    if not match:
        raise WorkflowError(f"Cannot parse wm size: {output}")
    return int(match.group(1)), int(match.group(2))


def paste_text(adb: ADB, text: str) -> None:
    if text:
        if activate_adb_keyboard(adb):
            encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
            adb.shell("am", "broadcast", "-a", "ADB_INPUT_B64", "--es", "msg", encoded, check=False, timeout=15)
            time.sleep(0.2)
            return
        adb.shell("input", "text", escape_input_text(text), check=False, timeout=30)


def caption_with_trailing_space(caption: str) -> tuple[str, bool]:
    if caption and not caption[-1].isspace():
        return f"{caption} ", True
    return caption, False


def verify_caption_pasted_or_raise(
    *,
    adb: ADB,
    run_dir: Path,
    step_id: str,
    step: dict[str, Any],
    context: dict[str, Any],
    point: dict[str, Any],
    caption: str,
    trace: dict[str, Any],
) -> None:
    attempts: list[dict[str, Any]] = []
    max_attempts = max(1, int(step.get("caption_verify_attempts") or 2))
    wait_seconds = float(step.get("caption_verify_wait_seconds", 0.8)) * float(context.get("wait_scale") or 1.0)
    prefix_chars = int(step.get("caption_verify_prefix_chars") or DEFAULT_CAPTION_VERIFY_PREFIX_CHARS)
    for attempt in range(1, max_attempts + 1):
        if wait_seconds:
            time.sleep(wait_seconds)
        xml_path = run_dir / "xml" / f"{step_id}_caption_verify_{attempt:02d}.xml"
        xml_text = adb.dump_xml(xml_path)
        result = caption_paste_match_details(xml_text, caption, prefix_chars=prefix_chars)
        result["attempt"] = attempt
        result["xml"] = str(xml_path)
        attempts.append(result)
        if result["found"]:
            trace["caption_verify"] = {
                "found": True,
                "attempts": attempts,
                "expected_prefix": result.get("expected_prefix", ""),
            }
            return
        if attempt < max_attempts:
            adb.tap(int(point["x"]), int(point["y"]))
            if step.get("clear_existing_text", False):
                clear_focused_text(adb, int(step.get("clear_max_chars", 160)))
            paste_text(adb, caption)
            if step.get("hide_keyboard_after_paste", False):
                time.sleep(0.5)
                adb.shell("input", "keyevent", "4", check=False, timeout=15)

    trace["caption_verify"] = {
        "found": False,
        "attempts": attempts,
        "expected_prefix": attempts[-1].get("expected_prefix", "") if attempts else "",
    }
    raise WorkflowError(f"{step_id}: caption paste was not verified; refusing to continue before product add.")


def caption_paste_match_details(xml_text: str, caption: str, *, prefix_chars: int = DEFAULT_CAPTION_VERIFY_PREFIX_CHARS) -> dict[str, Any]:
    expected = normalize_product_match_text(caption)
    if not expected:
        return {
            "found": False,
            "reason": "empty_expected_caption",
            "expected_prefix": "",
            "values_preview": xml_text_values(xml_text)[:12],
        }
    prefix_len = min(max(1, int(prefix_chars)), len(expected))
    expected_prefix = expected[:prefix_len]
    values = [normalize_product_match_text(value) for value in xml_text_values(xml_text)]
    values = [value for value in values if value]
    min_overlap = min(DEFAULT_CAPTION_VERIFY_MIN_CHARS, len(expected_prefix))
    for value in values:
        if expected_prefix in value:
            return {
                "found": True,
                "reason": "expected_prefix_in_xml_value",
                "expected_prefix": expected_prefix,
                "matched_value": value[:120],
                "values_preview": values[:12],
            }
        overlap = min(len(value), len(expected_prefix))
        if overlap >= min_overlap and expected.startswith(value[:overlap]):
            return {
                "found": True,
                "reason": "xml_value_matches_expected_start",
                "expected_prefix": expected_prefix,
                "matched_value": value[:120],
                "values_preview": values[:12],
            }
    return {
        "found": False,
        "reason": "expected_prefix_not_found",
        "expected_prefix": expected_prefix,
        "values_preview": values[:12],
    }


def clear_focused_text(adb: ADB, max_chars: int) -> None:
    if max_chars <= 0:
        return
    if activate_adb_keyboard(adb):
        adb.shell("am", "broadcast", "-a", "ADB_CLEAR_TEXT", check=False, timeout=15)
        time.sleep(0.2)
        return
    adb.shell("input", "keyevent", "123", check=False, timeout=15)
    adb.shell_command(f"for i in $(seq 1 {max_chars}); do input keyevent 67; done", check=False, timeout=60)
    time.sleep(min(2.5, max(0.3, max_chars * 0.02)))


def activate_adb_keyboard(adb: ADB) -> bool:
    ime_list = adb.shell("ime", "list", "-s", check=False, timeout=15)
    if ADB_KEYBOARD_IME not in ime_list:
        return False
    adb.shell("ime", "set", ADB_KEYBOARD_IME, check=False, timeout=15)
    return True


def escape_input_text(text: str) -> str:
    replacements = {
        " ": "%s",
        "&": "\\&",
        "<": "\\<",
        ">": "\\>",
        "|": "\\|",
        ";": "\\;",
        "(": "\\(",
        ")": "\\)",
        "'": "\\'",
        '"': '\\"',
        ",": "\\,",
    }
    return "".join(replacements.get(char, char) for char in text)


def wait_for_any_text(
    adb: ADB,
    run_dir: Path,
    step_id: str,
    texts: list[str],
    timeout_seconds: float,
    *,
    match_mode: str = "contains",
) -> bool:
    deadline = time.time() + timeout_seconds
    attempt = 1
    while time.time() < deadline:
        xml_path = run_dir / "xml" / f"{step_id}_wait_{attempt:02d}.xml"
        xml_text = adb.dump_xml(xml_path)
        if xml_has_any_text(xml_text, texts, match_mode=match_mode):
            return True
        time.sleep(1.0)
        attempt += 1
    return False


def wait_for_product_link_attached(
    *,
    adb: ADB,
    run_dir: Path,
    step_id: str,
    context: dict[str, Any],
    timeout_seconds: float,
    min_prefix_chars: int,
    initial_xml: str,
) -> tuple[bool, dict[str, Any] | None, list[dict[str, Any]], list[str]]:
    keywords = product_link_keywords(context, min_prefix_chars=min_prefix_chars)
    attempts: list[dict[str, Any]] = []
    if not keywords:
        attempts.append({"attempt": 0, "found": False, "xml": "", "reason": "no_product_link_keywords"})
        return False, None, attempts, keywords

    match = first_product_link_match(initial_xml, keywords)
    attempts.append({"attempt": 0, "found": bool(match), "xml": "", "match": match})
    if match:
        return True, match, attempts, keywords

    deadline = time.time() + timeout_seconds
    attempt = 1
    while time.time() < deadline:
        time.sleep(1.0)
        xml_path = run_dir / "xml" / f"{step_id}_wait_{attempt:02d}.xml"
        xml_text = adb.dump_xml(xml_path)
        match = first_product_link_match(xml_text, keywords)
        attempts.append({"attempt": attempt, "found": bool(match), "xml": str(xml_path), "match": match})
        if match:
            return True, match, attempts, keywords
        attempt += 1
    return False, None, attempts, keywords


def product_link_keywords(context: dict[str, Any], *, min_prefix_chars: int = 10) -> list[str]:
    min_prefix_chars = max(1, min_prefix_chars)
    keywords: list[str] = []
    for key in ("product_publish_name", "product_search_title"):
        value = normalize(context.get(key))
        if not value:
            continue
        keyword = value[:min_prefix_chars] if len(value) > min_prefix_chars else value
        if keyword and keyword not in keywords:
            keywords.append(keyword)
    return keywords


def first_product_link_match(xml_text: str, keywords: list[str]) -> dict[str, Any] | None:
    if not keywords:
        return None
    for value in xml_text_values(xml_text):
        for keyword in keywords:
            if keyword in value:
                return {"matched_keyword": keyword, "matched_value": value}
    return None


def wait_for_media_picker_item_count(
    *,
    adb: ADB,
    run_dir: Path,
    step_id: str,
    expected_count: int,
    count_mode: str,
    timeout_seconds: float,
    initial_xml: str,
    initial_screenshot: Any = "",
    step: dict[str, Any] | None = None,
) -> tuple[bool, int, list[dict[str, Any]]]:
    attempts: list[dict[str, Any]] = []
    count = count_media_picker_items(initial_xml)
    ocr_count = count_media_picker_items_from_screenshot(
        screenshot_path=initial_screenshot,
        step=step or {},
        step_id=step_id,
    )
    count = max(count, ocr_count)
    attempts.append({"attempt": 0, "count": count, "xml": "", "ocr_count": ocr_count})
    if media_picker_count_matches(count, expected_count, count_mode):
        return True, count, attempts

    deadline = time.time() + timeout_seconds
    attempt = 1
    while time.time() < deadline:
        time.sleep(1.0)
        xml_path = run_dir / "xml" / f"{step_id}_wait_{attempt:02d}.xml"
        xml_text = adb.dump_xml(xml_path)
        count = count_media_picker_items(xml_text)
        screenshot_path = run_dir / "screenshots" / f"{step_id}_wait_{attempt:02d}.png"
        screenshot = adb.screenshot(screenshot_path)
        ocr_count = count_media_picker_items_from_screenshot(
            screenshot_path=screenshot,
            step=step or {},
            step_id=step_id,
        )
        count = max(count, ocr_count)
        attempts.append({"attempt": attempt, "count": count, "xml": str(xml_path), "screenshot": str(screenshot), "ocr_count": ocr_count})
        if media_picker_count_matches(count, expected_count, count_mode):
            return True, count, attempts
        attempt += 1
    return False, count, attempts


def media_picker_count_matches(count: int, expected_count: int, count_mode: str) -> bool:
    if count_mode == "at_least":
        return count >= expected_count
    return count == expected_count


def media_picker_count_expectation_text(expected_count: int, count_mode: str) -> str:
    if count_mode == "at_least":
        return f"at least {expected_count}"
    return str(expected_count)


def count_media_picker_items_from_screenshot(*, screenshot_path: Any, step: dict[str, Any], step_id: str) -> int:
    if not screenshot_path:
        return 0
    try:
        candidates = detect_product_ocr_candidates(screenshot_path=screenshot_path, step=step, step_id=step_id)
    except WorkflowError:
        return 0
    duration_candidates = [
        candidate
        for candidate in candidates
        if re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", candidate.text.strip())
    ]
    return len(duration_candidates)


def count_media_picker_items(xml_text: str) -> int:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return 0

    counts: list[int] = []
    for node in root.iter("node"):
        resource_id = node.attrib.get("resource-id", "")
        if node.attrib.get("class") != "android.widget.GridView":
            continue
        if not is_media_picker_grid(node):
            continue
        item_count = 0
        for child in list(node):
            bounds = parse_bounds(child.attrib.get("bounds", ""))
            if not bounds:
                continue
            left, top, right, bottom = bounds
            if right <= left or bottom <= top:
                continue
            if child.attrib.get("clickable") == "true" or child.attrib.get("long-clickable") == "true":
                item_count += 1
        counts.append(item_count)
    return max(counts, default=0)


def is_media_picker_grid(node: ET.Element) -> bool:
    resource_id = node.attrib.get("resource-id", "")
    known_suffixes = (":id/ir_", "/id/ir_", ":id/ivd", "/id/ivd")
    if any(resource_id.endswith(suffix) for suffix in known_suffixes):
        return True
    return any(node_has_video_duration(child) for child in list(node))


def node_has_video_duration(node: ET.Element) -> bool:
    for item in node.iter("node"):
        text = item.attrib.get("text", "")
        if re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", text.strip()):
            return True
    return False


def wait_for_first_matching_node(
    *,
    adb: ADB,
    run_dir: Path,
    step_id: str,
    selector: dict[str, Any],
    timeout_seconds: float,
    initial_xml: str,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    attempts: list[dict[str, Any]] = []
    match = first_matching_node_center(initial_xml, selector)
    attempts.append({"attempt": 0, "found": bool(match), "xml": ""})
    if match:
        return match, attempts

    deadline = time.time() + timeout_seconds
    attempt = 1
    while time.time() < deadline:
        time.sleep(1.0)
        xml_path = run_dir / "xml" / f"{step_id}_wait_{attempt:02d}.xml"
        xml_text = adb.dump_xml(xml_path)
        match = first_matching_node_center(xml_text, selector)
        attempts.append({"attempt": attempt, "found": bool(match), "xml": str(xml_path)})
        if match:
            return match, attempts
        attempt += 1
    return None, attempts


def first_matching_node_center(xml_text: str, selector: dict[str, Any]) -> dict[str, Any] | None:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None

    for node in root.iter("node"):
        if not xml_node_matches(node.attrib, selector):
            continue
        bounds = parse_bounds(node.attrib.get("bounds", ""))
        if not bounds:
            continue
        left, top, right, bottom = bounds
        if right <= left or bottom <= top:
            continue
        return {
            "x": (left + right) // 2,
            "y": (top + bottom) // 2,
            "bounds": [left, top, right, bottom],
            "matched_text": node.attrib.get("text", ""),
            "matched_content_desc": node.attrib.get("content-desc", ""),
            "matched_resource_id": node.attrib.get("resource-id", ""),
            "matched_class": node.attrib.get("class", ""),
        }
    return None


def first_text_match_center(xml_text: str, texts: list[str], *, match_mode: str = "contains") -> dict[str, Any] | None:
    needles = [normalize(text) for text in texts if normalize(text)]
    if not needles:
        return None
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    parent_map = build_parent_map(root)
    screen_bounds = root_bounds(root)

    for node in root.iter("node"):
        for attr in ("text", "content-desc"):
            raw_value = node.attrib.get(attr, "")
            value = normalize(raw_value)
            if not value:
                continue
            if match_mode in {"exact", "node_exact"}:
                matched = any(value == needle for needle in needles)
            else:
                matched = any(needle in value for needle in needles)
            if not matched:
                continue
            bounds = parse_bounds(node.attrib.get("bounds", ""))
            if not bounds:
                continue
            left, top, right, bottom = bounds
            if right <= left or bottom <= top:
                continue
            click_node, click_bounds = clickable_text_target(node, parent_map, bounds, screen_bounds)
            click_left, click_top, click_right, click_bottom = click_bounds
            return {
                "x": (click_left + click_right) // 2,
                "y": (click_top + click_bottom) // 2,
                "bounds": [click_left, click_top, click_right, click_bottom],
                "matched_node_bounds": [left, top, right, bottom],
                "matched_text": node.attrib.get("text", ""),
                "matched_content_desc": node.attrib.get("content-desc", ""),
                "matched_resource_id": node.attrib.get("resource-id", ""),
                "matched_class": node.attrib.get("class", ""),
                "click_target_text": click_node.attrib.get("text", ""),
                "click_target_content_desc": click_node.attrib.get("content-desc", ""),
                "click_target_resource_id": click_node.attrib.get("resource-id", ""),
                "click_target_class": click_node.attrib.get("class", ""),
                "click_target_from_ancestor": click_node is not node,
                "text_match_mode": match_mode,
            }
    return None


def clickable_text_target(
    node: ET.Element,
    parent_map: dict[ET.Element, ET.Element],
    text_bounds: tuple[int, int, int, int],
    screen_bounds: tuple[int, int, int, int],
) -> tuple[ET.Element, tuple[int, int, int, int]]:
    screen_height = max(1, screen_bounds[3] - screen_bounds[1])
    current: ET.Element | None = node
    while current is not None:
        bounds = parse_bounds(current.attrib.get("bounds", ""))
        if bounds and current.attrib.get("clickable") == "true" and clickable_text_bounds_are_reasonable(bounds, text_bounds, screen_height):
            return current, bounds
        current = parent_map.get(current)
    return node, text_bounds


def clickable_text_bounds_are_reasonable(
    click_bounds: tuple[int, int, int, int],
    text_bounds: tuple[int, int, int, int],
    screen_height: int,
) -> bool:
    left, top, right, bottom = click_bounds
    text_left, text_top, text_right, text_bottom = text_bounds
    if right <= left or bottom <= top:
        return False
    if not (left <= text_left <= text_right <= right and top <= text_top <= text_bottom <= bottom):
        return False
    return (bottom - top) <= max(1, int(screen_height * 0.35))


def xml_node_matches(attributes: dict[str, str], selector: dict[str, Any]) -> bool:
    aliases = {
        "text": "text",
        "content_desc": "content-desc",
        "content-desc": "content-desc",
        "resource_id": "resource-id",
        "resource-id": "resource-id",
        "class": "class",
        "clickable": "clickable",
        "enabled": "enabled",
    }
    match_mode = str(selector.get("match_mode") or "exact")
    for key, attr_name in aliases.items():
        if key not in selector:
            continue
        expected_values = selector_expected_values(selector.get(key))
        actual = str(attributes.get(attr_name) or "")
        if key in {"clickable", "enabled"}:
            if not any(actual.casefold() == expected.casefold() for expected in expected_values):
                return False
        elif not any(xml_selector_value_matches(expected, actual, match_mode) for expected in expected_values):
            return False
    return True


def selector_expected_values(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item or "") for item in value]
    return [str(value or "")]


def xml_selector_value_matches(expected: str, actual: str, match_mode: str) -> bool:
    if match_mode == "contains":
        return normalize(expected) in normalize(actual)
    return actual == expected


def wait_for_edit_text_at_point(
    adb: ADB,
    run_dir: Path,
    step_id: str,
    point: dict[str, Any],
    timeout_seconds: float,
    tolerance_px: int,
) -> bool:
    deadline = time.time() + timeout_seconds
    attempt = 1
    while time.time() < deadline:
        xml_path = run_dir / "xml" / f"{step_id}_wait_{attempt:02d}.xml"
        xml_text = adb.dump_xml(xml_path)
        if xml_has_edit_text_at_point(xml_text, point, tolerance_px):
            return True
        time.sleep(1.0)
        attempt += 1
    return False


def xml_has_edit_text_at_point(xml_text: str, point: dict[str, Any], tolerance_px: int) -> bool:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return False
    target_x = int(point["x"])
    target_y = int(point["y"])
    for node in root.iter("node"):
        if node.attrib.get("class") != "android.widget.EditText":
            continue
        bounds = parse_bounds(node.attrib.get("bounds", ""))
        if not bounds:
            continue
        left, top, right, bottom = bounds
        if left - tolerance_px <= target_x <= right + tolerance_px and top - tolerance_px <= target_y <= bottom + tolerance_px:
            return True
    return False


def parse_bounds(bounds: str) -> tuple[int, int, int, int] | None:
    match = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds)
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def tap_until_any_text(
    *,
    adb: ADB,
    run_dir: Path,
    step_id: str,
    point: dict[str, Any],
    texts: list[str],
    tap_texts: list[str],
    max_taps: int,
    wait_between_seconds: float,
    match_mode: str,
    tap_match_mode: str,
    dry_run: bool,
    trace: dict[str, Any],
    initial_xml: str,
    required_package: str = "",
) -> bool:
    trace.update(
        {
            "action": "tap_until_any_text",
            **point_log(point),
            "target_texts": texts,
            "tap_texts": tap_texts,
            "max_taps": max_taps,
            "attempts": [],
        }
    )
    if xml_has_any_text(initial_xml, texts, match_mode=match_mode):
        trace["found"] = True
        trace["found_after_taps"] = 0
        return True
    if dry_run:
        trace["found"] = False
        trace["found_after_taps"] = 0
        return False

    current_xml = initial_xml
    for attempt in range(1, max_taps + 1):
        tap_target = first_text_match_center(current_xml, tap_texts, match_mode=tap_match_mode) if tap_texts else None
        if tap_target:
            tap_x = int(tap_target["x"])
            tap_y = int(tap_target["y"])
            tap_source = "text"
        else:
            tap_x = int(point["x"])
            tap_y = int(point["y"])
            tap_source = "point"
        adb.tap(tap_x, tap_y)
        time.sleep(wait_between_seconds)
        xml_path = run_dir / "xml" / f"{step_id}_after_tap_{attempt:02d}.xml"
        xml_text = adb.dump_xml(xml_path)
        current_xml = xml_text
        packages = xml_packages(xml_text)
        found = xml_has_any_text(xml_text, texts, match_mode=match_mode)
        attempt_trace = {
            "tap": attempt,
            "tap_source": tap_source,
            "tap_x": tap_x,
            "tap_y": tap_y,
            "tap_text_match": tap_target,
            "xml": str(xml_path),
            "found": found,
            "packages": packages,
        }
        if required_package:
            package_check = confirm_expected_package(
                adb=adb,
                run_dir=run_dir,
                step_id=step_id,
                expected_package=required_package,
                initial_xml_text=xml_text,
                phase=f"after_repeated_tap_{attempt:02d}",
            )
            attempt_trace["foreground_check"] = package_check
            if not package_check["found"]:
                attempt_trace["foreground_package_mismatch"] = True
                trace["attempts"].append(attempt_trace)
                trace["found"] = False
                trace["found_after_taps"] = attempt
                trace["foreground_package_mismatch"] = package_check
                package_text = package_check.get("foreground_package") or ", ".join(package_check.get("xml_packages") or []) or "none"
                raise WorkflowError(
                    f"{step_id}: foreground changed during repeated tap; "
                    f"expected {required_package}, current package: {package_text}."
                )
            confirmed_xml_text = str(package_check.get("xml_text") or "")
            if not found and confirmed_xml_text and confirmed_xml_text != xml_text:
                current_xml = confirmed_xml_text
                packages = xml_packages(confirmed_xml_text)
                found = xml_has_any_text(confirmed_xml_text, texts, match_mode=match_mode)
                attempt_trace["packages"] = packages
                attempt_trace["found"] = found
                attempt_trace["found_after_foreground_retry"] = found
        trace["attempts"].append(attempt_trace)
        if found:
            trace["found"] = True
            trace["found_after_taps"] = attempt
            return True

    trace["found"] = False
    trace["found_after_taps"] = max_taps
    return False


def xml_has_any_text(xml_text: str, texts: list[str], *, match_mode: str = "contains") -> bool:
    needles = [normalize(text) for text in texts if normalize(text)]
    if not needles:
        return False
    values = xml_text_values(xml_text)
    if values:
        if match_mode in {"exact", "node_exact"}:
            return any(value == needle for value in values for needle in needles)
        return any(needle in value for value in values for needle in needles)
    haystack = normalize(xml_text)
    return any(needle in haystack for needle in needles)


def xml_text_values(xml_text: str) -> list[str]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    values: list[str] = []
    for node in root.iter("node"):
        for attr in ("text", "content-desc"):
            value = normalize(node.attrib.get(attr, ""))
            if value:
                values.append(value)
    return values


def xml_has_package(xml_text: str, package: str) -> bool:
    return package in xml_packages(xml_text)


def xml_packages(xml_text: str) -> list[str]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    packages = sorted({node.attrib.get("package", "") for node in root.iter("node") if node.attrib.get("package", "")})
    return packages


def normalize(value: Any) -> str:
    return str(value or "").casefold().replace(" ", "")


def build_context(args: argparse.Namespace, profile: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    inputs = profile.get("inputs", {})
    product_search_title = args.product_search_title
    search_title_max_chars = int(inputs.get("product_search_title_max_chars") or DEFAULT_PRODUCT_SEARCH_TITLE_MAX_CHARS)
    if search_title_max_chars > 0:
        search_title_max_chars = min(search_title_max_chars, DEFAULT_PRODUCT_SEARCH_TITLE_MAX_CHARS)
    if search_title_max_chars > 0:
        product_search_title = truncate_text_to_normalized_prefix(product_search_title, search_title_max_chars)
    product_publish_name = args.product_publish_name or args.product_search_title
    publish_name_max_chars = int(inputs.get("product_publish_name_max_chars") or 0)
    if publish_name_max_chars > 0:
        product_publish_name = product_publish_name[:publish_name_max_chars]
    return {
        "run_dir": str(run_dir),
        "caption": args.caption,
        "product_search_title": product_search_title,
        "product_publish_name": product_publish_name,
        "remote_video_dir": str(inputs.get("remote_video_dir") or "/sdcard/DCIM/Camera"),
        "remote_video_path": "",
        "managed_gallery_video_dir": managed_gallery_video_dir(profile),
        "allow_publish": "1" if args.allow_publish else "",
        "stop_before_final_publish": bool(getattr(args, "stop_before_final_publish", False)),
        "stopped_before_final_publish": False,
        "stopped_before_final_publish_step": "",
        "publish_mode": getattr(args, "publish_mode", "immediate"),
        "scheduled_at": getattr(args, "scheduled_at", ""),
        "schedule_timezone": getattr(args, "schedule_timezone", ""),
        "schedule_date": "",
        "schedule_time": "",
        "schedule_configured": False,
        "schedule_configured_verified": False,
        "schedule_auto_extensions": [],
        "capture_mode": args.capture_mode,
        "wait_scale": args.wait_scale,
    }


def ensure_showcase_schedule_config(config: dict[str, Any], timezone_name: str = "") -> None:
    pipeline = config.setdefault("pipeline", {})
    schedule = pipeline.setdefault("schedule", {})
    if timezone_name:
        schedule["timezone"] = timezone_name
    schedule.setdefault("timezone", "America/Sao_Paulo")
    schedule.setdefault("auto_extend_if_within_minutes", 40)
    schedule.setdefault("auto_extend_by_minutes", 30)
    native_schedule = pipeline.setdefault("native_schedule", {})
    native_schedule["strategy"] = "phone_clock_wheel"
    native_schedule.setdefault("picker_open_wait_attempts", 8)
    native_schedule.setdefault("picker_open_wait_seconds", 0.75)
    native_schedule.setdefault("configured_wait_attempts", 6)
    native_schedule.setdefault("configured_wait_seconds", 0.75)
    native_schedule.setdefault("configured_time_tolerance_minutes", 15)
    native_schedule.setdefault("require_target_text_after_confirm", True)
    wheel = native_schedule.setdefault("wheel", {})
    wheel["initial_source"] = "phone_clock"
    wheel["initial_offset_minutes"] = 30
    wheel["initial_rounding"] = "floor_minute"
    wheel["clock_boundary_guard_seconds"] = 5
    wheel["linear_no_wrap"] = True
    wheel["verify_after_confirm"] = False
    wheel["verification_retries"] = 0


def apply_showcase_schedule_points(config: dict[str, Any], profile: dict[str, Any]) -> None:
    points = profile.get("coordinate_points", {}) if isinstance(profile.get("coordinate_points"), dict) else {}
    wheel = config.setdefault("pipeline", {}).setdefault("native_schedule", {}).setdefault("wheel", {})
    mapping = {
        "native_schedule_date_column": ("date_x_ratio", None),
        "native_schedule_hour_column": ("hour_x_ratio", None),
        "native_schedule_minute_column": ("minute_x_ratio", None),
        "native_schedule_increase_start": (None, "increase_start_y_ratio"),
        "native_schedule_increase_end": (None, "increase_end_y_ratio"),
        "schedule_confirm": ("confirm_x_ratio", "confirm_y_ratio"),
    }
    for point_name, (x_key, y_key) in mapping.items():
        point = points.get(point_name)
        if not isinstance(point, dict):
            continue
        if x_key and point.get("x_ratio") is not None:
            wheel[x_key] = float(point["x_ratio"])
        if y_key and point.get("y_ratio") is not None:
            wheel[y_key] = float(point["y_ratio"])


def verify_showcase_schedule_configured_from_current_ui(
    *,
    adb: ADB,
    run_dir: Path,
    step_id: str,
    step: dict[str, Any],
    initial_screen: dict[str, Any],
    context: dict[str, Any],
    dry_run: bool,
) -> dict[str, Any]:
    max_attempts = int(step.get("configured_verify_attempts") or 4)
    wait_seconds = float(step.get("configured_verify_wait_seconds") or 0.75) * float(context.get("wait_scale") or 1.0)
    done_texts = clean_texts(step.get("schedule_done_texts")) + ["\u5b8c\u6210", "Done"]
    current = ensure_screen(adb, run_dir, f"{step_id}_initial", initial_screen)
    attempts: list[dict[str, Any]] = []
    last_error = ""

    for attempt_index in range(max(1, max_attempts)):
        xml_text = str(current.get("xml_text") or "")
        page_type = classify_schedule_verification_page(xml_text)
        candidates = schedule_datetime_candidates_from_xml(xml_text)
        attempt: dict[str, Any] = {
            "attempt": attempt_index,
            "page_type": page_type,
            "screenshot": str(current.get("screenshot") or ""),
            "xml": str(current.get("xml") or ""),
            "candidate_times": [candidate.isoformat(timespec="minutes") for candidate in candidates],
            "candidate_checks": schedule_candidate_checks(candidates, context),
        }
        attempts.append(attempt)

        if page_type == "schedule_dialog":
            done_tap = first_text_match_center(xml_text, done_texts, match_mode="exact")
            attempt["handled_schedule_dialog"] = bool(done_tap)
            if done_tap:
                attempt["done_tap"] = done_tap
                if not dry_run:
                    adb.tap(int(done_tap["x"]), int(done_tap["y"]))
            if attempt_index >= max_attempts - 1:
                last_error = "schedule dialog was still open after tapping Done."
                break
            time.sleep(wait_seconds)
            current = capture(adb, run_dir, f"{step_id}_after_done_{attempt_index + 1:02d}")
            continue

        try:
            verification = verify_showcase_schedule_configured(xml_text, context)
            verification["attempts"] = attempts
            verification["page_type"] = page_type
            return verification
        except WorkflowError as exc:
            last_error = str(exc)
            attempt["error"] = last_error
            if attempt_index >= max_attempts - 1:
                break
            time.sleep(wait_seconds)
            current = capture(adb, run_dir, f"{step_id}_retry_{attempt_index + 1:02d}")

    target = f"{context.get('schedule_date', '')} {context.get('schedule_time', '')}".strip()
    candidate_times = [
        candidate
        for attempt in attempts
        for candidate in attempt.get("candidate_times", [])
    ]
    current_page_type = attempts[-1].get("page_type", "unknown") if attempts else "unknown"
    raise WorkflowError(
        "verify_schedule_configured: scheduled publish time could not be verified after retries; "
        f"target={target}, candidates={candidate_times}, current_page_type={current_page_type}, last_error={last_error}"
    )


def classify_schedule_verification_page(xml_text: str) -> str:
    if not xml_text:
        return "unknown"
    has_done = xml_has_any_text(xml_text, ["\u5b8c\u6210", "Done"], match_mode="exact")
    has_schedule_dialog_marker = xml_has_any_text(
        xml_text,
        ["\u53d1\u5e03\u65f6\u95f4", "\u9884\u7ea6", "Publish time", "Schedule"],
        match_mode="contains",
    )
    if has_done and has_schedule_dialog_marker:
        return "schedule_dialog"
    if schedule_datetime_candidates_from_xml(xml_text):
        return "publish_or_more_settings"
    return "unknown"


def schedule_candidate_checks(candidates: list[datetime], context: dict[str, Any]) -> list[dict[str, Any]]:
    target_date = str(context.get("schedule_date") or "").strip()
    target_time = str(context.get("schedule_time") or "").strip()
    if not target_date or not target_time:
        return []
    try:
        target_dt = datetime.fromisoformat(f"{target_date}T{target_time}")
    except ValueError:
        return []
    checks: list[dict[str, Any]] = []
    for candidate in candidates:
        delta_seconds = abs((candidate - target_dt).total_seconds())
        checks.append(
            {
                "date_iso": candidate.strftime("%Y-%m-%d"),
                "time_24": candidate.strftime("%H:%M"),
                "delta_minutes": round(delta_seconds / 60, 2),
            }
        )
    return checks


def verify_showcase_schedule_configured(xml_text: str, context: dict[str, Any]) -> dict[str, Any]:
    target_date = str(context.get("schedule_date") or "").strip()
    target_time = str(context.get("schedule_time") or "").strip()
    if not target_date or not target_time:
        raise WorkflowError("verify_schedule_configured: target schedule date/time is missing.")
    try:
        target_dt = datetime.fromisoformat(f"{target_date}T{target_time}")
    except ValueError as exc:
        raise WorkflowError(
            f"verify_schedule_configured: invalid target schedule date/time: {target_date} {target_time}."
        ) from exc

    tolerance_minutes = int(context.get("schedule_verification_tolerance_minutes") or 15)
    tolerance_seconds = max(0, tolerance_minutes) * 60
    candidates = schedule_datetime_candidates_from_xml(xml_text)
    if not candidates:
        raise WorkflowError(
            "verify_schedule_configured: could not read scheduled publish time from current UI."
        )

    checked: list[dict[str, Any]] = []
    for candidate in candidates:
        delta_seconds = abs((candidate - target_dt).total_seconds())
        checked.append(
            {
                "date_iso": candidate.strftime("%Y-%m-%d"),
                "time_24": candidate.strftime("%H:%M"),
                "delta_minutes": round(delta_seconds / 60, 2),
            }
        )
        if delta_seconds <= tolerance_seconds:
            return {
                "found": True,
                "target_date": target_date,
                "target_time": target_time,
                "tolerance_minutes": tolerance_minutes,
                "matched": checked[-1],
                "candidates": checked,
            }

    raise WorkflowError(
        "verify_schedule_configured: scheduled publish time on UI is outside tolerance; "
        f"target={target_date} {target_time}, tolerance_minutes={tolerance_minutes}, candidates={checked}."
    )


def schedule_datetime_candidates_from_xml(xml_text: str) -> list[datetime]:
    if not xml_text:
        return []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    values: list[str] = []
    for node in root.iter("node"):
        for key in ("text", "content-desc"):
            value = str(node.attrib.get(key) or "").strip()
            if value:
                values.append(value)

    candidates: list[datetime] = []
    seen: set[str] = set()
    pattern = re.compile(
        r"\b(20\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})日?\s+([01]?\d|2[0-3])[:：]([0-5]\d)\b"
    )
    for value in values:
        for match in pattern.finditer(value):
            year, month, day, hour, minute = (int(part) for part in match.groups())
            try:
                candidate = datetime(year, month, day, hour, minute)
            except ValueError:
                continue
            key = candidate.isoformat(timespec="minutes")
            if key in seen:
                continue
            seen.add(key)
            candidates.append(candidate)
    return candidates


def set_showcase_schedule_datetime(
    *,
    adb: ADB,
    config: dict[str, Any],
    context: dict[str, Any],
    state_path: Path,
    run_dir: Path,
    dry_run: bool,
) -> dict[str, Any]:
    if str(context.get("publish_mode") or "immediate") != "scheduled":
        return {"action": "skip_schedule_for_immediate_mode"}

    timezone_name = str(context.get("schedule_timezone") or config.get("pipeline", {}).get("schedule", {}).get("timezone") or "America/Sao_Paulo")
    ensure_showcase_schedule_config(config, timezone_name)
    state = {
        "run_id": state_path.parent.name,
        "run_dir": str(run_dir),
        "state_path": str(state_path),
        "dry_run": dry_run,
        "publish_mode": "scheduled",
        "scheduled_at": str(context.get("scheduled_at") or ""),
        "schedule_date": str(context.get("schedule_date") or ""),
        "schedule_time": str(context.get("schedule_time") or ""),
        "schedule_timezone": timezone_name,
        "completed_steps": [],
    }
    schedule = resolve_publish_schedule(state, config)
    native_config = config["pipeline"].get("native_schedule", {})
    try:
        wheel_trace = run_wheel_strategy(
            adb=adb,
            state=state,
            config=config,
            schedule=schedule,
            native_config=native_config,
        )
    except Exception as exc:  # noqa: BLE001
        raise WorkflowError(f"set_schedule_datetime: {exc}") from exc

    verification_schedule = schedule_state_for_verified_page(schedule, wheel_trace)
    context["scheduled_at"] = verification_schedule["target_iso"]
    context["schedule_date"] = verification_schedule["date_iso"]
    context["schedule_time"] = verification_schedule["time_24"]
    context["schedule_timezone"] = verification_schedule["timezone"]
    context["schedule_auto_extensions"] = wheel_trace.get("auto_extensions", schedule.get("auto_extensions", []))
    context["schedule_configured"] = True
    return {
        "strategy": wheel_trace.get("strategy", native_config.get("strategy", "")),
        "initial": wheel_trace.get("initial"),
        "attempts": wheel_trace.get("attempts", []),
        "auto_extensions": context["schedule_auto_extensions"],
        "verification": wheel_trace.get("verification", ""),
        "target_iso": context["scheduled_at"],
        "date_iso": context["schedule_date"],
        "time_24": context["schedule_time"],
        "timezone": context["schedule_timezone"],
    }


def wait_for_schedule_picker_open(
    *,
    adb: ADB,
    config: dict[str, Any],
    context: dict[str, Any],
    run_dir: Path,
    state_path: Path,
    step_id: str,
    timeout_seconds: float,
    poll_seconds: float,
    dry_run: bool,
) -> dict[str, Any]:
    if dry_run:
        return {"is_open": True, "dry_run": True, "attempts": []}
    state = {
        "run_id": state_path.parent.name,
        "run_dir": str(run_dir),
        "state_path": str(state_path),
        "dry_run": dry_run,
        "publish_mode": str(context.get("publish_mode") or "scheduled"),
        "scheduled_at": str(context.get("scheduled_at") or ""),
        "schedule_timezone": str(context.get("schedule_timezone") or config.get("pipeline", {}).get("schedule", {}).get("timezone") or "America/Sao_Paulo"),
        "completed_steps": [],
    }
    deadline = time.monotonic() + max(0.1, timeout_seconds)
    attempts: list[dict[str, Any]] = []
    attempt_no = 0
    while time.monotonic() <= deadline:
        attempt_no += 1
        screen = capture_screen(adb=adb, state=state, step_name=f"{step_id}_{attempt_no}", config=config)
        signal = native_schedule_picker_signal(screen)
        attempts.append(
            {
                "attempt": attempt_no,
                "is_open": bool(signal.get("is_open")),
                "seekbar_count": int(signal.get("seekbar_count") or 0),
                "screenshot": str(screen["screenshot_path"]),
            }
        )
        if signal.get("is_open"):
            return {"is_open": True, "attempts": attempts, "picker": signal}
        time.sleep(max(0.1, poll_seconds) * float(context.get("wait_scale") or 1.0))
    return {"is_open": False, "attempts": attempts}


def run_device_preflight(
    *,
    args: argparse.Namespace,
    profile: dict[str, Any],
    run_dir: Path,
    context: dict[str, Any],
    serial: str,
) -> dict[str, Any]:
    screen = profile.get("screen", {}) if isinstance(profile.get("screen"), dict) else {}
    options = DevicePreflightOptions(
        serial=serial,
        adb_path=Path(args.adb),
        run_dir=run_dir,
        phase="startup",
        app_package=app_package(profile, "tiktok"),
        remote_video_dir=str(context.get("remote_video_dir") or "/sdcard/DCIM/Camera"),
        expected_width=int(screen.get("width") or 0),
        expected_height=int(screen.get("height") or 0),
        skip_wake=bool(args.skip_wake),
        startup=not bool(args.from_step),
        check_media_dir=bool(args.video),
        check_input_injection=True,
        require_ready=True,
    )
    return require_device_ready(options)


def write_showcase_state(
    state_path: Path,
    *,
    args: argparse.Namespace,
    context: dict[str, Any],
    completed_steps: list[str],
) -> None:
    write_json(
        state_path,
        {
            "run_id": args.run_id,
            "run_dir": str(context.get("run_dir") or state_path.parent),
            "state_path": str(state_path),
            "video_path": args.video,
            "remote_video_path": str(context.get("remote_video_path") or ""),
            "caption": args.caption,
            "account_type": "showcase",
            "product_search_title": str(context.get("product_search_title") or ""),
            "product_publish_name": str(context.get("product_publish_name") or ""),
            "dry_run": bool(args.dry_run),
            "allow_publish": bool(args.allow_publish),
            "stop_before_final_publish": bool(context.get("stop_before_final_publish", False)),
            "stopped_before_final_publish": bool(context.get("stopped_before_final_publish", False)),
            "stopped_before_final_publish_step": str(context.get("stopped_before_final_publish_step") or ""),
            "publish_mode": str(context.get("publish_mode") or args.publish_mode),
            "schedule_time": str(context.get("schedule_time") or ""),
            "schedule_date": str(context.get("schedule_date") or ""),
            "schedule_timezone": str(context.get("schedule_timezone") or args.schedule_timezone or ""),
            "scheduled_at": str(context.get("scheduled_at") or args.scheduled_at or ""),
            "schedule_configured": bool(context.get("schedule_configured", False)),
            "schedule_configured_verified": bool(context.get("schedule_configured_verified", False)),
            "schedule_auto_extensions": context.get("schedule_auto_extensions", []),
            "completed_steps": [step for step in completed_steps if step],
        },
    )


def redact_context(context: dict[str, Any]) -> dict[str, Any]:
    return dict(context)


def context_value(context: dict[str, Any], key: str) -> str:
    return str(context.get(key) or "")


def app_package(profile: dict[str, Any], app: str) -> str:
    try:
        return str(profile["apps"][app]["package"])
    except KeyError as exc:
        raise WorkflowError(f"App not configured in profile: {app}") from exc


def coordinate(profile: dict[str, Any], point_name: str) -> dict[str, Any]:
    try:
        point = dict(profile["coordinate_points"][point_name])
    except KeyError as exc:
        raise WorkflowError(f"Point not configured in profile: {point_name}") from exc
    point["name"] = point_name
    return point


def point_from_step(profile: dict[str, Any], step: dict[str, Any]) -> dict[str, Any]:
    return coordinate(profile, str(step["point"]))


def slice_steps(steps: list[dict[str, Any]], from_step: str, to_step: str) -> list[dict[str, Any]]:
    if from_step:
        start = next((index for index, step in enumerate(steps) if step["id"] == from_step), None)
        if start is None:
            raise WorkflowError(f"from-step not found: {from_step}")
        steps = steps[start:]
    if to_step:
        end = next((index for index, step in enumerate(steps) if step["id"] == to_step), None)
        if end is None:
            raise WorkflowError(f"to-step not found: {to_step}")
        steps = steps[: end + 1]
    return steps


def filter_steps_for_publish_mode(steps: list[dict[str, Any]], publish_mode: str) -> list[dict[str, Any]]:
    clean_mode = str(publish_mode or "immediate")
    selected: list[dict[str, Any]] = []
    for step in steps:
        if str(step.get("id") or "") in SKIPPED_RUNTIME_STEP_IDS:
            continue
        modes = step.get("publish_modes")
        if not modes:
            selected.append(step)
            continue
        allowed = {str(mode) for mode in modes}
        if clean_mode in allowed:
            selected.append(step)
    return selected


def print_step_result(trace: dict[str, Any]) -> None:
    suffix = f" [{trace.get('duration_seconds')}s]" if trace.get("duration_seconds") is not None else ""
    if trace.get("action") in {"tap", "long_press", "open_recents_and_tap"}:
        print(f"{trace['id']}: {trace['action']} ({trace['x']}, {trace['y']}){suffix}")
    else:
        print(f"{trace['id']}: {trace.get('action', trace['type'])}{suffix}")


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"JSON file must contain an object: {path}")
    return data


def adb_transient_failure(result: subprocess.CompletedProcess) -> bool:
    stderr = result.stderr if isinstance(result.stderr, str) else (result.stderr or b"").decode("utf-8", "replace")
    stdout = result.stdout if isinstance(result.stdout, str) else (result.stdout or b"").decode("utf-8", "replace")
    text = (stderr + "\n" + stdout).casefold()
    return any(
        marker in text
        for marker in (
            "device '" ,
            "not found",
            "no devices/emulators found",
            "device offline",
            "more than one device",
        )
    )


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_trace(run_dir: Path, step_id: str, trace: dict[str, Any]) -> None:
    write_json(run_dir / "traces" / f"{step_id}.json", trace)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
