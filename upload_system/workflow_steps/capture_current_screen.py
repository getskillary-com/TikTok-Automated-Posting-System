from __future__ import annotations

import argparse
import json
from pathlib import Path

from common import add_step_args, capture_screen, load_or_create_state, load_step_config, make_adb


def main() -> int:
    parser = argparse.ArgumentParser(description="Debug helper: capture the current phone screen and visible UI text.")
    add_step_args(parser)
    parser.add_argument("--label", default="manual_capture", help="Artifact file label.")
    args = parser.parse_args()

    config = load_step_config(args.config)
    state = load_or_create_state(args, config)
    adb = make_adb(config)
    label = safe_label(args.label)
    screen = capture_screen(adb=adb, state=state, step_name=label, config=config)

    visible_rows = []
    for node in screen["nodes"]:
        if node.text or node.description:
            visible_rows.append(
                {
                    "text": node.text,
                    "description": node.description,
                    "resource_id": node.resource_id,
                    "bounds": node.bounds,
                    "clickable": node.clickable,
                    "enabled": node.enabled,
                }
            )

    output_path = Path(state["run_dir"]) / "traces" / f"{label}_visible_text.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(visible_rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"Screenshot: {screen['screenshot_path']}")
    print(f"XML: {Path(state['run_dir']) / 'xml' / f'{label}.xml'}")
    print(f"Visible text: {output_path}")
    return 0


def safe_label(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in value).strip("._-") or "manual_capture"


if __name__ == "__main__":
    raise SystemExit(main())
