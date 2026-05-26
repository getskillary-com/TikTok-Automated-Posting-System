from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from shared.queue_rules import BLOCKING_PHONE_STATUSES, CLAIMABLE_TASK_STATUSES, READY_PHONE_STATUS


@dataclass(frozen=True)
class QueueCandidate:
    task: dict[str, object]
    eligible: bool
    reason: str


@dataclass(frozen=True)
class ExecutionClaim:
    task_id: str
    attempt_id: str
    log_id: str
    phone_id: str
    attempt_no: int
    task: dict[str, object]


@dataclass(frozen=True)
class PipelineRunPlan:
    command: list[str]
    cwd: Path
    attempt_dir: Path
    config_path: Path
    state_path: Path
    stdout_path: Path
    stderr_path: Path
    manifest_path: Path
    env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PipelineRunResult:
    returncode: int
    stdout_path: Path
    stderr_path: Path
    command: list[str]
