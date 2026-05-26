from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


VERIFIED_STATUS = "verified_from_xiaomi_marketing_parallel_debug"
PENDING_STATUS = "candidate_pending_verify"
DRY_RUN_STATUS = "candidate_dry_run"
REJECTED_STATUS = "rejected_step_failed"

DIRECT_STEP_POINTS = {
    "tap_next_after_select": "next_after_select",
    "tap_next_after_edit": "next_after_edit",
    "paste_caption": "caption_field",
    "open_schedule_settings": "schedule_entry",
}
SCHEDULE_POINTS = {
    "native_schedule_date_column",
    "native_schedule_hour_column",
    "native_schedule_minute_column",
    "schedule_confirm",
}


@dataclass(frozen=True)
class CalibrationCandidate:
    point_name: str
    x: int
    y: int
    source: str
    label: str = ""
    confidence: float = 0.0
    requires_verify_step: str = ""


class CalibrationRecorder:
    def __init__(
        self,
        *,
        profile_path: Path,
        save_profile_coordinates: bool,
        event_path: Path | None = None,
        serial: str = "",
        model: str = "",
    ) -> None:
        self.profile_path = profile_path
        self.save_profile_coordinates = save_profile_coordinates
        self.event_path = event_path
        self.serial = serial
        self.model = model

    def record_step_result(
        self,
        *,
        profile: dict[str, Any],
        state_path: Path,
        step: dict[str, Any],
        success: bool,
    ) -> dict[str, Any]:
        state = read_json_object(state_path) if state_path.exists() else {}
        run_dir = Path(str(state.get("run_dir") or state_path.parent)).resolve()
        event_path = self.event_path or run_dir / "calibration_events.jsonl"
        event_path.parent.mkdir(parents=True, exist_ok=True)
        step_id = str(step.get("id") or "")

        if step_id == "verify_schedule_configured" and success and not state.get("dry_run", False):
            profile = self._promote_pending_schedule_candidates(
                profile=profile,
                state=state,
                run_dir=run_dir,
                event_path=event_path,
                verify_step_id=step_id,
            )

        trace_path = run_dir / "traces" / f"{step_id}.json"
        trace = read_json_object(trace_path) if trace_path.exists() else {}
        candidates = extract_candidates(profile=profile, step=step, trace=trace)
        if not candidates:
            self._append_event(
                event_path,
                base_event(
                    state=state,
                    run_dir=run_dir,
                    step_id=step_id,
                    profile_path=self.profile_path,
                    serial=self.serial,
                    model=self.model,
                    success=success,
                    status="no_coordinate_candidate" if success else REJECTED_STATUS,
                    trace_path=trace_path if trace else None,
                ),
            )
            return profile

        screen_size = resolve_screen_size(profile, run_dir, step_id, trace)
        completed_steps = set(str(value) for value in state.get("completed_steps", []))
        updates: list[dict[str, Any]] = []
        events: list[dict[str, Any]] = []
        for candidate in candidates:
            status = VERIFIED_STATUS
            if not success:
                status = REJECTED_STATUS
            elif state.get("dry_run", False):
                status = DRY_RUN_STATUS
            elif candidate.requires_verify_step and candidate.requires_verify_step not in completed_steps:
                status = PENDING_STATUS

            event = base_event(
                state=state,
                run_dir=run_dir,
                step_id=step_id,
                profile_path=self.profile_path,
                serial=self.serial,
                model=self.model,
                success=success,
                status=status,
                trace_path=trace_path if trace else None,
            )
            event.update(
                {
                    "point_name": candidate.point_name,
                    "x": candidate.x,
                    "y": candidate.y,
                    "x_ratio": ratio(candidate.x, screen_size[0]),
                    "y_ratio": ratio(candidate.y, screen_size[1]),
                    "source": candidate.source,
                    "label": candidate.label,
                    "confidence": candidate.confidence,
                    "requires_verify_step": candidate.requires_verify_step,
                    "screen_width": screen_size[0],
                    "screen_height": screen_size[1],
                    "evidence": evidence_paths(run_dir, step_id, trace, trace_path),
                    "saved_to_profile": False,
                }
            )
            if status == VERIFIED_STATUS and self.save_profile_coordinates:
                updates.append(event)
            events.append(event)

        if updates:
            profile = apply_profile_updates(self.profile_path, updates)
            for event in events:
                if event["status"] == VERIFIED_STATUS:
                    event["saved_to_profile"] = True

        for event in events:
            self._append_event(event_path, event)
        return profile

    def _promote_pending_schedule_candidates(
        self,
        *,
        profile: dict[str, Any],
        state: dict[str, Any],
        run_dir: Path,
        event_path: Path,
        verify_step_id: str,
    ) -> dict[str, Any]:
        if not event_path.exists():
            return profile

        pending = []
        for event in read_json_lines(event_path):
            if event.get("status") != PENDING_STATUS:
                continue
            if event.get("run_dir") != str(run_dir):
                continue
            if str(event.get("point_name") or "") not in SCHEDULE_POINTS:
                continue
            promoted = dict(event)
            promoted["status"] = VERIFIED_STATUS
            promoted["verified_by_step"] = verify_step_id
            promoted["verified_at"] = now_utc()
            promoted["saved_to_profile"] = False
            pending.append(promoted)

        if not pending or not self.save_profile_coordinates:
            return profile

        profile = apply_profile_updates(self.profile_path, pending)
        for event in pending:
            event["saved_to_profile"] = True
            event["promotion_event"] = True
            event["verification_step"] = verify_step_id
            self._append_event(event_path, event)
        return profile

    def _append_event(self, event_path: Path, event: dict[str, Any]) -> None:
        event_path.parent.mkdir(parents=True, exist_ok=True)
        with event_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        point = event.get("point_name") or "-"
        print(
            "CALIBRATION_EVENT "
            f"step={event.get('step_id')} point={point} status={event.get('status')} "
            f"saved={event.get('saved_to_profile', False)}"
        )


