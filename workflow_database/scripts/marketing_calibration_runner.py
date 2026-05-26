from __future__ import annotations

import argparse
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_ROOT = PROJECT_ROOT / "workflow_database"
RUN_ROOT = WORKFLOW_ROOT / "runs"
DEFAULT_ADB = PROJECT_ROOT / "platform-tools" / "adb.exe"
DEFAULT_BASE_PROFILE = WORKFLOW_ROOT / "devices" / "samsung_SM-A5260" / "marketing_tiktok_studio.json"
DEFAULT_VIDEO = PROJECT_ROOT / "test" / "营销号1" / "营销号1.mp4"
DEFAULT_CAPTION_FILE = PROJECT_ROOT / "test" / "营销号1" / "营销号1.txt"
DEFAULT_TIMEZONE = "America/Sao_Paulo"
DEFAULT_STOP_AT = "verify_schedule_configured"

TARGETS = {
    "SERIAL_2209116AG": {"brand": "Redmi", "model": "2209116AG", "width": 1080, "height": 2400, "density": 440},
    "SERIAL_2312DRA50G": {"brand": "Redmi", "model": "2312DRA50G", "width": 1220, "height": 2712, "density": 480},
    "SERIAL_22101316UG": {"brand": "Redmi", "model": "22101316UG", "width": 1080, "height": 2400, "density": 440},
}
DEFAULT_SERIALS = ",".join(TARGETS)
TIKTOK_STUDIO_PACKAGE = "com.ss.android.tt.creator"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from execution_queue.idle_wake import is_awake, is_locked, read_power_state, wake_idle_phone  # noqa: E402
from mobile_phone_library.adb_manager import ADBManager  # noqa: E402


@dataclass(frozen=True)
class Target:
    serial: str
    brand: str
    model: str
    width: int
    height: int
    density: int
    profile_path: Path

    @property
    def run_slug(self) -> str:
        safe_serial = re.sub(r"[^A-Za-z0-9._-]+", "_", self.serial)
        return f"xiaomi-marketing-calibration-{self.model}-{safe_serial}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run three Redmi/Xiaomi TikTok Studio marketing calibration workflows in parallel.")
    parser.add_argument("--serials", default=DEFAULT_SERIALS, help="Comma-separated ADB serials. Replace the example TARGETS map with local device serials before live use.")
    parser.add_argument("--video", default=str(DEFAULT_VIDEO), help="Local video to push to each phone.")
    parser.add_argument("--caption-file", default=str(DEFAULT_CAPTION_FILE), help="Plain text material file for caption and publish time.")
    parser.add_argument("--caption", default="", help="Direct caption override.")
    parser.add_argument("--scheduled-at", default="", help="ISO scheduled publish datetime override.")
    parser.add_argument("--schedule-timezone", default=DEFAULT_TIMEZONE, help="IANA timezone for the scheduled time.")
    parser.add_argument("--parallel", type=int, default=3, help="Maximum number of phones to run at once.")
    parser.add_argument("--stagger-seconds", type=float, default=12.0, help="Delay between starting phone workflows.")
    parser.add_argument("--stop-at", default=DEFAULT_STOP_AT, help="Last marketing workflow step to execute.")
    parser.add_argument("--adb", default=str(DEFAULT_ADB), help="ADB executable path.")
    parser.add_argument("--dry-run", action="store_true", help="Do not tap/type; still writes run artifacts and candidate events.")
    parser.add_argument("--allow-publish", action="store_true", help="Allow the final scheduled publish tap.")
    parser.add_argument("--no-save-profile-coordinates", action="store_true", help="Keep calibration events only; do not write verified coordinates into profiles.")
    parser.add_argument("--prepare-only", action="store_true", help="Create/update Xiaomi profiles and index, then exit.")
    parser.add_argument("--preflight-only", action="store_true", help="Run ADB/screen/app/media preflight, then exit.")
    parser.add_argument("--skip-preflight", action="store_true", help="Start workflows without the ADB preflight.")
    parser.add_argument("--skip-wake", action="store_true", help="Do not wake dozing/keyguard devices during preflight.")
    args = parser.parse_args()

    os.environ["GROUP_CONTROL_ADB_DISABLE_KILL_SERVER"] = "1"
    video = Path(args.video).resolve()
    if not video.exists():
        raise FileNotFoundError(f"Video not found: {video}")
    material = read_material(Path(args.caption_file), timezone_name=args.schedule_timezone)
    caption = args.caption or material["caption"]
    scheduled_at = args.scheduled_at or material["scheduled_at"]
    if not scheduled_at:
        raise ValueError("Missing scheduled time. Pass --scheduled-at or include 发布时间 in the material file.")

    targets = ensure_xiaomi_profiles(parse_serials(args.serials))
    if args.prepare_only:
        print(json.dumps({"prepared_profiles": [str(target.profile_path) for target in targets]}, indent=2, ensure_ascii=False))
        return 0

    if not args.skip_preflight:
        preflight_results = run_preflight(targets, adb_path=Path(args.adb), skip_wake=args.skip_wake)
        print(json.dumps({"preflight": preflight_results}, indent=2, ensure_ascii=False))
        if not all(result.get("ready") for result in preflight_results):
            return 2
        if args.preflight_only:
            return 0
    elif args.preflight_only:
        raise ValueError("--preflight-only cannot be used with --skip-preflight")

    return run_parallel_workflows(
        targets=targets,
        args=args,
        video=video,
        caption=caption,
        scheduled_at=scheduled_at,
        save_profile_coordinates=not args.no_save_profile_coordinates,
    )


