from __future__ import annotations

import os
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .idle_wake import IdleWakeResult, wake_idle_phone
from .models import ExecutionClaim, PipelineRunPlan
from .pipeline_adapter import command_to_text
from .queue_service import ExecutionQueueService
from .worker_state import WorkerStateRepository


@dataclass(frozen=True)
class PhoneWorkerOptions:
    phone_id: str
    adb_serial: str = ""
    account_type: str = ""
    worker_id: str = ""
    dry_run: bool = False
    pipeline_dry_run: bool = False
    allow_publish: bool = False
    stop_before_final_publish: bool = False
    timezone: str = "Asia/Shanghai"
    preparation_window_minutes: int = 60
    require_phone_ready: bool = True
    allow_overdue: bool = False
    timeout_seconds: int = 7200
    poll_seconds: int = 5
    max_runs: int = 0
    stop_when_idle: bool = False
    heartbeat_seconds: int = 10
    lease_seconds: int = 120
    post_run_cooldown_seconds: int = 90
    stop_on_failure: bool = True
    idle_wake_enabled: bool = True
    idle_wake_interval_seconds: int = 300


class PhoneWorker:
    def __init__(self, options: PhoneWorkerOptions, db_path: str | Path | None = None) -> None:
        self.options = options
        self.db_path = db_path
        self.service = ExecutionQueueService(db_path)
        self.worker_state = WorkerStateRepository(db_path)
        self.worker_id = options.worker_id or make_worker_id(options.phone_id, options.adb_serial)
        self.stop_event = threading.Event()
        self.state_lock = threading.Lock()
        self.status = "starting"
        self.current_task_id = ""
        self.current_attempt_id = ""
        self.last_error = ""
        self.last_idle_wake_at = 0.0

    def stop(self) -> None:
        self.stop_event.set()

    def run_forever(self) -> dict[str, Any]:
        self.worker_state.register(
            worker_id=self.worker_id,
            phone_id=self.options.phone_id,
            adb_serial=self.options.adb_serial,
            pid=os.getpid(),
            lease_seconds=self.options.lease_seconds,
        )
        heartbeat = threading.Thread(target=self._heartbeat_loop, name=f"heartbeat-{self.worker_id}", daemon=True)
        heartbeat.start()
        outcomes: list[dict[str, Any]] = []
        completed_runs = 0
        stopped_reason = ""
        final_status = "stopped"

        try:
            while not self.stop_event.is_set() and (self.options.max_runs == 0 or completed_runs < self.options.max_runs):
                self._set_state(status="idle", current_task_id="", current_attempt_id="", last_error="")
                outcome = self.service.run_once(
                    phone_id=self.options.phone_id,
                    adb_serial=self.options.adb_serial,
                    account_type=self.options.account_type,
                    worker_id=self.worker_id,
                    lease_seconds=max(self.options.lease_seconds, self.options.heartbeat_seconds * 3),
                    dry_run=self.options.dry_run,
                    pipeline_dry_run=self.options.pipeline_dry_run,
                    allow_publish=self.options.allow_publish,
                    stop_before_final_publish=self.options.stop_before_final_publish,
                    timezone=self.options.timezone,
                    preparation_window_minutes=self.options.preparation_window_minutes,
                    require_phone_ready=self.options.require_phone_ready,
                    allow_overdue=self.options.allow_overdue,
                    timeout_seconds=self.options.timeout_seconds,
                    claim_hook=self._claim_hook,
                )
                outcomes.append(summarize_worker_outcome(outcome))
                status = str(outcome.get("status") or "")
                if status == "no_task":
                    stopped_reason = "idle"
                    if self.options.stop_when_idle:
                        break
                    self._set_state(status="idle", current_task_id="", current_attempt_id="", last_error="")
                    self._idle_wait(max(1, self.options.poll_seconds))
                    continue

                completed_runs += 1
                if status == "failed":
                    self._set_state(status="error", last_error=str(outcome.get("error") or ""))
                    if self.options.stop_on_failure:
                        stopped_reason = "failed"
                        break
                else:
                    self._set_state(status="idle", current_task_id="", current_attempt_id="", last_error="")
                if self.options.dry_run and self.options.max_runs == 0:
                    stopped_reason = "dry_run_completed"
                    break
                if (
                    not self.stop_event.is_set()
                    and (self.options.max_runs == 0 or completed_runs < self.options.max_runs)
                    and self.options.post_run_cooldown_seconds > 0
                ):
                    self._idle_wait(self.options.post_run_cooldown_seconds)
        except Exception as exc:  # noqa: BLE001
            final_status = "error"
            stopped_reason = "error"
            self._set_state(status="error", last_error=str(exc))
            outcomes.append({"status": "worker_error", "error": str(exc)})
        finally:
            if self.stop_event.is_set() and not stopped_reason:
                stopped_reason = "stopped"
            self.stop_event.set()
            heartbeat.join(timeout=max(2, self.options.heartbeat_seconds + 1))
            snapshot = self._state_snapshot()
            self.worker_state.finish(
                worker_id=self.worker_id,
                status=final_status if snapshot["status"] != "error" else "error",
                last_error=str(snapshot["last_error"] or ""),
            )

        return {
            "worker_id": self.worker_id,
            "phone_id": self.options.phone_id,
            "adb_serial": self.options.adb_serial,
            "status": "stopped",
            "reason": stopped_reason or ("max_runs_reached" if self.options.max_runs else "stopped"),
            "runs": completed_runs,
            "outcomes": outcomes,
        }

    def _claim_hook(self, claim: ExecutionClaim, plan: PipelineRunPlan) -> None:
        self._set_state(
            status="running",
            current_task_id=claim.task_id,
            current_attempt_id=claim.attempt_id,
            last_error="",
        )
        self.worker_state.heartbeat(
            worker_id=self.worker_id,
            status="running",
            current_task_id=claim.task_id,
            current_attempt_id=claim.attempt_id,
            lease_seconds=max(self.options.lease_seconds, self.options.heartbeat_seconds * 3),
        )

    def _heartbeat_loop(self) -> None:
        while not self.stop_event.is_set():
            snapshot = self._state_snapshot()
            self.worker_state.heartbeat(
                worker_id=self.worker_id,
                status=str(snapshot["status"]),
                current_task_id=str(snapshot["current_task_id"]),
                current_attempt_id=str(snapshot["current_attempt_id"]),
                last_error=str(snapshot["last_error"]),
                lease_seconds=max(self.options.lease_seconds, self.options.heartbeat_seconds * 3),
            )
            self.stop_event.wait(max(1, self.options.heartbeat_seconds))

    def _idle_wait(self, seconds: int) -> None:
        deadline = time.monotonic() + max(0, seconds)
        while not self.stop_event.is_set():
            self._maybe_wake_idle_phone()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            self.stop_event.wait(min(max(1, self.options.heartbeat_seconds), remaining))

    def _maybe_wake_idle_phone(self) -> None:
        if not self.options.idle_wake_enabled or not self.options.adb_serial:
            return
        snapshot = self._state_snapshot()
        if str(snapshot["status"]) == "running" or snapshot["current_task_id"] or snapshot["current_attempt_id"]:
            return
        now = time.monotonic()
        interval = max(1, self.options.idle_wake_interval_seconds)
        if self.last_idle_wake_at and now - self.last_idle_wake_at < interval:
            return
        self.last_idle_wake_at = now
        result = wake_idle_phone(self.options.adb_serial)
        self._record_idle_wake_result(result)

    def _record_idle_wake_result(self, result: IdleWakeResult) -> None:
        snapshot = self._state_snapshot()
        if result.success:
            if str(snapshot["last_error"]).startswith("idle wake failed:"):
                self._set_state(status=str(snapshot["status"]), last_error="")
            return
        reason = result.failure_reason()
        if reason:
            self._set_state(status=str(snapshot["status"]), last_error=f"idle wake failed: {reason}")

    def _set_state(
        self,
        *,
        status: str,
        current_task_id: str | None = None,
        current_attempt_id: str | None = None,
        last_error: str | None = None,
    ) -> None:
        with self.state_lock:
            self.status = status
            if current_task_id is not None:
                self.current_task_id = current_task_id
            if current_attempt_id is not None:
                self.current_attempt_id = current_attempt_id
            if last_error is not None:
                self.last_error = last_error

    def _state_snapshot(self) -> dict[str, str]:
        with self.state_lock:
            return {
                "status": self.status,
                "current_task_id": self.current_task_id,
                "current_attempt_id": self.current_attempt_id,
                "last_error": self.last_error,
            }


def make_worker_id(phone_id: str, adb_serial: str) -> str:
    base = phone_id or adb_serial or "phone"
    safe = "".join(char if char.isalnum() else "-" for char in base)[:32].strip("-") or "phone"
    return f"WRK-{safe}-{uuid.uuid4().hex[:8].upper()}"


def summarize_worker_outcome(outcome: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {"status": outcome.get("status")}
    for key in ("phase", "next_task_status", "error"):
        if outcome.get(key):
            summary[key] = outcome[key]
    claim = outcome.get("claim")
    if isinstance(claim, ExecutionClaim):
        summary.update(
            {
                "task_id": claim.task_id,
                "attempt_id": claim.attempt_id,
                "log_id": claim.log_id,
                "phone_id": claim.phone_id,
            }
        )
    plan = outcome.get("plan")
    if isinstance(plan, PipelineRunPlan):
        summary.update(
            {
                "run_dir": str(plan.attempt_dir),
                "state_path": str(plan.state_path),
                "command": command_to_text(plan.command),
            }
        )
    return summary
