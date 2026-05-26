from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import unicodedata
from copy import deepcopy
from pathlib import Path
from typing import Any

from shared.account_rules import product_fields_from_mapping, require_account_workflow
from shared.windows_process import no_window_subprocess_kwargs

from .models import ExecutionClaim, PipelineRunPlan, PipelineRunResult


PROJECT_ROOT = Path(__file__).resolve().parents[1]
UPLOAD_ROOT = PROJECT_ROOT / "upload_system"
UPLOAD_CONFIG = UPLOAD_ROOT / "config.json"
WORKFLOW_ROOT = PROJECT_ROOT / "workflow_database"
WORKFLOW_INDEX = WORKFLOW_ROOT / "index.json"
BUNDLED_ADB = PROJECT_ROOT / "platform-tools" / "adb.exe"
ANDROID_HOME = PROJECT_ROOT / "storage" / "android_home"
DEFAULT_ADB_SERVER_PORT = "5037"
RUN_ROOT = PROJECT_ROOT / "run_log" / "executions"
SHOWCASE_PRODUCT_SEARCH_TITLE_MAX_CHARS = 25


class UploadPipelineAdapter:
    def __init__(self, upload_root: str | Path | None = None) -> None:
        self.upload_root = Path(upload_root).resolve() if upload_root else UPLOAD_ROOT

    def prepare_plan(
        self,
        claim: ExecutionClaim,
        *,
        allow_publish: bool,
        pipeline_dry_run: bool,
        schedule_timezone: str,
        stop_before_final_publish: bool = False,
    ) -> PipelineRunPlan:
        task = claim.task
        validate_pipeline_account_workflow(task)
        attempt_dir = RUN_ROOT / claim.task_id / f"{claim.attempt_no:02d}_{claim.attempt_id}"
        attempt_dir.mkdir(parents=True, exist_ok=True)
        config_path = attempt_dir / "upload_config.json"
        state_path = attempt_dir / "state.json"
        manifest_path = attempt_dir / "task_manifest.json"
        stdout_path = attempt_dir / "pipeline_stdout.log"
        stderr_path = attempt_dir / "pipeline_stderr.log"

        config = self.build_config(task, attempt_dir=attempt_dir, schedule_timezone=schedule_timezone)
        write_json(config_path, config)
        state = self.build_state(
            claim,
            config_path=config_path,
            state_path=state_path,
            attempt_dir=attempt_dir,
            allow_publish=allow_publish,
            pipeline_dry_run=pipeline_dry_run,
            stop_before_final_publish=stop_before_final_publish,
            schedule_timezone=schedule_timezone,
        )
        write_json(state_path, state)
        write_json(
            manifest_path,
            {
                "task_id": claim.task_id,
                "attempt_id": claim.attempt_id,
                "log_id": claim.log_id,
                "attempt_no": claim.attempt_no,
                "task": task,
                "allow_publish": allow_publish,
                "pipeline_dry_run": pipeline_dry_run,
                "stop_before_final_publish": stop_before_final_publish,
                "schedule_timezone": schedule_timezone,
            },
        )

        command = self.build_command(
            task,
            config_path=config_path,
            state_path=state_path,
            allow_publish=allow_publish,
            pipeline_dry_run=pipeline_dry_run,
            stop_before_final_publish=stop_before_final_publish,
            schedule_timezone=schedule_timezone,
        )
        cwd = self.build_cwd(task)
        write_json(attempt_dir / "pipeline_command.json", {"command": command, "cwd": str(cwd)})
        return PipelineRunPlan(
            command=command,
            cwd=cwd,
            attempt_dir=attempt_dir,
            config_path=config_path,
            state_path=state_path,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            manifest_path=manifest_path,
            env=pipeline_env(),
        )

    def run_plan(self, plan: PipelineRunPlan, *, timeout_seconds: int = 7200) -> PipelineRunResult:
        result = subprocess.run(
            plan.command,
            cwd=str(plan.cwd),
            env=plan.env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
            **no_window_subprocess_kwargs(),
        )
        plan.stdout_path.write_text(result.stdout, encoding="utf-8", errors="replace")
        plan.stderr_path.write_text(result.stderr, encoding="utf-8", errors="replace")
        return PipelineRunResult(
            returncode=result.returncode,
            stdout_path=plan.stdout_path,
            stderr_path=plan.stderr_path,
            command=plan.command,
        )

    def build_config(self, task: dict[str, Any], *, attempt_dir: Path, schedule_timezone: str) -> dict[str, Any]:
        config = load_base_config()
        adb = config.setdefault("adb", {})
        adb["path"] = str(BUNDLED_ADB)
        adb["serial"] = str(task.get("phone_adb_serial") or "")
        account_type = normalize_account_type(str(task.get("phone_account_type") or "marketing"))
        profile = select_workflow_profile(task, account_type) if account_type in {"marketing", "showcase"} else None
        if task.get("phone_app_package"):
            adb["app_package"] = str(task["phone_app_package"])
        elif profile and profile.get("app_package"):
            adb["app_package"] = str(profile["app_package"])
        if task.get("phone_remote_video_dir"):
            adb["remote_video_dir"] = str(task["phone_remote_video_dir"])

        pipeline = config.setdefault("pipeline", {})
        pipeline["state_dir"] = str(attempt_dir)
        schedule = pipeline.setdefault("schedule", {})
        schedule["timezone"] = schedule_timezone
        if account_type == "showcase":
            schedule["auto_extend_if_within_minutes"] = 40
            schedule["auto_extend_by_minutes"] = 30
            native_schedule = pipeline.setdefault("native_schedule", {})
            native_schedule["strategy"] = "phone_clock_wheel"
            native_schedule["picker_open_wait_attempts"] = 8
            native_schedule["picker_open_wait_seconds"] = 0.75
            native_schedule["configured_wait_attempts"] = 6
            native_schedule["configured_wait_seconds"] = 0.75
            native_schedule["configured_time_tolerance_minutes"] = 30
            native_schedule["require_target_text_after_confirm"] = True
            wheel = native_schedule.setdefault("wheel", {})
            wheel.update(
                {
                    "initial_source": "phone_clock",
                    "initial_offset_minutes": 30,
                    "initial_rounding": "floor_minute",
                    "clock_boundary_guard_seconds": 5,
                    "linear_no_wrap": True,
                    "verify_after_confirm": False,
                    "verification_retries": 0,
                }
            )
        return config

    def build_state(
        self,
        claim: ExecutionClaim,
        *,
        config_path: Path,
        state_path: Path,
        attempt_dir: Path,
        allow_publish: bool,
        pipeline_dry_run: bool,
        schedule_timezone: str,
        stop_before_final_publish: bool = False,
    ) -> dict[str, Any]:
        task = claim.task
        account_type = normalize_account_type(str(task.get("phone_account_type") or "marketing"))
        product_search_title = (
            showcase_product_search_title(task)
            if account_type == "showcase"
            else str(task.get("product_search_title") or task.get("product_name") or "")
        )
        return {
            "run_id": claim.attempt_id,
            "run_dir": str(attempt_dir),
            "state_path": str(state_path),
            "config_path": str(config_path),
            "video_path": str(task.get("video_file_path") or ""),
            "remote_video_path": "",
            "caption": str(task.get("caption_content") or ""),
            "caption_preset": "",
            "caption_file": "workflow_steps/caption_presets.json",
            "account_type": account_type,
            "product_id": str(task.get("product_id") or ""),
            "product_link": str(task.get("product_link") or ""),
            "product_name": str(task.get("product_name") or task.get("product_search_title") or ""),
            "product_search_title": product_search_title,
            "product_publish_name": str(task.get("product_publish_name") or ""),
            "dry_run": pipeline_dry_run,
            "allow_publish": allow_publish,
            "stop_before_final_publish": stop_before_final_publish,
            "stopped_before_final_publish": False,
            "publish_mode": normalize_publish_mode(str(task.get("publish_mode") or "scheduled")),
            "schedule_time": "",
            "schedule_date": "",
            "schedule_timezone": schedule_timezone,
            "scheduled_at": str(task.get("scheduled_at") or ""),
            "completed_steps": [],
            "group_control": {
                "task_id": claim.task_id,
                "attempt_id": claim.attempt_id,
                "log_id": claim.log_id,
                "phone_id": claim.phone_id,
                "video_id": task.get("video_id"),
                "caption_id": task.get("caption_id"),
            },
        }

    def build_command(
        self,
        task: dict[str, Any],
        *,
        config_path: Path,
        state_path: Path,
        allow_publish: bool,
        pipeline_dry_run: bool,
        schedule_timezone: str,
        stop_before_final_publish: bool = False,
    ) -> list[str]:
        validate_pipeline_account_workflow(task)
        publish_mode = normalize_publish_mode(str(task.get("publish_mode") or "scheduled"))
        account_type = normalize_account_type(str(task.get("phone_account_type") or "marketing"))
        if account_type == "marketing":
            profile = select_workflow_profile(task, account_type)
            validate_profile_publish_gate(
                profile,
                account_type=account_type,
                allow_publish=allow_publish,
                pipeline_dry_run=pipeline_dry_run,
                stop_before_final_publish=stop_before_final_publish,
            )
            profile_path = WORKFLOW_ROOT / str(profile["profile_path"])
            command = [
                sys.executable,
                "workflow_database/scripts/run_marketing_workflow.py",
                str(task.get("video_file_path") or ""),
                "--profile",
                str(profile_path),
                "--config",
                str(config_path),
                "--state",
                str(state_path),
                "--caption",
                str(task.get("caption_content") or ""),
                "--publish-mode",
                "scheduled",
                "--schedule-timezone",
                schedule_timezone,
                "--scheduled-at",
                str(task.get("scheduled_at") or ""),
                "--force-stop-before-open",
            ]
            if allow_publish:
                command.append("--allow-publish")
            if pipeline_dry_run:
                command.append("--dry-run")
            else:
                command.append("--no-dry-run")
            return command

        if account_type == "showcase":
            profile = select_workflow_profile(task, account_type)
            validate_profile_publish_gate(
                profile,
                account_type=account_type,
                allow_publish=allow_publish,
                pipeline_dry_run=pipeline_dry_run,
                stop_before_final_publish=stop_before_final_publish,
            )
            profile_path = WORKFLOW_ROOT / str(profile["profile_path"])
            product_search_title = showcase_product_search_title(task)
            product_publish_name = str(task.get("product_publish_name") or task.get("product_name") or product_search_title)
            command = [
                sys.executable,
                "workflow_database/scripts/run_showcase_workflow.py",
                "--profile",
                str(profile_path),
                "--config",
                str(config_path),
                "--adb",
                str(BUNDLED_ADB),
                "--serial",
                str(task.get("phone_adb_serial") or ""),
                "--video",
                str(task.get("video_file_path") or ""),
                "--caption",
                str(task.get("caption_content") or ""),
                "--product-search-title",
                product_search_title,
                "--product-publish-name",
                product_publish_name,
                "--publish-mode",
                publish_mode,
                "--schedule-timezone",
                schedule_timezone,
                "--section",
                "publish",
                "--state",
                str(state_path),
                "--run-dir",
                str(state_path.parent),
                "--run-id",
                str(task.get("task_id") or ""),
            ]
            if publish_mode in {"scheduled", "timed"}:
                command.extend(["--scheduled-at", str(task.get("scheduled_at") or "")])
            if allow_publish:
                command.append("--allow-publish")
            if stop_before_final_publish:
                command.append("--stop-before-final-publish")
            if pipeline_dry_run:
                command.append("--dry-run")
            return command

        command = [
            sys.executable,
            "workflow_steps/run_pipeline.py",
            str(task.get("video_file_path") or ""),
            "--config",
            str(config_path),
            "--state",
            str(state_path),
            "--caption",
            str(task.get("caption_content") or ""),
            "--account-type",
            normalize_account_type(str(task.get("phone_account_type") or "marketing")),
            "--publish-mode",
            publish_mode,
            "--schedule-timezone",
            schedule_timezone,
        ]
        if publish_mode in {"scheduled", "timed"}:
            command.extend(["--scheduled-at", str(task.get("scheduled_at") or "")])
        product_link = str(task.get("product_link") or "")
        product_name = str(task.get("product_search_title") or task.get("product_name") or "")
        if product_link:
            command.extend(["--product-link", product_link])
        if product_name:
            command.extend(["--product-name", product_name])
        if allow_publish:
            command.append("--allow-publish")
        if pipeline_dry_run:
            command.append("--dry-run")
        else:
            command.append("--no-dry-run")
        return command

    def build_cwd(self, task: dict[str, Any]) -> Path:
        account_type = normalize_account_type(str(task.get("phone_account_type") or "marketing"))
        if account_type in {"marketing", "showcase"}:
            return PROJECT_ROOT
        return self.upload_root