def parse_serials(value: str) -> list[str]:
    serials = [part.strip() for part in value.split(",") if part.strip()]
    if not serials:
        raise ValueError("At least one serial is required.")
    unknown = [serial for serial in serials if serial not in TARGETS]
    if unknown:
        raise ValueError(f"Unknown Xiaomi serial(s): {', '.join(unknown)}")
    return serials


def ensure_xiaomi_profiles(serials: list[str]) -> list[Target]:
    base_profile = read_json(DEFAULT_BASE_PROFILE)
    targets: list[Target] = []
    index = read_json(WORKFLOW_ROOT / "index.json")
    profiles = [profile for profile in index.get("profiles", []) if isinstance(profile, dict)]

    for serial in serials:
        spec = TARGETS[serial]
        model = str(spec["model"])
        profile_rel = f"devices/redmi_{model}/marketing_tiktok_studio.json"
        profile_path = WORKFLOW_ROOT / profile_rel
        target = Target(
            serial=serial,
            brand=str(spec["brand"]),
            model=model,
            width=int(spec["width"]),
            height=int(spec["height"]),
            density=int(spec["density"]),
            profile_path=profile_path,
        )
        targets.append(target)
        if not profile_path.exists():
            write_json(profile_path, build_seed_profile(base_profile, target))

        entry = {
            "profile_id": profile_id(target),
            "phone_id": "",
            "adb_serial": target.serial,
            "device_model": target.model,
            "brand": target.brand,
            "account_type": "marketing",
            "app_package": TIKTOK_STUDIO_PACKAGE,
            "profile_path": profile_rel,
            "status": "draft_requires_calibration",
        }
        profiles = [
            profile
            for profile in profiles
            if str(profile.get("profile_path") or "") != profile_rel
            and str(profile.get("profile_id") or "") != entry["profile_id"]
        ]
        profiles.append(entry)

    index["profiles"] = profiles
    write_json(WORKFLOW_ROOT / "index.json", index)
    return targets