def extract_candidates(
    *,
    profile: dict[str, Any],
    step: dict[str, Any],
    trace: dict[str, Any],
) -> list[CalibrationCandidate]:
    if not trace:
        return []
    step_id = str(step.get("id") or "")
    if step_id == "tap_upload":
        return upload_candidates(trace)
    if step_id == "select_first_video":
        return select_first_video_candidates(trace)
    if step_id == "set_schedule_datetime":
        return schedule_candidates(profile, trace)
    if str(step.get("type") or "") in {"tap_point", "tap_profile_point"}:
        point_name = str(step.get("point") or "")
        return direct_candidate(trace, point_name)
    if step_id in DIRECT_STEP_POINTS:
        return direct_candidate(trace, DIRECT_STEP_POINTS[step_id])
    return []


def direct_candidate(trace: dict[str, Any], point_name: str) -> list[CalibrationCandidate]:
    if not point_name or not has_xy(trace):
        return []
    return [
        CalibrationCandidate(
            point_name=point_name,
            x=int(trace["x"]),
            y=int(trace["y"]),
            source=str(trace.get("source") or ""),
            label=str(trace.get("label") or ""),
            confidence=float(trace.get("confidence") or 0.0),
        )
    ]


def upload_candidates(trace: dict[str, Any]) -> list[CalibrationCandidate]:
    attempts = trace.get("attempts")
    if not isinstance(attempts, list) or not attempts:
        return []
    attempt = next((item for item in reversed(attempts) if isinstance(item, dict) and has_xy(item)), None)
    if not attempt:
        return []
    label = str(attempt.get("label") or "")
    point_name = label if label in {"upload_button_center", "upload_button_icon", "upload_button_text"} else "upload_button_center"
    return [
        CalibrationCandidate(
            point_name=point_name,
            x=int(attempt["x"]),
            y=int(attempt["y"]),
            source=str(attempt.get("source") or "tap_upload_attempt"),
            label=label,
            confidence=0.8 if trace.get("result") == "media_picker_visible" else 0.35,
        )
    ]