def load_base_config() -> dict[str, Any]:
    if not UPLOAD_CONFIG.exists():
        return {}
    with UPLOAD_CONFIG.open("r", encoding="utf-8") as file:
        return deepcopy(json.load(file))


def load_workflow_index() -> dict[str, Any]:
    if not WORKFLOW_INDEX.exists():
        raise FileNotFoundError(f"Workflow index not found: {WORKFLOW_INDEX}")
    with WORKFLOW_INDEX.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"Workflow index must contain an object: {WORKFLOW_INDEX}")
    return data


def select_workflow_profile(task: dict[str, Any], account_type: str) -> dict[str, Any]:
    profiles = [
        profile
        for profile in load_workflow_index().get("profiles", [])
        if isinstance(profile, dict)
        and str(profile.get("account_type") or "") == account_type
        and str(profile.get("status") or "") != "disabled"
    ]
    phone_id = str(task.get("phone_id") or "").strip()
    adb_serial = str(task.get("phone_adb_serial") or "").strip()
    for profile in profiles:
        if phone_id and str(profile.get("phone_id") or "") == phone_id:
            return profile
    for profile in profiles:
        if adb_serial and str(profile.get("adb_serial") or "") == adb_serial:
            return profile
    phone_name = normalize_device_text(str(task.get("phone_name") or ""))
    if phone_name:
        model_matches = [
            profile
            for profile in profiles
            if normalize_device_text(str(profile.get("device_model") or ""))
            and normalize_device_text(str(profile.get("device_model") or "")) in phone_name
        ]
        if len(model_matches) == 1:
            return model_matches[0]
    if len(profiles) == 1:
        return profiles[0]
    raise ValueError(f"No workflow profile found for account_type={account_type}, phone_id={phone_id or 'unknown'}")