def build_seed_profile(base_profile: dict[str, Any], target: Target) -> dict[str, Any]:
    profile = json.loads(json.dumps(base_profile, ensure_ascii=False))
    profile["profile_id"] = profile_id(target)
    profile["status"] = "draft_requires_calibration"
    profile["account_type"] = "marketing"
    profile["created_at"] = datetime.now(timezone.utc).date().isoformat()
    profile["purpose"] = "Redmi TikTok Studio marketing scheduled publish calibration profile"
    profile["source"] = "seed_scaled_from_samsung_SM-A5260_marketing_tiktok_studio"
    profile["device"] = {
        "brand": target.brand,
        "model": target.model,
        "android_release": "",
        "known_phone_id": "",
        "known_adb_serial": target.serial,
        "device_name": f"{target.brand} {target.model}",
        "match_keys": {
            "ro.product.brand": target.brand,
            "ro.product.model": target.model,
        },
    }
    profile["apps"]["tiktok_studio"]["package"] = TIKTOK_STUDIO_PACKAGE
    profile["apps"]["gallery"] = {"package": "com.miui.gallery", "launch_mode": "monkey_launcher"}
    profile["screen"] = {
        "orientation": "portrait",
        "width": target.width,
        "height": target.height,
        "density": target.density,
        "require_exact_size": True,
    }
    profile["runner"]["script"] = "workflow_database/scripts/run_marketing_workflow.py"
    profile["runner"]["supports_coordinate_writeback"] = True
    profile["runner"]["calibration_runner"] = "workflow_database/scripts/marketing_calibration_runner.py"
    profile["coordinate_points"] = scale_points(profile.get("coordinate_points", {}), target.width, target.height)
    profile["coordinate_points"]["studio_create_entry"] = xiaomi_studio_create_entry(target)
    profile["notes"] = [
        f"Seeded for {target.brand} {target.model} / {target.serial}.",
        "Coordinates are ratio-scaled seeds; verified calibration writes evidence-backed points.",
        "Profile remains draft_requires_calibration until manual review approves real publishing.",
    ]
    return profile


def xiaomi_studio_create_entry(target: Target) -> dict[str, Any]:
    return {
        "x": int(target.width * 0.076),
        "y": int(target.height * 0.0667),
        "x_ratio": 0.076,
        "y_ratio": 0.0667,
        "description": "TikTok Studio top-left plus Create entry on Xiaomi Studio home/inspiration feed",
        "calibration_status": "seed_verified_from_2209116AG_left_plus_capture",
        "verified_at": "",
    }


def scale_points(points: dict[str, Any], width: int, height: int) -> dict[str, Any]:
    scaled: dict[str, Any] = {}
    for name, value in points.items():
        if not isinstance(value, dict):
            continue
        x_ratio = float(value.get("x_ratio") or (float(value.get("x", 0)) / 1080.0))
        y_ratio = float(value.get("y_ratio") or (float(value.get("y", 0)) / 2400.0))
        scaled[name] = {
            **{key: item for key, item in value.items() if key not in {"x", "y", "calibration_status", "verified_at", "calibration_evidence"}},
            "x": int(width * x_ratio),
            "y": int(height * y_ratio),
            "x_ratio": round(x_ratio, 6),
            "y_ratio": round(y_ratio, 6),
            "calibration_status": "seed_scaled_from_samsung_SM-A5260_marketing",
            "verified_at": "",
        }
    return scaled


def profile_id(target: Target) -> str:
    return f"redmi_{target.model}_marketing_tiktok_studio_{target.serial}_v1"


def run_preflight(targets: list[Target], *, adb_path: Path, skip_wake: bool) -> list[dict[str, Any]]:
    adb = ADBManager(adb_path)
    adb.start_server()
    online = {device.serial: device.state for device in adb.devices()}
    results = []
    for target in targets:
        result: dict[str, Any] = {
            "serial": target.serial,
            "model": target.model,
            "expected_screen": f"{target.width}x{target.height}",
            "device_state": online.get(target.serial, "missing"),
            "ready": False,
        }
        if online.get(target.serial) != "device":
            result["reason"] = "adb device is not online/authorized"
            results.append(result)
            continue

        awake_before = is_awake(read_power_state(adb, target.serial))
        locked_before = is_locked(adb, target.serial)
        result["awake_before"] = awake_before
        result["locked_before"] = locked_before
        if (not awake_before or locked_before) and not skip_wake:
            wake_result = wake_idle_phone(target.serial)
            result["wake"] = {
                "success": wake_result.success,
                "awake_after": wake_result.awake_after,
                "locked_after": wake_result.locked_after,
                "actions": list(wake_result.actions),
                "error": wake_result.error,
            }
        elif not awake_before or locked_before:
            result["reason"] = "screen is not awake/unlocked and --skip-wake was set"
            results.append(result)
            continue

        width, height = read_screen_size(adb, target.serial)
        result["screen"] = f"{width}x{height}"
        result["package_exists"] = adb.package_exists(target.serial, TIKTOK_STUDIO_PACKAGE)
        result["screenshot_ok"] = adb.screenshot_ok(target.serial)
        result["media_dir_writable"] = adb.media_dir_writable(target.serial, "/sdcard/DCIM/Camera")
        injection = check_input_injection(adb, target.serial)
        result["input_injection_ok"] = injection["ok"]
        if injection["output"]:
            result["input_injection_output"] = injection["output"]
        result["awake_after"] = is_awake(read_power_state(adb, target.serial))
        result["locked_after"] = is_locked(adb, target.serial)
        checks = (
            width == target.width,
            height == target.height,
            result["package_exists"],
            result["screenshot_ok"],
            result["media_dir_writable"],
            result["input_injection_ok"],
            result["awake_after"],
            not result["locked_after"],
        )
        result["ready"] = all(checks)
        if not result["ready"]:
            result["reason"] = "one or more preflight checks failed"
        results.append(result)
    return results


