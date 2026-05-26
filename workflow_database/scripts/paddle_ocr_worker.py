from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
UPLOAD_SYSTEM_ROOT = PROJECT_ROOT / "upload_system"
for import_path in (PROJECT_ROOT, UPLOAD_SYSTEM_ROOT):
    path = str(import_path)
    if path not in sys.path:
        sys.path.insert(0, path)

from upload_system.ai import paddle_ocr_worker_main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(paddle_ocr_worker_main())
