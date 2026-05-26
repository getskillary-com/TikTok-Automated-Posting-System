from __future__ import annotations

import json
import shutil
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from workflow_database.scripts.marketing_calibration import CalibrationRecorder, VERIFIED_STATUS, extract_candidates
from workflow_database.scripts.marketing_calibration_runner import parse_caption, parse_scheduled_at

WORKFLOW_STEPS = Path(__file__).resolve().parents[1] / "upload_system" / "workflow_steps"
if str(WORKFLOW_STEPS) not in sys.path:
    sys.path.insert(0, str(WORKFLOW_STEPS))

from common import infer_native_schedule_initial_from_phone_clock, load_timezone, publish_form_signal, scheduled_publish_page_signal
from paste_caption import caption_with_trailing_space, should_hide_keyboard_after_paste
from upload_system.adb import UINode


class MarketingCalibrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(".test_tmp") / self.id().replace(".", "_")
        shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_upload_trace_extracts_successful_attempt_as_profile_point(self) -> None:
        candidates = extract_candidates(
            profile={},
            step={"id": "tap_upload"},
            trace={
                "action": "tap_upload",
                "result": "media_picker_visible",
                "attempts": [
                    {"x": 10, "y": 20, "label": "miss", "source": "fallback"},
                    {"x": 540, "y": 542, "label": "upload_button_center", "source": "configured_fallback"},
                ],
            },
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].point_name, "upload_button_center")
        self.assertEqual((candidates[0].x, candidates[0].y), (540, 542))

    def test_recorder_writes_verified_coordinate_to_profile_and_jsonl(self) -> None:
        profile_path = self.root / "profile.json"
        run_dir = self.root / "run"
        trace_dir = run_dir / "traces"
        trace_dir.mkdir(parents=True)
        state_path = run_dir / "state.json"
        profile = {
            "screen": {"width": 1080, "height": 2400},
            "coordinate_points": {
                "caption_field": {
                    "x": 1,
                    "y": 2,
                    "x_ratio": 0.001,
                    "y_ratio": 0.001,
                    "description": "caption",
                    "calibration_status": "seed",
                    "verified_at": "",
                }
            },
        }
        write_json(profile_path, profile)
        write_json(
            state_path,
            {
                "run_dir": str(run_dir.resolve()),
                "state_path": str(state_path.resolve()),
                "dry_run": False,
                "completed_steps": ["paste_caption"],
            },
        )
        write_json(
            trace_dir / "paste_caption.json",
            {"action": "tap", "x": 540, "y": 480, "label": "Caption", "source": "ui", "confidence": 0.9},
        )

        recorder = CalibrationRecorder(
            profile_path=profile_path.resolve(),
            save_profile_coordinates=True,
            serial="serial-1",
            model="model-1",
        )
        updated = recorder.record_step_result(
            profile=profile,
            state_path=state_path.resolve(),
            step={"id": "paste_caption"},
            success=True,
        )

        point = updated["coordinate_points"]["caption_field"]
        self.assertEqual((point["x"], point["y"]), (540, 480))
        self.assertEqual(point["calibration_status"], VERIFIED_STATUS)
        events = [json.loads(line) for line in (run_dir / "calibration_events.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(events[-1]["point_name"], "caption_field")
        self.assertTrue(events[-1]["saved_to_profile"])

    def test_material_parser_reads_caption_and_chinese_publish_time(self) -> None:
        text = "视频文案: Have a nice day\n发布时间: 2026年5月20日 12:00\n"

        self.assertEqual(parse_caption(text), "Have a nice day")
        self.assertEqual(parse_scheduled_at(text, timezone_name="America/Sao_Paulo"), "2026-05-20T12:00:00-03:00")

    def test_phone_clock_schedule_initial_ceil_rounds_crossing_minute(self) -> None:
        initial = infer_native_schedule_initial_from_phone_clock(
            {
                "iso": "2026-05-12T00:00:54-03:00",
                "timezone": "America/Sao_Paulo",
            },
            {"initial_offset_minutes": 30},
        )

        self.assertEqual(initial["time_24"], "00:31")
        self.assertEqual(initial["initial_rounding"], "ceil_minute")

    def test_publish_form_signal_requires_caption_and_bottom_button(self) -> None:
        publish_form = {
            "screen_size": (720, 1457),
            "nodes": [
                ui_node(class_name="android.widget.EditText", center_x=240, center_y=291),
                ui_node(class_name="android.widget.Button", center_x=533, center_y=1430),
            ],
        }
        edit_screen = {
            "screen_size": (720, 1457),
            "nodes": [
                ui_node(class_name="android.widget.Button", center_x=667, center_y=296),
                ui_node(class_name="android.widget.TextView", center_x=360, center_y=1338),
            ],
        }

        self.assertTrue(publish_form_signal(publish_form)["looks_like_publish_form"])
        self.assertFalse(publish_form_signal(edit_screen)["looks_like_publish_form"])

    def test_scheduled_publish_signal_accepts_small_minute_drift(self) -> None:
        target_date = (datetime.now(load_timezone("America/Sao_Paulo")) + timedelta(days=1)).date()
        screen = {
            "screen_size": (1080, 2400),
            "nodes": [
                ui_node(
                    class_name="android.widget.TextView",
                    center_x=540,
                    center_y=900,
                    text=f"{target_date.month}月{target_date.day}日, 08:42",
                ),
                ui_node(class_name="android.widget.Button", center_x=540, center_y=2260, text="预约发布"),
            ],
        }

        signal = scheduled_publish_page_signal(
            screen,
            target_date_iso=target_date.isoformat(),
            target_time_24="08:43",
            target_timezone="America/Sao_Paulo",
            time_tolerance_minutes=2,
        )

        self.assertTrue(signal["has_target_text"])
        self.assertTrue(signal["has_target_time"])
        self.assertFalse(signal["has_exact_target_time"])
        self.assertTrue(signal["time_tolerance"]["matched"])
        self.assertEqual(signal["time_tolerance"]["actual"]["time_24"], "08:42")

    def test_scheduled_publish_signal_rejects_large_minute_drift(self) -> None:
        target_date = (datetime.now(load_timezone("America/Sao_Paulo")) + timedelta(days=1)).date()
        screen = {
            "screen_size": (1080, 2400),
            "nodes": [
                ui_node(
                    class_name="android.widget.TextView",
                    center_x=540,
                    center_y=900,
                    text=f"{target_date.month}月{target_date.day}日, 08:39",
                ),
                ui_node(class_name="android.widget.Button", center_x=540, center_y=2260, text="预约发布"),
            ],
        }

        signal = scheduled_publish_page_signal(
            screen,
            target_date_iso=target_date.isoformat(),
            target_time_24="08:43",
            target_timezone="America/Sao_Paulo",
            time_tolerance_minutes=2,
        )

        self.assertFalse(signal["has_target_text"])
        self.assertFalse(signal["has_target_time"])
        self.assertFalse(signal["time_tolerance"]["matched"])

    def test_caption_with_trailing_space_adds_one_space_only(self) -> None:
        self.assertEqual(caption_with_trailing_space("caption"), ("caption ", True))
        self.assertEqual(caption_with_trailing_space("caption "), ("caption ", False))
        self.assertEqual(caption_with_trailing_space(""), ("", False))

    def test_adb_keyboard_paste_hides_with_back_by_default(self) -> None:
        self.assertTrue(
            should_hide_keyboard_after_paste(
                paste_config={"hide_keyboard_after_paste": True},
                paste_mode="adb_keyboard",
                dry_run=False,
            )
        )
        self.assertTrue(
            should_hide_keyboard_after_paste(
                paste_config={"hide_keyboard_after_paste": True},
                paste_mode="clipboard_keyevent",
                dry_run=False,
            )
        )


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def ui_node(
    *,
    class_name: str,
    center_x: int,
    center_y: int,
    text: str = "",
    description: str = "",
) -> UINode:
    return UINode(
        index=0,
        text=text,
        description=description,
        resource_id="",
        class_name=class_name,
        clickable="true",
        enabled="true",
        bounds="",
        center_x=center_x,
        center_y=center_y,
    )