def read_screen_size(adb: ADBManager, serial: str) -> tuple[int, int]:
    output = adb.shell(serial, "wm", "size", timeout=10)
    match = re.search(r"(\d+)x(\d+)", output)
    if not match:
        return 0, 0
    return int(match.group(1)), int(match.group(2))


def check_input_injection(adb: ADBManager, serial: str) -> dict[str, Any]:
    result = adb.run(["shell", "input", "keyevent", "0"], serial=serial, timeout=10)
    output = (result.stdout + result.stderr).strip()
    normalized = output.casefold()
    ok = result.returncode == 0 and "securityexception" not in normalized and "inject_events" not in normalized
    return {
        "ok": ok,
        "output": compact_output(output),
    }


def compact_output(output: str, limit: int = 220) -> str:
    text = re.sub(r"\s+", " ", str(output or "")).strip()
    return text[:limit]


def run_parallel_workflows(
    *,
    targets: list[Target],
    args: argparse.Namespace,
    video: Path,
    caption: str,
    scheduled_at: str,
    save_profile_coordinates: bool,
) -> int:
    run_stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    pending = queue.Queue()
    for target in targets:
        pending.put(target)

    active: list[tuple[Target, subprocess.Popen[str], list[threading.Thread]]] = []
    results: dict[str, int] = {}
    max_parallel = max(1, int(args.parallel))
    last_start_at = 0.0

    while not pending.empty() or active:
        while not pending.empty() and len(active) < max_parallel:
            if last_start_at:
                wait_for = float(args.stagger_seconds) - (time.monotonic() - last_start_at)
                if wait_for > 0:
                    time.sleep(wait_for)
            target = pending.get()
            process, threads = start_phone_workflow(
                target=target,
                args=args,
                video=video,
                caption=caption,
                scheduled_at=scheduled_at,
                run_stamp=run_stamp,
                save_profile_coordinates=save_profile_coordinates,
            )
            active.append((target, process, threads))
            last_start_at = time.monotonic()

        still_active = []
        for target, process, threads in active:
            code = process.poll()
            if code is None:
                still_active.append((target, process, threads))
                continue
            for thread in threads:
                thread.join(timeout=5)
            results[target.serial] = int(code)
            print(f"PHONE_WORKFLOW_DONE serial={target.serial} code={code}")
        active = still_active
        if active:
            time.sleep(1.0)

    print(json.dumps({"results": results}, indent=2, ensure_ascii=False))
    return 0 if results and all(code == 0 for code in results.values()) else 1