def validate_profile_publish_gate(
    profile: dict[str, Any],
    *,
    account_type: str,
    allow_publish: bool,
    pipeline_dry_run: bool,
    stop_before_final_publish: bool = False,
) -> None:
    if not allow_publish or pipeline_dry_run or stop_before_final_publish:
        return
    profile_path = WORKFLOW_ROOT / str(profile.get("profile_path") or "")
    profile_data = load_workflow_profile(profile_path)
    status = str(profile_data.get("status") or profile.get("status") or "")
    if profile_requires_calibration(status):
        raise ValueError(
            "Workflow profile is not calibrated for real publish: "
            f"account_type={account_type}, profile={profile.get('profile_id') or profile_path}, status={status or 'unknown'}"
        )


def load_workflow_profile(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Workflow profile not found: {path}")
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"Workflow profile must contain an object: {path}")
    return data


def profile_requires_calibration(status: str) -> bool:
    text = str(status or "").strip().casefold()
    return not text or text.startswith("draft") or "requires_calibration" in text


def normalize_device_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def normalize_publish_mode(value: str) -> str:
    if value == "timed":
        return "timed"
    if value == "immediate":
        return "immediate"
    return "scheduled"


def normalize_account_type(value: str) -> str:
    text = str(value or "marketing").strip().casefold()
    if text in {"showcase", "shop", "showcase_account", "window", "橱窗号", "chuchuang"}:
        return "showcase"
    if text in {"", "marketing", "market", "marketing_account", "营销号", "yingxiao"}:
        return "marketing"
    return text


