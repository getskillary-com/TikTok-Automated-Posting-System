from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ApiResponse:
    ok: bool
    data: Any = None
    error: str = ""
    status: int = 200