def start_phone_workflow(
    *,
    target: Target,
    args: argparse.Namespace,
    video: Path,
    caption: str,
    scheduled_at: str,
    run_stamp: str,
    save_profile_coordinates: bool,
) -> tuple[subprocess.Popen[str], list[threading.Thread]]:
    run_id = f"{target.run_slug}-{run_stamp}"
    run_dir = RUN_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = run_dir / "pipeline_stdout.log"
    stderr_path = run_dir / "pipeline_stderr.log"
    event_path = run_dir / "calibration_events.jsonl"
    command = [
        sys.executable,
        str(WORKFLOW_ROOT / "scripts" / "run_marketing_workflow.py"),
        str(video),
        "--profile",
        str(target.profile_path),
        "--adb",
        str(Path(args.adb).resolve()),
        "--serial",
        target.serial,
        "--caption",
        caption,
        "--publish-mode",
        "scheduled",
        "--schedule-timezone",
        args.schedule_timezone,
        "--scheduled-at",
        scheduled_at,
        "--to-step",
        args.stop_at,
        "--run-id",
        run_id,
        "--calibration-events",
        str(event_path),
        "--force-stop-before-open",
        "--allow-input-text-fallback",
    ]
    if args.allow_publish:
        command.append("--allow-publish")
    if save_profile_coordinates:
        command.append("--save-profile-coordinates")
    if args.dry_run:
        command.append("--dry-run")
    else:
        command.append("--no-dry-run")

    (run_dir / "pipeline_command.json").write_text(
        json.dumps({"command": command, "cwd": str(PROJECT_ROOT)}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["GROUP_CONTROL_ADB_DISABLE_KILL_SERVER"] = "1"
    process = subprocess.Popen(
        command,
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    threads = [
        threading.Thread(target=stream_output, args=(process.stdout, stdout_path, target.serial, "OUT"), daemon=True),
        threading.Thread(target=stream_output, args=(process.stderr, stderr_path, target.serial, "ERR"), daemon=True),
    ]
    for thread in threads:
        thread.start()
    print(f"PHONE_WORKFLOW_START serial={target.serial} model={target.model} run_dir={run_dir}")
    return process, threads


def stream_output(pipe: Any, log_path: Path, serial: str, stream_name: str) -> None:
    if pipe is None:
        return
    with log_path.open("a", encoding="utf-8", errors="replace") as file:
        for line in pipe:
            file.write(line)
            file.flush()
            print_console_safe(f"[{serial} {stream_name}] {line.rstrip()}")


def print_console_safe(value: str) -> None:
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    safe_value = str(value).encode(encoding, "replace").decode(encoding, "replace")
    print(safe_value)


def read_material(path: Path, *, timezone_name: str) -> dict[str, str]:
    if not path.exists():
        return {"caption": "", "scheduled_at": ""}
    text = read_text_fallback(path)
    caption = parse_caption(text)
    scheduled_at = parse_scheduled_at(text, timezone_name=timezone_name)
    return {"caption": caption, "scheduled_at": scheduled_at}


def read_text_fallback(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "cp936"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def parse_caption(text: str) -> str:
    for line in text.splitlines():
        clean = line.strip()
        if not clean:
            continue
        match = re.match(r"^(?:视频文案|文案|caption)\s*[:：]\s*(.+)$", clean, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not re.search(r"^(?:发布时间|publish)", line.strip(), flags=re.IGNORECASE)
    ]
    return "\n".join(lines).strip()


def parse_scheduled_at(text: str, *, timezone_name: str) -> str:
    patterns = [
        r"(?P<year>\d{4})\s*年\s*(?P<month>\d{1,2})\s*月\s*(?P<day>\d{1,2})\s*日\s*(?P<hour>\d{1,2})[:：](?P<minute>\d{2})",
        r"(?P<year>\d{4})[-/](?P<month>\d{1,2})[-/](?P<day>\d{1,2})\s+(?P<hour>\d{1,2})[:：](?P<minute>\d{2})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        parts = {key: int(value) for key, value in match.groupdict().items()}
        tz = fixed_timezone(timezone_name)
        value = datetime(
            parts["year"],
            parts["month"],
            parts["day"],
            parts["hour"],
            parts["minute"],
            tzinfo=tz,
        )
        return value.isoformat()
    return ""


def fixed_timezone(timezone_name: str) -> timezone:
    offsets = {
        "America/Sao_Paulo": -3,
        "Brazil/East": -3,
        "BRT": -3,
        "Asia/Shanghai": 8,
        "China/Shanghai": 8,
        "CST": 8,
    }
    offset = offsets.get(timezone_name, -3)
    return timezone(timedelta(hours=offset), timezone_name)


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
