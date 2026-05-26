from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mobile_phone_library.phone_repository import PhoneRepository

from .phone_worker import PhoneWorker, PhoneWorkerOptions
from .worker_readiness import DEFAULT_STAGGER_SECONDS, EXPECTED_STEADY_STATE_WORKERS, build_worker_readiness_report
from .worker_state import WorkerStateRepository


@dataclass(frozen=True)
class WorkerManagerOptions:
    phone_ids: tuple[str, ...] = ()
    adb_serials: tuple[str, ...] = ()
    account_type: str = ""
    max_workers: int = EXPECTED_STEADY_STATE_WORKERS
    stagger_seconds: int = DEFAULT_STAGGER_SECONDS
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
    max_runs_per_worker: int = 0
    stop_when_idle: bool = False
    heartbeat_seconds: int = 10
    lease_seconds: int = 120
    post_run_cooldown_seconds: int = 90
    stop_on_failure: bool = True
    idle_wake_enabled: bool = True
    idle_wake_interval_seconds: int = 300
    steady_state_gate_enabled: bool = True
    expected_worker_count: int = EXPECTED_STEADY_STATE_WORKERS
    require_task_per_phone: bool = False
    recover_stale_before_start: bool = True


class WorkerManager:
    def __init__(self, options: WorkerManagerOptions, db_path: str | Path | None = None) -> None:
        self.options = options
        self.db_path = db_path
        self.phone_repository = PhoneRepository(db_path)
        self.worker_state = WorkerStateRepository(db_path)
        self.stop_event = threading.Event()
        self.workers: list[PhoneWorker] = []
        self._readiness_prechecked = False

    def stop(self) -> None:
        self.stop_event.set()
        for worker in self.workers:
            worker.stop()

    def select_phones(self) -> list[dict[str, Any]]:
        phones = self.phone_repository.list_phones(
            status="" if not self.options.require_phone_ready else "online_idle",
            limit=1000,
            include_disabled=False,
            include_removed=False,
        )
        selected: list[dict[str, Any]] = []
        phone_ids = set(self.options.phone_ids)
        adb_serials = set(self.options.adb_serials)
        for phone in phones:
            phone_id = str(phone.get("phone_id") or "")
            adb_serial = str(phone.get("adb_serial") or "")
            account_type = str(phone.get("account_type") or "")
            if phone_ids and phone_id not in phone_ids:
                continue
            if adb_serials and adb_serial not in adb_serials:
                continue
            if self.options.account_type and account_type != self.options.account_type:
                continue
            if str(phone.get("current_status") or "") in {"disabled", "removed"}:
                continue
            selected.append(phone)
        return selected[: max(1, self.options.max_workers)]

    def run_forever(self) -> dict[str, Any]:
        previous_adb_kill_guard = os.environ.get("GROUP_CONTROL_ADB_DISABLE_KILL_SERVER")
        os.environ["GROUP_CONTROL_ADB_DISABLE_KILL_SERVER"] = "1"
        if self.options.steady_state_gate_enabled and self.options.recover_stale_before_start:
            self.worker_state.recover_stale(limit=100, reason="worker lease expired before steady-state start")
        phones = self.select_phones()
        try:
            if self.options.steady_state_gate_enabled and not self._readiness_prechecked:
                readiness = self.readiness_report(phones)
                if not readiness["ok"]:
                    return {"status": "blocked", "workers": 0, "phones": summarize_phones(phones), "readiness": readiness, "outcomes": []}
            if not phones:
                return {"status": "no_phones", "workers": 0, "outcomes": []}

            threads: list[threading.Thread] = []
            outcomes: list[dict[str, Any]] = []
            outcome_lock = threading.Lock()

            for index, phone in enumerate(phones):
                if self.stop_event.is_set():
                    break
                worker = self.build_worker(phone)
                self.workers.append(worker)
                thread = threading.Thread(
                    target=run_worker_capture,
                    args=(worker, outcomes, outcome_lock),
                    name=f"phone-worker-{phone.get('adb_serial') or phone.get('phone_id')}",
                    daemon=False,
                )
                thread.start()
                threads.append(thread)
                if index < len(phones) - 1 and self.options.stagger_seconds > 0:
                    self.stop_event.wait(self.options.stagger_seconds)

            stopped_by_signal = False
            try:
                while any(thread.is_alive() for thread in threads):
                    if self.stop_event.wait(1):
                        stopped_by_signal = True
                        break
            except KeyboardInterrupt:
                stopped_by_signal = True
                self.stop()
            finally:
                if stopped_by_signal or any(thread.is_alive() for thread in threads):
                    self.stop()
                for thread in threads:
                    thread.join()

            status = "stopped" if stopped_by_signal or self.stop_event.is_set() else "completed"
            return {
                "status": status,
                "workers": len(threads),
                "phones": summarize_phones(phones),
                "outcomes": outcomes,
            }
        finally:
            if previous_adb_kill_guard is None:
                os.environ.pop("GROUP_CONTROL_ADB_DISABLE_KILL_SERVER", None)
            else:
                os.environ["GROUP_CONTROL_ADB_DISABLE_KILL_SERVER"] = previous_adb_kill_guard

    def build_worker(self, phone: dict[str, Any]) -> PhoneWorker:
        options = PhoneWorkerOptions(
            phone_id=str(phone.get("phone_id") or ""),
            adb_serial=str(phone.get("adb_serial") or ""),
            # The manager's account_type is only an initial phone selection filter.
            # A long-lived phone worker must not cache it, because a phone can be
            # reassigned between marketing/showcase while the worker is running.
            account_type="",
            dry_run=self.options.dry_run,
            pipeline_dry_run=self.options.pipeline_dry_run,
            allow_publish=self.options.allow_publish,
            stop_before_final_publish=self.options.stop_before_final_publish,
            timezone=self.options.timezone,
            preparation_window_minutes=self.options.preparation_window_minutes,
            require_phone_ready=self.options.require_phone_ready,
            allow_overdue=self.options.allow_overdue,
            timeout_seconds=self.options.timeout_seconds,
            poll_seconds=self.options.poll_seconds,
            max_runs=self.options.max_runs_per_worker,
            stop_when_idle=self.options.stop_when_idle,
            heartbeat_seconds=self.options.heartbeat_seconds,
            lease_seconds=self.options.lease_seconds,
            post_run_cooldown_seconds=self.options.post_run_cooldown_seconds,
            stop_on_failure=self.options.stop_on_failure,
            idle_wake_enabled=self.options.idle_wake_enabled,
            idle_wake_interval_seconds=self.options.idle_wake_interval_seconds,
        )
        return PhoneWorker(options, db_path=self.db_path)

    def list_worker_states(self, *, status: str = "", phone_id: str = "", limit: int = 100) -> list[dict[str, Any]]:
        return self.worker_state.list_workers(status=status, phone_id=phone_id, limit=limit)

    def readiness_report(self, phones: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        selected = self.select_phones() if phones is None else phones
        report = build_worker_readiness_report(
            db_path=self.db_path,
            phones=selected,
            expected_workers=self.options.expected_worker_count,
            allow_publish=self.options.allow_publish,
            pipeline_dry_run=self.options.pipeline_dry_run,
            dry_run=self.options.dry_run,
            stop_before_final_publish=self.options.stop_before_final_publish,
            require_task_per_phone=self.options.require_task_per_phone,
            recover_stale_before_start=self.options.recover_stale_before_start,
        ).to_dict()
        self._readiness_prechecked = bool(report.get("ok"))
        return report


def run_worker_capture(worker: PhoneWorker, outcomes: list[dict[str, Any]], lock: threading.Lock) -> None:
    outcome = worker.run_forever()
    with lock:
        outcomes.append(outcome)


def summarize_phones(phones: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {
            "phone_id": str(phone.get("phone_id") or ""),
            "adb_serial": str(phone.get("adb_serial") or ""),
            "account_type": str(phone.get("account_type") or ""),
        }
        for phone in phones
    ]
