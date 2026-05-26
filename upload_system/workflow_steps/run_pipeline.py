from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from common import load_or_create_state, load_step_config, save_state


def no_window_subprocess_kwargs() -> dict[str, int]:
    if os.name != "nt":
        return {}
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}


CONTENT_STEPS = [
    "push_video.py",
    "open_target_app.py",
    "tap_upload.py",
    "refresh_media_picker.py",
    "select_first_video.py",
    "tap_next_after_select.py",
    "tap_next_after_edit.py",
    "paste_caption.py",
]

IMMEDIATE_TAIL_STEPS = [
    "tap_publish.py",
    "minimize_and_cleanup.py",
]

SCHEDULED_TAIL_STEPS = [
    "open_schedule_settings.py",
    "set_schedule_datetime.py",
    "tap_scheduled_publish.py",
    "minimize_and_cleanup.py",
]

TIMED_TAIL_STEPS = [
    "wait_until_publish_time.py",
    "tap_publish.py",
    "minimize_and_cleanup.py",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the TikTok Studio upload workflow step by step.")
    parser.add_argument("video", nargs="?", default="", help="Local video path.")
    parser.add_argument("--config", default="config.json", help="Path to config JSON.")
    parser.add_argument("--state", default="", help="Existing state JSON to resume.")
    parser.add_argument("--job-file", default="", help="JSON file that contains video/caption/schedule jobs.")
    parser.add_argument("--job-id", default="", help="Job id inside --job-file.")
    parser.add_argument("--list-jobs", action="store_true", help="List jobs in --job-file and exit.")
    parser.add_argument("--caption-file", default="", help="Caption preset JSON path.")
    parser.add_argument("--caption-preset", default="", help="Named caption preset key.")
    parser.add_argument("--caption", default="", help="Direct caption text override.")
    parser.add_argument("--account-type", choices=["marketing", "showcase"], default="", help="marketing uses the standard timed/scheduled flow; showcase also attaches a product before publishing.")
    parser.add_argument("--product-link", default="", help="Product link for showcase account publishing.")
    parser.add_argument("--product-name", default="", help="Product name or search keyword for showcase account publishing.")
    parser.add_argument("--dry-run", action="store_true", help="Run without taps/text entry.")
    parser.add_argument("--no-dry-run", action="store_true", help="Execute even if config dry_run is true.")
    parser.add_argument("--allow-publish", action="store_true", help="Allow the final publish tap.")
    parser.add_argument(
        "--publish-mode",
        choices=["immediate", "scheduled", "timed"],
        default="",
        help="immediate posts now, scheduled uses TikTok Studio native scheduling, timed waits on the computer before posting.",
    )
    parser.add_argument("--schedule-time", default="", help="Target publish time, for example 08:00.")
    parser.add_argument("--schedule-date", default="", help="Target publish date in YYYY-MM-DD. Defaults to next valid day.")
    parser.add_argument("--schedule-timezone", default="", help="IANA timezone, for example America/Sao_Paulo.")
    parser.add_argument("--scheduled-at", default="", help="Full scheduled datetime in ISO format.")
    parser.add_argument("--from-step", type=int, default=1, help="First step number to run.")
    parser.add_argument("--to-step", type=int, default=0, help="Last step number to run.")
    args = parser.parse_args()
    apply_job_config(args, parser)

    config = load_step_config(args.config)
    state = load_or_create_state(args, config)

    if not args.publish_mode:
        args.publish_mode = (
            "scheduled"
            if args.schedule_time or args.scheduled_at
            else str(state.get("publish_mode", "immediate") or "immediate")
        )
    elif args.publish_mode == "immediate" and (args.schedule_time or args.scheduled_at):
        args.publish_mode = "scheduled"
    if not args.account_type:
        args.account_type = str(state.get("account_type", "marketing") or "marketing")
    if args.account_type == "showcase":
        product_link = args.product_link or str(state.get("product_link") or "")
        product_name = args.product_name or str(state.get("product_name") or "")
        if not product_link.strip() and not product_name.strip():
            parser.error("Showcase workflow requires --product-link or --product-name.")
    steps = build_steps(args.publish_mode, args.account_type)
    if args.to_step == 0:
        args.to_step = len(steps)

    if args.from_step < 1 or args.to_step > len(steps) or args.from_step > args.to_step:
        parser.error(f"Step range must be between 1 and {len(steps)}.")

    if args.caption_file:
        state["caption_file"] = args.caption_file
    if args.caption_preset:
        state["caption_preset"] = args.caption_preset
    if args.caption:
        state["caption"] = args.caption
    state["account_type"] = args.account_type
    if args.product_link:
        state["product_link"] = args.product_link
    if args.product_name:
        state["product_name"] = args.product_name
    if args.job_file:
        state["job_file"] = args.job_file
    if args.job_id:
        state["job_id"] = args.job_id
    state["publish_mode"] = args.publish_mode
    if args.schedule_time:
        state["schedule_time"] = args.schedule_time
    if args.schedule_date:
        state["schedule_date"] = args.schedule_date
    if args.schedule_timezone:
        state["schedule_timezone"] = args.schedule_timezone
    if args.scheduled_at:
        state["scheduled_at"] = args.scheduled_at
    save_state(state)

    step_dir = Path(__file__).resolve().parent
    selected_steps = steps[args.from_step - 1 : args.to_step]
    print(f"Pipeline state: {state['state_path']}")
    print(f"Publish mode: {args.publish_mode}")
    print(f"Account workflow: {args.account_type}")
    if args.job_id:
        print(f"Job: {args.job_id}")
    for step_script in selected_steps:
        command = [
            sys.executable,
            str(step_dir / step_script),
            "--config",
            args.config,
            "--state",
            state["state_path"],
        ]
        if args.allow_publish:
            command.append("--allow-publish")
        if args.dry_run:
            command.append("--dry-run")
        if args.no_dry_run:
            command.append("--no-dry-run")
        command.extend(["--publish-mode", args.publish_mode])
        command.extend(["--account-type", args.account_type])
        if args.product_link:
            command.extend(["--product-link", args.product_link])
        if args.product_name:
            command.extend(["--product-name", args.product_name])
        if args.schedule_time:
            command.extend(["--schedule-time", args.schedule_time])
        if args.schedule_date:
            command.extend(["--schedule-date", args.schedule_date])
        if args.schedule_timezone:
            command.extend(["--schedule-timezone", args.schedule_timezone])
        if args.scheduled_at:
            command.extend(["--scheduled-at", args.scheduled_at])
        if step_script == "paste_caption.py":
            if args.caption_file:
                command.extend(["--caption-file", args.caption_file])
            if args.caption_preset:
                command.extend(["--caption-preset", args.caption_preset])
            if args.caption:
                command.extend(["--caption", args.caption])

        print(f"\n=== {step_script} ===")
        result = subprocess.run(command, check=False, **no_window_subprocess_kwargs())
        if result.returncode != 0:
            print(f"Pipeline stopped at {step_script} with exit code {result.returncode}.")
            return result.returncode

    print("\nPipeline completed.")
    print(f"Artifacts: {state['run_dir']}")
    return 0


def apply_job_config(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if not args.job_file:
        if args.list_jobs:
            parser.error("--list-jobs requires --job-file.")
        return

    job_file = Path(args.job_file)
    if not job_file.exists():
        parser.error(f"Job file not found: {job_file}")

    with job_file.open("r", encoding="utf-8") as file:
        data = json.load(file)

    try:
        jobs = normalize_jobs(data.get("jobs", []))
    except ValueError as exc:
        parser.error(str(exc))
    if args.list_jobs:
        for job in jobs:
            schedule = job.get("schedule", {})
            date_value = job.get("schedule_date") or schedule.get("date") or ""
            time_value = job.get("schedule_time") or schedule.get("time") or ""
            account_type = job.get("account_type") or data.get("defaults", {}).get("account_type") or "marketing"
            print(f"{job['id']}: {account_type} {job.get('video', '')} {date_value} {time_value}".rstrip())
        raise SystemExit(0)

    job = select_job(jobs, args.job_id, parser)
    args.job_id = str(job["id"])

    defaults = data.get("defaults", {})
    merged = merge_job(defaults, job)
    apply_value(args, "video", merged.get("video", ""))
    apply_value(args, "caption_file", merged.get("caption_file", ""))
    apply_value(args, "caption_preset", merged.get("caption_preset", ""))
    apply_value(args, "caption", merged.get("caption", ""))
    apply_value(args, "account_type", merged.get("account_type", ""))
    apply_value(args, "product_link", merged.get("product_link", ""))
    apply_value(args, "product_name", merged.get("product_name", ""))
    apply_value(args, "publish_mode", merged.get("publish_mode", ""))

    schedule = merged.get("schedule", {})
    if not isinstance(schedule, dict):
        schedule = {}
    apply_value(args, "schedule_date", merged.get("schedule_date", "") or schedule.get("date", ""))
    apply_value(args, "schedule_time", merged.get("schedule_time", "") or schedule.get("time", ""))
    apply_value(args, "schedule_timezone", merged.get("schedule_timezone", "") or schedule.get("timezone", ""))
    apply_value(args, "scheduled_at", merged.get("scheduled_at", "") or schedule.get("at", ""))


def normalize_jobs(raw_jobs: Any) -> list[dict[str, Any]]:
    if isinstance(raw_jobs, dict):
        return [
            {**(job if isinstance(job, dict) else {"video": str(job)}), "id": str(job_id)}
            for job_id, job in raw_jobs.items()
        ]
    if isinstance(raw_jobs, list):
        jobs: list[dict[str, Any]] = []
        for index, job in enumerate(raw_jobs, start=1):
            if not isinstance(job, dict):
                raise ValueError(f"Job #{index} must be an object.")
            jobs.append({"id": str(job.get("id") or index), **job})
        return jobs
    raise ValueError("jobs must be an object or an array.")


def select_job(jobs: list[dict[str, Any]], job_id: str, parser: argparse.ArgumentParser) -> dict[str, Any]:
    if not jobs:
        parser.error("Job file does not contain any jobs.")
    if job_id:
        for job in jobs:
            if str(job.get("id", "")) == job_id:
                return job
        parser.error(f"Job id not found: {job_id}")
    if len(jobs) == 1:
        return jobs[0]
    parser.error("--job-id is required when --job-file contains multiple jobs.")


def merge_job(defaults: Any, job: dict[str, Any]) -> dict[str, Any]:
    merged = dict(defaults) if isinstance(defaults, dict) else {}
    merged.update(job)
    if isinstance(defaults, dict) and isinstance(defaults.get("schedule"), dict):
        schedule = dict(defaults["schedule"])
        if isinstance(job.get("schedule"), dict):
            schedule.update(job["schedule"])
        merged["schedule"] = schedule
    return merged


def apply_value(args: argparse.Namespace, name: str, value: Any) -> None:
    if value not in (None, "") and not getattr(args, name):
        setattr(args, name, str(value))


def build_steps(publish_mode: str, account_type: str) -> list[str]:
    steps = list(CONTENT_STEPS)
    if account_type == "showcase":
        steps.append("attach_product_link.py")
    if publish_mode == "scheduled":
        steps.extend(SCHEDULED_TAIL_STEPS)
    elif publish_mode == "timed":
        steps.extend(TIMED_TAIL_STEPS)
    else:
        steps.extend(IMMEDIATE_TAIL_STEPS)
    return steps


if __name__ == "__main__":
    raise SystemExit(main())