def select_first_video_candidates(trace: dict[str, Any]) -> list[CalibrationCandidate]:
    candidates: list[CalibrationCandidate] = []
    if has_xy(trace):
        candidates.append(
            CalibrationCandidate(
                point_name="media_picker_selection_control",
                x=int(trace["x"]),
                y=int(trace["y"]),
                source=str(trace.get("source") or "select_first_video"),
                label=str(trace.get("label") or "first_media_selection"),
                confidence=float(trace.get("confidence") or 0.8),
            )
        )
    media_bounds = parse_bounds(str(trace.get("media_bounds") or ""))
    if media_bounds:
        left, top, right, bottom = media_bounds
        candidates.append(
            CalibrationCandidate(
                point_name="media_picker_first_video",
                x=(left + right) // 2,
                y=(top + bottom) // 2,
                source="media_bounds_center",
                label="first_media_item",
                confidence=0.8,
            )
        )
    return candidates


def schedule_candidates(profile: dict[str, Any], trace: dict[str, Any]) -> list[CalibrationCandidate]:
    attempts = trace.get("attempts")
    if not isinstance(attempts, list) or not attempts:
        return []
    attempt = next((item for item in reversed(attempts) if isinstance(item, dict)), None)
    if not attempt:
        return []

    candidates: list[CalibrationCandidate] = []
    confirm = attempt.get("confirm")
    if isinstance(confirm, dict) and has_xy(confirm):
        candidates.append(
            CalibrationCandidate(
                point_name="schedule_confirm",
                x=int(confirm["x"]),
                y=int(confirm["y"]),
                source="native_schedule_confirm",
                label="schedule_confirm",
                confidence=0.75,
                requires_verify_step="verify_schedule_configured",
            )
        )

    screen = profile.get("screen", {}) if isinstance(profile.get("screen"), dict) else {}
    screen_height = int(screen.get("height") or 0)
    points = profile.get("coordinate_points", {}) if isinstance(profile.get("coordinate_points"), dict) else {}
    point_by_move = {
        "date": "native_schedule_date_column",
        "hour": "native_schedule_hour_column",
        "minute": "native_schedule_minute_column",
    }
    moves = attempt.get("moves")
    if isinstance(moves, list):
        for move in moves:
            if not isinstance(move, dict) or "x" not in move:
                continue
            move_name = str(move.get("name") or "")
            point_name = point_by_move.get(move_name)
            if not point_name:
                continue
            existing = points.get(point_name, {}) if isinstance(points.get(point_name), dict) else {}
            y_ratio = float(existing.get("y_ratio") or 0.75)
            y = int(screen_height * y_ratio) if screen_height else int(existing.get("y") or 0)
            candidates.append(
                CalibrationCandidate(
                    point_name=point_name,
                    x=int(move["x"]),
                    y=y,
                    source="native_schedule_wheel_move",
                    label=move_name,
                    confidence=0.7,
                    requires_verify_step="verify_schedule_configured",
                )
            )
    return dedupe_by_point(candidates)


def dedupe_by_point(candidates: list[CalibrationCandidate]) -> list[CalibrationCandidate]:
    deduped: dict[str, CalibrationCandidate] = {}
    for candidate in candidates:
        deduped[candidate.point_name] = candidate
    return list(deduped.values())


def apply_profile_updates(profile_path: Path, updates: list[dict[str, Any]]) -> dict[str, Any]:
    with profile_lock(profile_path):
        profile = read_json_object(profile_path)
        points = profile.setdefault("coordinate_points", {})
        timestamp = now_utc()
        for update in updates:
            point_name = str(update["point_name"])
            existing = points.get(point_name, {}) if isinstance(points.get(point_name), dict) else {}
            evidence = dict(update.get("evidence") or {})
            evidence.update(
                {
                    "serial": update.get("serial", ""),
                    "model": update.get("model", ""),
                    "step_id": update.get("step_id", ""),
                    "run_dir": update.get("run_dir", ""),
                    "trace_path": update.get("trace_path", ""),
                }
            )
            points[point_name] = {
                **existing,
                "x": int(update["x"]),
                "y": int(update["y"]),
                "x_ratio": float(update["x_ratio"]),
                "y_ratio": float(update["y_ratio"]),
                "description": existing.get("description", point_name),
                "calibration_status": VERIFIED_STATUS,
                "verified_at": timestamp,
                "calibration_source": str(update.get("source") or ""),
                "calibration_evidence": evidence,
            }
        write_profile_atomic(profile_path, profile)
        return profile