def truncate_text(value: str, max_chars: int) -> str:
    if max_chars <= 0:
        return value
    return value[:max_chars]


def normalize_product_match_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    chars: list[str] = []
    for char in text:
        category = unicodedata.category(char)
        if category[0] in {"C", "P", "S", "Z"}:
            continue
        chars.append(char)
    return "".join(chars)


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


def showcase_product_search_title(task: dict[str, Any]) -> str:
    value = str(task.get("product_search_title") or task.get("product_name") or "")
    return truncate_text_to_normalized_prefix(value, SHOWCASE_PRODUCT_SEARCH_TITLE_MAX_CHARS)


def validate_pipeline_account_workflow(task: dict[str, Any]) -> None:
    require_account_workflow(
        account_type=normalize_account_type(str(task.get("phone_account_type") or "marketing")),
        publish_mode=normalize_publish_mode(str(task.get("publish_mode") or "scheduled")),
        product_fields=product_fields_from_mapping(task),
    )


def pipeline_env() -> dict[str, str]:
    ANDROID_HOME.mkdir(parents=True, exist_ok=True)
    android_dir = ANDROID_HOME / ".android"
    android_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["ANDROID_USER_HOME"] = str(ANDROID_HOME)
    env["USERPROFILE"] = str(ANDROID_HOME)
    env["HOME"] = str(ANDROID_HOME)
    env["ADB_VENDOR_KEYS"] = str(android_dir / "adbkey")
    env["ADB_SERVER_PORT"] = os.environ.get("GROUP_CONTROL_ADB_SERVER_PORT", DEFAULT_ADB_SERVER_PORT)
    env["GROUP_CONTROL_ADB_DISABLE_KILL_SERVER"] = "1"
    return env


def command_to_text(command: list[str]) -> str:
    return " ".join(quote_command_part(part) for part in command)


def quote_command_part(value: str) -> str:
    text = str(value)
    if not text or any(char.isspace() for char in text) or '"' in text:
        return '"' + text.replace('"', '\\"') + '"'
    return text