class profile_lock:
    def __init__(self, profile_path: Path, timeout_seconds: float = 30.0) -> None:
        self.lock_path = profile_path.with_suffix(profile_path.suffix + ".lock")
        self.timeout_seconds = timeout_seconds
        self.fd: int | None = None

    def __enter__(self) -> "profile_lock":
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            try:
                self.fd = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self.fd, str(os.getpid()).encode("ascii", "ignore"))
                return self
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Timed out waiting for profile lock: {self.lock_path}")
                time.sleep(0.1)

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self.fd is not None:
            os.close(self.fd)
        try:
            self.lock_path.unlink()
        except FileNotFoundError:
            pass


def write_profile_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    if path.exists():
        backup = path.with_suffix(path.suffix + f".bak.{stamp}")
        backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    temp_path = path.with_suffix(path.suffix + f".tmp.{os.getpid()}.{stamp}")
    temp_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temp_path, path)


def base_event(
    *,
    state: dict[str, Any],
    run_dir: Path,
    step_id: str,
    profile_path: Path,
    serial: str,
    model: str,
    success: bool,
    status: str,
    trace_path: Path | None,
) -> dict[str, Any]:
    return {
        "event_type": "marketing_coordinate_calibration",
        "created_at": now_utc(),
        "step_id": step_id,
        "success": bool(success),
        "status": status,
        "dry_run": bool(state.get("dry_run", False)),
        "run_dir": str(run_dir),
        "state_path": str(state.get("state_path") or ""),
        "profile_path": str(profile_path),
        "trace_path": str(trace_path) if trace_path else "",
        "serial": serial,
        "model": model,
    }


def evidence_paths(run_dir: Path, step_id: str, trace: dict[str, Any], trace_path: Path) -> dict[str, str]:
    screenshot = find_screenshot(run_dir, step_id, trace)
    xml = find_xml_for_screenshot(run_dir, screenshot, step_id)
    return {
        "screenshot": str(screenshot) if screenshot else "",
        "xml": str(xml) if xml else "",
        "trace": str(trace_path),
    }


def find_screenshot(run_dir: Path, step_id: str, trace: dict[str, Any]) -> Path | None:
    traced = str(trace.get("screenshot") or "")
    if traced:
        path = Path(traced)
        if path.exists():
            return path
    screenshot_dir = run_dir / "screenshots"
    exact = screenshot_dir / f"{step_id}.png"
    if exact.exists():
        return exact
    matches = sorted(screenshot_dir.glob(f"{step_id}*.png"))
    return matches[-1] if matches else None


def find_xml_for_screenshot(run_dir: Path, screenshot: Path | None, step_id: str) -> Path | None:
    xml_dir = run_dir / "xml"
    if screenshot:
        candidate = xml_dir / f"{screenshot.stem}.xml"
        if candidate.exists():
            return candidate
    exact = xml_dir / f"{step_id}.xml"
    if exact.exists():
        return exact
    matches = sorted(xml_dir.glob(f"{step_id}*.xml"))
    return matches[-1] if matches else None


def resolve_screen_size(
    profile: dict[str, Any],
    run_dir: Path,
    step_id: str,
    trace: dict[str, Any],
) -> tuple[int, int]:
    screenshot = find_screenshot(run_dir, step_id, trace)
    if screenshot:
        parsed = png_size(screenshot)
        if parsed:
            return parsed
    screen = profile.get("screen", {}) if isinstance(profile.get("screen"), dict) else {}
    return int(screen.get("width") or 1), int(screen.get("height") or 1)


def png_size(path: Path) -> tuple[int, int] | None:
    try:
        data = path.read_bytes()[:24]
    except OSError:
        return None
    if len(data) < 24 or not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


def parse_bounds(bounds: str) -> tuple[int, int, int, int] | None:
    match = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds or "")
    if not match:
        return None
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def has_xy(value: dict[str, Any]) -> bool:
    return value.get("x") is not None and value.get("y") is not None


def ratio(value: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(float(value) / float(total), 6)


def read_json_object(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"JSON file must contain an object: {path}")
    return data


def read_json_lines(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
    return rows


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
