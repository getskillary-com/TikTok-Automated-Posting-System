from __future__ import annotations

import json
from unittest import mock
import unittest
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
for import_path in (ROOT / "upload_system", ROOT / "upload_system" / "workflow_steps"):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

from execution_queue.models import ExecutionClaim, PipelineRunPlan
from execution_queue.pipeline_adapter import UploadPipelineAdapter
from execution_queue.queue_service import (
    ExecutionQueueService,
    failure_is_retryable,
    pipeline_stopped_before_final_publish,
    validate_pipeline_success,
    validate_task_payload,
)
from execution_queue.queue_repository import ExecutionQueueRepository
from execution_queue.supervisor import SupervisorOptions, WorkerSupervisor
from execution_queue.worker_manager import WorkerManager, WorkerManagerOptions
from execution_queue.worker_state import WorkerStateRepository
from release_task_list.task_repository import ReleaseTaskRepository
from shared.database import init_database, session
from shared.queue_rules import evaluate_task_candidate
from common import resolve_publish_schedule
from workflow_database.scripts.run_showcase_workflow import (
    added_music_close_tap,
    build_context,
    count_media_picker_items,
    count_media_picker_items_from_screenshot,
    first_product_link_match,
    first_matching_node_center,
    first_ocr_text_match,
    media_picker_count_matches,
    normalize_product_match_text,
    product_link_keywords,
    xml_has_package,
)


class AccountTypeQueueRuleTests(unittest.TestCase):
    def test_marketing_without_product_is_claimable_immediately(self) -> None:
        now = datetime(2026, 5, 4, 10, 0, tzinfo=timezone.utc)
        eligible, reason = evaluate_task_candidate(
            base_task(
                phone_account_type="marketing",
                publish_mode="scheduled",
                scheduled_at=(now + timedelta(hours=6)).isoformat(),
            ),
            now=now,
            preparation_window_minutes=60,
            require_phone_ready=True,
            allow_overdue=False,
        )
        self.assertTrue(eligible)
        self.assertIn("claimable immediately", reason)

    def test_marketing_past_schedule_still_requires_allow_overdue(self) -> None:
        now = datetime(2026, 5, 4, 10, 0, tzinfo=timezone.utc)
        eligible, reason = evaluate_task_candidate(
            base_task(
                phone_account_type="marketing",
                publish_mode="scheduled",
                scheduled_at=(now - timedelta(minutes=1)).isoformat(),
            ),
            now=now,
            preparation_window_minutes=60,
            require_phone_ready=True,
            allow_overdue=False,
        )
        self.assertFalse(eligible)
        self.assertIn("already past", reason)

    def test_marketing_with_product_is_not_claimable(self) -> None:
        now = datetime(2026, 5, 4, 10, 0, tzinfo=timezone.utc)
        eligible, reason = evaluate_task_candidate(
            base_task(
                phone_account_type="marketing",
                publish_mode="scheduled",
                scheduled_at=(now + timedelta(minutes=30)).isoformat(),
                product_name="Product",
            ),
            now=now,
            preparation_window_minutes=60,
            require_phone_ready=True,
            allow_overdue=False,
        )
        self.assertFalse(eligible)
        self.assertEqual(reason, "marketing task cannot contain product fields")

    def test_marketing_immediate_is_not_claimable(self) -> None:
        now = datetime(2026, 5, 4, 10, 0, tzinfo=timezone.utc)
        eligible, reason = evaluate_task_candidate(
            base_task(
                phone_account_type="marketing",
                publish_mode="immediate",
                scheduled_at=(now - timedelta(minutes=1)).isoformat(),
            ),
            now=now,
            preparation_window_minutes=60,
            require_phone_ready=True,
            allow_overdue=False,
        )
        self.assertFalse(eligible)
        self.assertEqual(reason, "marketing only allows scheduled publish_mode")

    def test_showcase_immediate_with_product_is_claimable(self) -> None:
        now = datetime(2026, 5, 4, 10, 0, tzinfo=timezone.utc)
        eligible, reason = evaluate_task_candidate(
            base_task(
                phone_account_type="showcase",
                publish_mode="immediate",
                scheduled_at=(now - timedelta(minutes=1)).isoformat(),
                product_search_title="Product",
            ),
            now=now,
            preparation_window_minutes=60,
            require_phone_ready=True,
            allow_overdue=False,
        )
        self.assertTrue(eligible)
        self.assertEqual(reason, "immediate task is due")

    def test_showcase_without_product_is_not_claimable(self) -> None:
        now = datetime(2026, 5, 4, 10, 0, tzinfo=timezone.utc)
        eligible, reason = evaluate_task_candidate(
            base_task(
                phone_account_type="showcase",
                publish_mode="immediate",
                scheduled_at=(now - timedelta(minutes=1)).isoformat(),
            ),
            now=now,
            preparation_window_minutes=60,
            require_phone_ready=True,
            allow_overdue=False,
        )
        self.assertFalse(eligible)
        self.assertEqual(reason, "showcase task requires product info")

    def test_showcase_scheduled_with_product_is_claimable_immediately(self) -> None:
        now = datetime(2026, 5, 4, 10, 0, tzinfo=timezone.utc)
        eligible, reason = evaluate_task_candidate(
            base_task(
                phone_account_type="showcase",
                publish_mode="scheduled",
                scheduled_at=(now + timedelta(hours=6)).isoformat(),
                product_search_title="Product",
            ),
            now=now,
            preparation_window_minutes=60,
            require_phone_ready=True,
            allow_overdue=False,
        )
        self.assertTrue(eligible)
        self.assertIn("showcase scheduled task is claimable immediately", reason)

    def test_showcase_past_schedule_still_requires_allow_overdue(self) -> None:
        now = datetime(2026, 5, 4, 10, 0, tzinfo=timezone.utc)
        eligible, reason = evaluate_task_candidate(
            base_task(
                phone_account_type="showcase",
                publish_mode="scheduled",
                scheduled_at=(now - timedelta(minutes=1)).isoformat(),
                product_search_title="Product",
            ),
            now=now,
            preparation_window_minutes=60,
            require_phone_ready=True,
            allow_overdue=False,
        )
        self.assertFalse(eligible)
        self.assertIn("already past", reason)


class ScheduleLeadWindowTests(unittest.TestCase):
    def test_resolve_publish_schedule_extends_close_scheduled_target(self) -> None:
        target = (datetime.now(timezone.utc).replace(microsecond=0) + timedelta(minutes=20))
        schedule = resolve_publish_schedule(
            {
                "publish_mode": "scheduled",
                "scheduled_at": target.isoformat(),
                "schedule_timezone": "UTC",
            },
            {
                "pipeline": {
                    "schedule": {
                        "timezone": "UTC",
                        "auto_extend_if_within_minutes": 40,
                        "auto_extend_by_minutes": 30,
                    }
                }
            },
        )

        expected = target + timedelta(minutes=30)
        self.assertEqual(schedule["target_iso"], expected.isoformat())
        self.assertEqual(schedule["auto_extension"]["original_target_iso"], target.isoformat())
        self.assertEqual(schedule["auto_extension"]["adjusted_target_iso"], expected.isoformat())

    def test_resolve_publish_schedule_keeps_safe_scheduled_target(self) -> None:
        target = (datetime.now(timezone.utc).replace(microsecond=0) + timedelta(minutes=50))
        schedule = resolve_publish_schedule(
            {
                "publish_mode": "scheduled",
                "scheduled_at": target.isoformat(),
                "schedule_timezone": "UTC",
            },
            {
                "pipeline": {
                    "schedule": {
                        "timezone": "UTC",
                        "auto_extend_if_within_minutes": 40,
                        "auto_extend_by_minutes": 30,
                    }
                }
            },
        )

        self.assertEqual(schedule["target_iso"], target.isoformat())
        self.assertNotIn("auto_extension", schedule)

    def test_resolve_publish_schedule_accepts_powershell_iso_fraction(self) -> None:
        target = (datetime.now(timezone.utc).replace(microsecond=234764) + timedelta(minutes=50))
        powershell_iso = target.isoformat().replace("+00:00", "0+00:00")

        schedule = resolve_publish_schedule(
            {
                "publish_mode": "scheduled",
                "scheduled_at": powershell_iso,
                "schedule_timezone": "UTC",
            },
            {
                "pipeline": {
                    "schedule": {
                        "timezone": "UTC",
                        "auto_extend_if_within_minutes": 40,
                        "auto_extend_by_minutes": 30,
                    }
                }
            },
        )

        self.assertEqual(schedule["target_iso"], target.isoformat())


class ShowcaseOcrSafetyTests(unittest.TestCase):
    def test_first_ocr_text_match_requires_confidence(self) -> None:
        candidates = [
            SimpleNamespace(text="确认添加", confidence=0.2),
            SimpleNamespace(text="其他文字", confidence=0.9),
        ]
        self.assertIsNone(first_ocr_text_match(candidates, ["确认添加"], min_confidence=0.3))

    def test_first_ocr_text_match_finds_configured_text(self) -> None:
        candidates = [
            SimpleNamespace(text="是否添加商品", confidence=0.85),
        ]
        match = first_ocr_text_match(candidates, ["添加商品"], min_confidence=0.3)
        self.assertIsNotNone(match)
        self.assertEqual(match[1], "添加商品")

    def test_first_ocr_text_match_finds_confirm_product_popup_title(self) -> None:
        candidates = [
            SimpleNamespace(text="添加商品？", confidence=0.95),
        ]
        match = first_ocr_text_match(candidates, ["添加商品？"], min_confidence=0.3)
        self.assertIsNotNone(match)
        self.assertEqual(match[1], "添加商品？")

    def test_first_ocr_text_match_can_choose_button_below_popup_title(self) -> None:
        candidates = [
            SimpleNamespace(text="添加", confidence=1.0, y=598),
            SimpleNamespace(text="添加商品？", confidence=0.95, y=951),
            SimpleNamespace(text="添加", confidence=1.0, y=1228),
        ]
        title = first_ocr_text_match(candidates, ["添加商品？"], min_confidence=0.3)
        self.assertIsNotNone(title)
        button = first_ocr_text_match(candidates, ["添加"], match_mode="exact", min_confidence=0.3, min_y=title[0].y)
        self.assertIsNotNone(button)
        self.assertEqual(button[0].y, 1228)

    def test_product_link_match_blocks_deleted_product_chip(self) -> None:
        context = {
            "product_publish_name": "High-quality wireless audio he",
            "product_search_title": "Upgraded High-Quality Wireless",
        }
        keywords = product_link_keywords(context, min_prefix_chars=10)
        self.assertEqual(keywords, ["high-quali", "upgradedhi"])

        with_product = '<hierarchy><node text="High-quality wireless audio he" content-desc="" /></hierarchy>'
        without_product = '<hierarchy><node text="Add link" content-desc="" /></hierarchy>'
        self.assertIsNotNone(first_product_link_match(with_product, keywords))
        self.assertIsNone(first_product_link_match(without_product, keywords))

    def test_showcase_optional_confirm_steps_require_ocr(self) -> None:
        root = Path(__file__).resolve().parents[1]
        profile_paths = sorted((root / "workflow_database" / "devices").glob("*/showcase_tiktok_app*.json"))
        self.assertGreaterEqual(len(profile_paths), 1)
        target_ids = {"optional_confirm_add_product", "optional_confirm_sponsored_content"}
        for profile_path in profile_paths:
            with self.subTest(profile=profile_path):
                import json

                profile = json.loads(profile_path.read_text(encoding="utf-8"))
                steps = {str(step.get("id") or ""): step for step in profile.get("workflow", [])}
                self.assertTrue(target_ids.issubset(steps))
                for step_id in target_ids:
                    step = steps[step_id]
                    self.assertTrue(step.get("ocr_required"), step_id)
                    self.assertEqual(step.get("ocr_match_mode"), "contains")
                    self.assertGreaterEqual(float(step.get("ocr_min_confidence", 0)), 0.3)
                    self.assertTrue(step.get("ocr_texts"), step_id)
                    self.assertTrue(step.get("ocr_presence_texts"), step_id)
                    self.assertTrue(step.get("ocr_confirm_texts"), step_id)
                    self.assertEqual(step.get("ocr_confirm_match_mode"), "exact")
                    self.assertTrue(step.get("ocr_allow_calibrated_point_fallback"), step_id)
                    if step_id == "optional_confirm_add_product":
                        self.assertIn("添加商品？", step.get("ocr_presence_texts", []))
                        self.assertIn("添加", step.get("ocr_confirm_texts", []))
                        self.assertNotIn("添加商品", step.get("ocr_texts", []))
                        self.assertNotIn("添加", step.get("ocr_texts", []))
                    if step_id == "optional_confirm_sponsored_content":
                        presence_texts = step.get("ocr_presence_texts", [])
                        self.assertIn("You need to disclose", presence_texts)
                        self.assertIn("this video is sponsored", presence_texts)
                        self.assertNotIn("Content disclosure", presence_texts)
                        self.assertNotIn("Sponsored content", presence_texts)
                        self.assertNotIn("Sponsored", presence_texts)
                        self.assertIn("同意发布", step.get("ocr_confirm_texts", []))
                        self.assertNotIn("同意发布", step.get("ocr_presence_texts", []))
                workflow = profile.get("workflow", [])
                workflow_ids = [str(step.get("id") or "") for step in workflow]
                assert_index = workflow_ids.index("assert_product_link_attached")
                expected_schedule_steps = [
                    "open_more_settings",
                    "open_schedule_publish",
                    "wait_schedule_picker_open",
                    "set_schedule_datetime",
                    "verify_schedule_configured",
                    "close_more_settings",
                    "tap_scheduled_publish",
                ]
                self.assertEqual(workflow_ids[assert_index + 1 : assert_index + 1 + len(expected_schedule_steps)], expected_schedule_steps)
                steps_by_id = {str(step.get("id") or ""): step for step in workflow}
                more_settings = steps_by_id["open_more_settings"]
                schedule_publish = steps_by_id["open_schedule_publish"]
                close_more_settings = steps_by_id["close_more_settings"]
                scheduled_publish_button = steps_by_id["tap_scheduled_publish"]
                self.assertTrue(more_settings.get("require_text_match"))
                self.assertTrue(schedule_publish.get("require_text_match"))
                self.assertEqual(close_more_settings.get("type"), "tap_point")
                self.assertEqual(close_more_settings.get("point"), "schedule_close")
                self.assertTrue(scheduled_publish_button.get("require_text_match"))
                self.assertIn("更多选项", more_settings.get("texts", []))
                self.assertIn("更多设置", more_settings.get("texts", []))
                self.assertNotIn("????", more_settings.get("texts", []))
                self.assertIn("预约发布作品", schedule_publish.get("texts", []))
                self.assertIn("预约发布", schedule_publish.get("texts", []))
                self.assertIn("定时发布", schedule_publish.get("texts", []))
                self.assertNotIn("????", schedule_publish.get("texts", []))
                self.assertIn("预约发布", scheduled_publish_button.get("texts", []))
                self.assertNotIn("????", scheduled_publish_button.get("texts", []))
                self.assertNotIn("??", scheduled_publish_button.get("texts", []))
                self.assertEqual(scheduled_publish_button.get("xml_match_mode"), "exact")
                self.assertEqual(steps_by_id["tap_publish"].get("publish_modes"), ["immediate"])
                self.assertEqual(steps_by_id["tap_scheduled_publish"].get("publish_modes"), ["scheduled"])

    def test_showcase_optional_music_warning_close_uses_ocr_presence_and_point_fallback(self) -> None:
        root = Path(__file__).resolve().parents[1]
        profile_paths = sorted((root / "workflow_database" / "devices").glob("*/showcase_tiktok_app.json"))
        self.assertGreaterEqual(len(profile_paths), 1)
        for profile_path in profile_paths:
            with self.subTest(profile=profile_path):
                import json

                profile = json.loads(profile_path.read_text(encoding="utf-8"))
                self.assertIn("music_warning_close", profile.get("coordinate_points", {}))
                steps = {str(step.get("id") or ""): step for step in profile.get("workflow", [])}
                step = steps.get("optional_close_music_warning")
                self.assertIsNotNone(step)
                assert step is not None
                self.assertTrue(step.get("ocr_required"))
                self.assertIn("可能被静音", step.get("ocr_presence_texts", []))
                self.assertEqual(step.get("ocr_confirm_texts"), [])
                self.assertTrue(step.get("ocr_allow_calibrated_point_fallback"))
                self.assertEqual(step.get("point"), "music_warning_close")

    def test_showcase_removes_added_music_before_publish_preset(self) -> None:
        root = Path(__file__).resolve().parents[1]
        profile_paths = sorted((root / "workflow_database" / "devices").glob("*/showcase_tiktok_app*.json"))
        self.assertGreaterEqual(len(profile_paths), 1)
        for profile_path in profile_paths:
            with self.subTest(profile=profile_path):
                profile = json.loads(profile_path.read_text(encoding="utf-8"))
                workflow_ids = [str(step.get("id") or "") for step in profile.get("workflow", [])]
                self.assertLess(workflow_ids.index("select_first_video"), workflow_ids.index("optional_remove_added_music"))
                self.assertLess(workflow_ids.index("optional_remove_added_music"), workflow_ids.index("tap_next_until_publish_preset"))
                step = next(step for step in profile["workflow"] if step.get("id") == "optional_remove_added_music")
                self.assertEqual(step.get("type"), "optional_remove_added_music")
                self.assertEqual(step.get("required_app"), "tiktok")

    def test_added_music_close_tap_ignores_add_music_button(self) -> None:
        xml = """<?xml version='1.0' encoding='UTF-8'?><hierarchy>
            <node bounds="[0,0][1080,2400]">
              <node text="添加音乐" content-desc="" class="android.widget.TextView" bounds="[469,169][677,289]" />
            </node>
        </hierarchy>"""
        self.assertIsNone(added_music_close_tap(xml, {}))

    def test_added_music_close_tap_detects_top_music_close(self) -> None:
        xml = """<?xml version='1.0' encoding='UTF-8'?><hierarchy>
            <node bounds="[0,0][1080,2400]">
              <node text="ally.music - 原声" content-desc="" class="android.widget.TextView" bounds="[357,169][703,289]" />
              <node text="" content-desc="关闭" class="android.widget.ImageView" bounds="[712,201][811,256]" />
            </node>
        </hierarchy>"""
        tap = added_music_close_tap(xml, {})
        self.assertIsNotNone(tap)
        assert tap is not None
        self.assertEqual(tap["x"], 761)
        self.assertEqual(tap["y"], 228)

    def test_showcase_context_truncates_product_search_title_to_normalized_25_prefix(self) -> None:
        args = SimpleNamespace(
            caption="Caption",
            product_search_title="Upgraded High-Quality Wireless Charger",
            product_publish_name="",
            allow_publish=False,
            capture_mode="all",
            wait_scale=1.0,
        )
        context = build_context(
            args,
            {"inputs": {"product_search_title_max_chars": 30, "product_publish_name_max_chars": 30}},
            Path(".test_tmp"),
        )

        self.assertEqual(context["product_search_title"], "Upgraded High-Quality Wirele")
        self.assertEqual(len(normalize_product_match_text(context["product_search_title"])), 25)


class PipelineSuccessValidationTests(unittest.TestCase):
    def test_showcase_scheduled_success_uses_scheduled_publish_step(self) -> None:
        state = {
            "dry_run": False,
            "allow_publish": True,
            "remote_video_path": "/sdcard/DCIM/Camera/video.mp4",
            "publish_mode": "scheduled",
            "account_type": "showcase",
            "schedule_configured_verified": True,
            "completed_steps": [
                "push_video",
                "tap_product_final_add",
                "set_schedule_datetime",
                "verify_schedule_configured",
                "tap_scheduled_publish",
                "minimize_tiktok_app",
            ],
        }

        error = validate_pipeline_success(
            pipeline_plan_with_state(state),
            pipeline_claim(phone_account_type="showcase", publish_mode="scheduled"),
        )

        self.assertEqual(error, "")

    def test_showcase_scheduled_success_rejects_immediate_publish_step(self) -> None:
        state = {
            "dry_run": False,
            "allow_publish": True,
            "remote_video_path": "/sdcard/DCIM/Camera/video.mp4",
            "publish_mode": "scheduled",
            "account_type": "showcase",
            "schedule_configured_verified": True,
            "completed_steps": [
                "push_video",
                "tap_product_final_add",
                "set_schedule_datetime",
                "verify_schedule_configured",
                "tap_publish",
                "minimize_tiktok_app",
            ],
        }

        error = validate_pipeline_success(
            pipeline_plan_with_state(state),
            pipeline_claim(phone_account_type="showcase", publish_mode="scheduled"),
        )

        self.assertIn("tap_scheduled_publish", error)

    def test_showcase_scheduled_success_requires_schedule_verification(self) -> None:
        state = {
            "dry_run": False,
            "allow_publish": True,
            "remote_video_path": "/sdcard/DCIM/Camera/video.mp4",
            "publish_mode": "scheduled",
            "account_type": "showcase",
            "schedule_configured_verified": False,
            "completed_steps": [
                "push_video",
                "tap_product_final_add",
                "set_schedule_datetime",
                "verify_schedule_configured",
                "tap_scheduled_publish",
                "minimize_tiktok_app",
            ],
        }

        error = validate_pipeline_success(
            pipeline_plan_with_state(state),
            pipeline_claim(phone_account_type="showcase", publish_mode="scheduled"),
        )

        self.assertEqual(error, "pipeline success validation failed: scheduled publish state was not verified")

    def test_stop_before_final_publish_state_is_detected(self) -> None:
        plan = pipeline_plan_with_state(
            {
                "dry_run": False,
                "allow_publish": False,
                "stop_before_final_publish": True,
                "stopped_before_final_publish": True,
                "stopped_before_final_publish_step": "tap_scheduled_publish",
                "remote_video_path": "/sdcard/DCIM/Camera/video.mp4",
                "publish_mode": "scheduled",
                "account_type": "showcase",
                "schedule_configured_verified": True,
            }
        )

        self.assertTrue(pipeline_stopped_before_final_publish(plan.state_path))


class ReleaseTaskRepositoryConstraintTests(unittest.TestCase):
    def setUp(self) -> None:
        temp_root = Path(".test_tmp")
        temp_root.mkdir(exist_ok=True)
        safe_name = self.id().replace(".", "_")
        self.db_path = temp_root / f"{safe_name}.db"
        remove_sqlite_files(self.db_path)
        init_database(self.db_path)
        seed_database(self.db_path)
        self.repository = ReleaseTaskRepository(self.db_path)

    def tearDown(self) -> None:
        remove_sqlite_files(self.db_path)

    def test_create_marketing_product_task_fails(self) -> None:
        with self.assertRaisesRegex(ValueError, "marketing task cannot contain product fields"):
            self.repository.create_task(
                video_id="VID-1",
                phone_id="PHN-MKT",
                scheduled_at="now",
                publish_mode="scheduled",
                product_name="Product",
                allow_reuse=True,
            )

    def test_create_showcase_without_product_fails(self) -> None:
        with self.assertRaisesRegex(ValueError, "showcase task requires product info"):
            self.repository.create_task(
                video_id="VID-1",
                phone_id="PHN-SHOW",
                scheduled_at="now",
                publish_mode="immediate",
                allow_reuse=True,
            )

    def test_create_showcase_scheduled_with_product_succeeds(self) -> None:
        result = self.repository.create_task(
            video_id="VID-1",
            phone_id="PHN-SHOW",
            scheduled_at="now",
            publish_mode="scheduled",
            product_name="Product",
            allow_reuse=True,
        )
        self.assertTrue(result.task_id)

    def test_assign_to_incompatible_phone_fails(self) -> None:
        result = self.repository.create_task(
            video_id="VID-1",
            phone_id="PHN-MKT",
            scheduled_at="now",
            publish_mode="scheduled",
            allow_reuse=True,
        )
        with self.assertRaisesRegex(ValueError, "showcase task requires product info"):
            self.repository.assign_phone(result.task_id, "PHN-SHOW")

    def test_reschedule_showcase_scheduled_task_succeeds(self) -> None:
        insert_invalid_showcase_scheduled_task(self.db_path)
        self.repository.reschedule("TSK-BAD-SHOW", "2026-05-04T11:00:00+00:00")
        with session(self.db_path) as connection:
            task = connection.execute("SELECT scheduled_at FROM release_tasks WHERE task_id = 'TSK-BAD-SHOW'").fetchone()
        self.assertEqual(task["scheduled_at"], "2026-05-04T11:00:00+00:00")


class ExecutionQueuePhoneClaimTests(unittest.TestCase):
    def setUp(self) -> None:
        temp_root = Path(".test_tmp")
        temp_root.mkdir(exist_ok=True)
        safe_name = self.id().replace(".", "_")
        self.db_path = temp_root / f"{safe_name}.db"
        remove_sqlite_files(self.db_path)
        init_database(self.db_path)
        seed_database(self.db_path)
        insert_claim_filter_tasks(self.db_path)
        self.repository = ExecutionQueueRepository(self.db_path)

    def tearDown(self) -> None:
        remove_sqlite_files(self.db_path)

    def test_list_candidates_can_filter_by_phone_id(self) -> None:
        candidates = self.repository.list_candidates(
            phone_id="PHN-MKT",
            allow_overdue=True,
            require_phone_ready=True,
        )
        self.assertEqual([candidate.task["task_id"] for candidate in candidates], ["TSK-MKT-CLAIM"])

    def test_claim_next_records_worker_lock_for_phone(self) -> None:
        claim = self.repository.claim_next(
            phone_id="PHN-MKT",
            worker_id="WRK-TEST",
            allow_overdue=True,
            require_phone_ready=True,
        )
        self.assertIsNotNone(claim)
        assert claim is not None
        self.assertEqual(claim.task_id, "TSK-MKT-CLAIM")
        with session(self.db_path) as connection:
            task = connection.execute("SELECT status, locked_by_worker, lock_expires_at FROM release_tasks WHERE task_id = ?", (claim.task_id,)).fetchone()
            phone = connection.execute("SELECT current_status FROM phones WHERE phone_id = ?", ("PHN-MKT",)).fetchone()
        self.assertEqual(task["status"], "running")
        self.assertEqual(task["locked_by_worker"], "WRK-TEST")
        self.assertTrue(task["lock_expires_at"])
        self.assertEqual(phone["current_status"], "running")

    def test_claim_next_takes_future_marketing_task_immediately(self) -> None:
        now = datetime(2026, 5, 4, 10, 0, tzinfo=timezone.utc).replace(microsecond=0).isoformat()
        with session(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO release_tasks(
                    task_id, video_id, phone_id, scheduled_at, publish_mode, status, created_at, updated_at
                )
                VALUES('TSK-MKT-FUTURE', 'VID-1', 'PHN-MKT', '2099-01-01T10:30:00+00:00', 'scheduled', 'pending', ?, ?)
                """,
                (now, now),
            )

        claim = self.repository.claim_next(
            task_id="TSK-MKT-FUTURE",
            phone_id="PHN-MKT",
            worker_id="WRK-FUTURE",
            preparation_window_minutes=60,
            allow_overdue=False,
            require_phone_ready=True,
        )

        self.assertIsNotNone(claim)
        assert claim is not None
        self.assertEqual(claim.task_id, "TSK-MKT-FUTURE")

    def test_mark_debug_ready_sets_terminal_task_without_publish_counters(self) -> None:
        claim = self.repository.claim_next(
            phone_id="PHN-SHOW",
            allow_overdue=True,
            require_phone_ready=True,
        )
        self.assertIsNotNone(claim)
        assert claim is not None
        state_path = self.db_path.with_suffix(".state.json")
        run_dir = self.db_path.parent / "debug-ready-run"
        run_dir.mkdir(exist_ok=True)
        state_path.write_text(
            json.dumps(
                {
                    "scheduled_at": "2026-05-04T10:45:00+00:00",
                    "stop_before_final_publish": True,
                    "stopped_before_final_publish": True,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        self.repository.mark_debug_ready(claim, run_dir=str(run_dir), state_path=str(state_path))

        with session(self.db_path) as connection:
            task = connection.execute("SELECT status, scheduled_at FROM release_tasks WHERE task_id = ?", (claim.task_id,)).fetchone()
            phone = connection.execute("SELECT current_status, daily_publish_count FROM phones WHERE phone_id = ?", (claim.phone_id,)).fetchone()
            attempt = connection.execute("SELECT result FROM task_attempts WHERE attempt_id = ?", (claim.attempt_id,)).fetchone()
            log = connection.execute("SELECT result FROM execution_logs WHERE log_id = ?", (claim.log_id,)).fetchone()

        self.assertEqual(task["status"], "debug_ready")
        self.assertEqual(task["scheduled_at"], "2026-05-04T10:45:00+00:00")
        self.assertEqual(phone["current_status"], "online_idle")
        self.assertEqual(phone["daily_publish_count"], 0)
        self.assertEqual(attempt["result"], "debug_ready")
        self.assertEqual(log["result"], "debug_ready")

    def test_non_retryable_failure_marks_task_failed_immediately(self) -> None:
        claim = self.repository.claim_next(
            phone_id="PHN-SHOW",
            allow_overdue=True,
            require_phone_ready=True,
        )
        self.assertIsNotNone(claim)
        assert claim is not None
        state_path = self.db_path.with_suffix(".state.json")
        state_path.write_text("{}\n", encoding="utf-8")
        run_dir = self.db_path.parent / "non-retryable-run"
        run_dir.mkdir(exist_ok=True)

        next_status = self.repository.mark_failure(
            claim,
            run_dir=str(run_dir),
            state_path=str(state_path),
            failure_reason="task validation failed: video file does not exist",
            retryable=False,
        )

        with session(self.db_path) as connection:
            task = connection.execute("SELECT status, retry_count FROM release_tasks WHERE task_id = ?", (claim.task_id,)).fetchone()
        self.assertEqual(next_status, "failed")
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["retry_count"], 1)

    def test_phone_worker_from_account_type_manager_uses_live_phone_account_type(self) -> None:
        manager = WorkerManager(WorkerManagerOptions(account_type="marketing"), self.db_path)
        worker = manager.build_worker(
            {
                "phone_id": "PHN-MKT",
                "adb_serial": "SERIAL-MKT",
                "account_type": "marketing",
            }
        )

        self.assertEqual(worker.options.account_type, "")

    def test_changed_phone_account_type_claims_matching_workflow_without_stale_filter(self) -> None:
        now = datetime(2026, 5, 4, 10, 0, tzinfo=timezone.utc).replace(microsecond=0).isoformat()
        with session(self.db_path) as connection:
            connection.execute("UPDATE release_tasks SET status = 'failed' WHERE phone_id = 'PHN-MKT'")
            connection.execute(
                """
                UPDATE phones
                SET account_type = 'showcase', app_package = 'com.zhiliaoapp.musically'
                WHERE phone_id = 'PHN-MKT'
                """
            )
            connection.execute(
                """
                INSERT INTO release_tasks(
                    task_id, video_id, phone_id, scheduled_at, publish_mode,
                    product_name, product_search_title, product_publish_name,
                    status, created_at, updated_at
                )
                VALUES(
                    'TSK-MKT-NOW-SHOW', 'VID-1', 'PHN-MKT', '2026-05-04T09:59:00+00:00',
                    'immediate', 'Product', 'Product', 'Product',
                    'pending', ?, ?
                )
                """,
                (now, now),
            )

        stale_claim = self.repository.claim_next(
            task_id="TSK-MKT-NOW-SHOW",
            phone_id="PHN-MKT",
            account_type="marketing",
            allow_overdue=True,
            require_phone_ready=True,
        )
        self.assertIsNone(stale_claim)

        dynamic_claim = self.repository.claim_next(
            task_id="TSK-MKT-NOW-SHOW",
            phone_id="PHN-MKT",
            account_type="",
            allow_overdue=True,
            require_phone_ready=True,
        )
        self.assertIsNotNone(dynamic_claim)
        assert dynamic_claim is not None
        self.assertEqual(dynamic_claim.task["phone_account_type"], "showcase")

    def test_claim_next_blocks_second_task_when_phone_has_running_task(self) -> None:
        now = datetime(2026, 5, 4, 10, 0, tzinfo=timezone.utc).replace(microsecond=0).isoformat()
        with session(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO release_tasks(
                    task_id, video_id, phone_id, scheduled_at, publish_mode, status, created_at, updated_at
                )
                VALUES('TSK-MKT-CLAIM-2', 'VID-1', 'PHN-MKT', '2026-05-04T10:31:00+00:00', 'scheduled', 'pending', ?, ?)
                """,
                (now, now),
            )
            connection.execute(
                "UPDATE release_tasks SET status = 'running' WHERE task_id = 'TSK-MKT-CLAIM'"
            )
            connection.execute(
                "UPDATE phones SET current_status = 'online_idle' WHERE phone_id = 'PHN-MKT'"
            )

        claim = self.repository.claim_next(
            task_id="TSK-MKT-CLAIM-2",
            phone_id="PHN-MKT",
            worker_id="WRK-SECOND",
            allow_overdue=True,
            require_phone_ready=True,
        )

        self.assertIsNone(claim)
        with session(self.db_path) as connection:
            second = connection.execute(
                "SELECT status FROM release_tasks WHERE task_id = 'TSK-MKT-CLAIM-2'"
            ).fetchone()
        self.assertEqual(second["status"], "pending")

    def test_recover_stale_worker_requeues_locked_task(self) -> None:
        claim = self.repository.claim_next(
            phone_id="PHN-MKT",
            worker_id="WRK-STALE",
            allow_overdue=True,
            require_phone_ready=True,
        )
        self.assertIsNotNone(claim)
        now = datetime(2026, 5, 4, 10, 0, tzinfo=timezone.utc).replace(microsecond=0).isoformat()
        with session(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO execution_workers(
                    worker_id, phone_id, adb_serial, status, pid, started_at,
                    heartbeat_at, lease_expires_at, updated_at
                )
                VALUES('WRK-STALE', 'PHN-MKT', 'SERIAL-MKT', 'running', 1, ?, ?, '2000-01-01T00:00:00+00:00', ?)
                """,
                (now, now, now),
            )
        recovered = WorkerStateRepository(self.db_path).recover_stale(reason="test stale recovery")
        self.assertEqual(recovered[0]["worker_id"], "WRK-STALE")
        with session(self.db_path) as connection:
            task = connection.execute("SELECT status, locked_by_worker, lock_expires_at FROM release_tasks WHERE task_id = ?", (claim.task_id,)).fetchone()
            phone = connection.execute("SELECT current_status FROM phones WHERE phone_id = ?", ("PHN-MKT",)).fetchone()
            worker = connection.execute("SELECT status FROM execution_workers WHERE worker_id = 'WRK-STALE'").fetchone()
        self.assertEqual(task["status"], "pending")
        self.assertEqual(task["locked_by_worker"], "")
        self.assertIsNone(task["lock_expires_at"])
        self.assertEqual(phone["current_status"], "online_idle")
        self.assertEqual(worker["status"], "stale")

    def test_worker_register_rejects_active_worker_for_same_phone(self) -> None:
        repository = WorkerStateRepository(self.db_path)
        repository.register(
            worker_id="WRK-ACTIVE-1",
            phone_id="PHN-MKT",
            adb_serial="SERIAL-MKT",
            pid=1,
            lease_seconds=120,
        )

        with self.assertRaisesRegex(RuntimeError, "active worker already exists"):
            repository.register(
                worker_id="WRK-ACTIVE-2",
                phone_id="PHN-MKT",
                adb_serial="SERIAL-MKT",
                pid=2,
                lease_seconds=120,
            )


class ExecutionQueueDebugModeTests(unittest.TestCase):
    def setUp(self) -> None:
        temp_root = Path(".test_tmp")
        temp_root.mkdir(exist_ok=True)
        safe_name = self.id().replace(".", "_")
        self.db_path = temp_root / f"{safe_name}.db"
        remove_sqlite_files(self.db_path)
        init_database(self.db_path)

    def tearDown(self) -> None:
        remove_sqlite_files(self.db_path)

    def test_run_once_requires_publish_authorization_unless_stop_before_final_publish(self) -> None:
        service = ExecutionQueueService(self.db_path)

        with self.assertRaisesRegex(RuntimeError, "Real execution requires --allow-publish"):
            service.run_once(dry_run=False, pipeline_dry_run=False, allow_publish=False, require_phone_ready=False)

        outcome = service.run_once(
            dry_run=False,
            pipeline_dry_run=False,
            allow_publish=False,
            stop_before_final_publish=True,
            require_phone_ready=False,
        )

        self.assertEqual(outcome["status"], "no_task")

    def test_validate_task_payload_rejects_bad_showcase_task_before_device_run(self) -> None:
        claim = pipeline_claim(
            phone_account_type="showcase",
            video_file_path=str(Path(".test_tmp") / "missing-video.mp4"),
            caption_content="",
            product_search_title="",
            product_publish_name="",
            publish_mode="scheduled",
            scheduled_at="not-a-date",
        )

        error = validate_task_payload(claim)

        self.assertIn("task validation failed", error)
        self.assertIn("video file does not exist", error)
        self.assertIn("caption is empty", error)
        self.assertIn("scheduled_at is not valid ISO datetime", error)
        self.assertIn("showcase product match title is empty", error)

    def test_failure_retryability_classifies_business_data_errors(self) -> None:
        self.assertFalse(failure_is_retryable("ensure_showcase_product_attached: target product was not found in TikTok Shop"))
        self.assertFalse(failure_is_retryable("task validation failed: caption is empty"))
        self.assertTrue(failure_is_retryable("phone preflight failed: app_detected=False"))


class WorkerSupervisorTests(unittest.TestCase):
    def setUp(self) -> None:
        temp_root = Path(".test_tmp")
        temp_root.mkdir(exist_ok=True)
        safe_name = self.id().replace(".", "_")
        self.db_path = temp_root / f"{safe_name}.db"
        remove_sqlite_files(self.db_path)
        init_database(self.db_path)
        seed_database(self.db_path)

    def tearDown(self) -> None:
        remove_sqlite_files(self.db_path)

    def test_dry_run_reports_missing_marketing_worker_without_launching(self) -> None:
        supervisor = WorkerSupervisor(
            SupervisorOptions(account_type="marketing", dry_run=True, allow_publish=True),
            self.db_path,
        )

        outcome = supervisor.run_once()

        missing = outcome["missing_worker_phones"]
        self.assertEqual([phone["phone_id"] for phone in missing], ["PHN-MKT"])
        launched = outcome["launched_workers"]
        self.assertEqual(len(launched), 1)
        self.assertEqual(launched[0]["pid"], "")
        self.assertIn("--allow-publish", " ".join(launched[0]["command"]))

    def test_pause_overdue_scheduled_tasks_moves_pending_to_paused(self) -> None:
        now = datetime(2026, 5, 4, 10, 0, tzinfo=timezone.utc).replace(microsecond=0).isoformat()
        with session(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO release_tasks(
                    task_id, video_id, phone_id, scheduled_at, publish_mode, status, created_at, updated_at
                )
                VALUES('TSK-OVERDUE', 'VID-1', 'PHN-MKT', '2000-01-01T00:00:00+00:00', 'scheduled', 'pending', ?, ?)
                """,
                (now, now),
            )

        supervisor = WorkerSupervisor(
            SupervisorOptions(account_type="marketing", launch_workers=False, overdue_action="pause"),
            self.db_path,
        )
        outcome = supervisor.run_once()

        self.assertEqual([task["task_id"] for task in outcome["paused_tasks"]], ["TSK-OVERDUE"])
        with session(self.db_path) as connection:
            task = connection.execute(
                "SELECT status, failure_reason, locked_by_worker, lock_expires_at FROM release_tasks WHERE task_id = 'TSK-OVERDUE'"
            ).fetchone()
        self.assertEqual(task["status"], "paused")
        self.assertIn("预约时间已过", task["failure_reason"])
        self.assertEqual(task["locked_by_worker"], "")
        self.assertIsNone(task["lock_expires_at"])

    def test_reschedule_overdue_scheduled_tasks_keeps_task_pending(self) -> None:
        now = datetime(2026, 5, 4, 10, 0, tzinfo=timezone.utc).replace(microsecond=0).isoformat()
        with session(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO release_tasks(
                    task_id, video_id, phone_id, scheduled_at, publish_mode, status, created_at, updated_at
                )
                VALUES('TSK-OVERDUE-RESCHEDULE', 'VID-1', 'PHN-MKT', '2000-01-01T00:00:00+00:00', 'scheduled', 'pending', ?, ?)
                """,
                (now, now),
            )

        supervisor = WorkerSupervisor(
            SupervisorOptions(
                account_type="marketing",
                launch_workers=False,
                overdue_action="reschedule",
                overdue_reschedule_minutes=30,
            ),
            self.db_path,
        )
        outcome = supervisor.run_once()

        self.assertEqual([task["task_id"] for task in outcome["rescheduled_tasks"]], ["TSK-OVERDUE-RESCHEDULE"])
        with session(self.db_path) as connection:
            task = connection.execute(
                "SELECT status, scheduled_at, failure_reason, locked_by_worker, lock_expires_at FROM release_tasks WHERE task_id = 'TSK-OVERDUE-RESCHEDULE'"
            ).fetchone()
        self.assertEqual(task["status"], "pending")
        self.assertGreater(datetime.fromisoformat(task["scheduled_at"]), datetime.now(timezone.utc))
        self.assertIn("自动顺延", task["failure_reason"])
        self.assertEqual(task["locked_by_worker"], "")
        self.assertIsNone(task["lock_expires_at"])

    def test_all_account_type_selects_showcase_and_marketing_phones(self) -> None:
        supervisor = WorkerSupervisor(
            SupervisorOptions(account_type="all", dry_run=True, launch_workers=False),
            self.db_path,
        )

        phone_ids = {phone["phone_id"] for phone in supervisor.select_target_phones()}

        self.assertIn("PHN-MKT", phone_ids)
        self.assertIn("PHN-SHOW", phone_ids)

    def test_supervisor_recovers_active_worker_when_pid_is_dead(self) -> None:
        now = datetime(2026, 5, 4, 10, 0, tzinfo=timezone.utc).replace(microsecond=0).isoformat()
        with session(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO execution_workers(
                    worker_id, phone_id, adb_serial, status, pid, started_at,
                    heartbeat_at, lease_expires_at, updated_at
                )
                VALUES('WRK-DEAD', 'PHN-MKT', 'SERIAL-MKT', 'idle', 99999999, ?, ?, '2099-01-01T00:00:00+00:00', ?)
                """,
                (now, now, now),
            )

        supervisor = WorkerSupervisor(
            SupervisorOptions(account_type="marketing", launch_workers=False),
            self.db_path,
        )
        outcome = supervisor.run_once()

        self.assertEqual([worker["worker_id"] for worker in outcome["recovered_dead_workers"]], ["WRK-DEAD"])
        with session(self.db_path) as connection:
            worker = connection.execute("SELECT status, last_error FROM execution_workers WHERE worker_id = 'WRK-DEAD'").fetchone()
        self.assertEqual(worker["status"], "stale")
        self.assertIn("dead worker", worker["last_error"])


class PipelineAdapterConstraintTests(unittest.TestCase):
    def test_prepare_plan_rejects_invalid_claim(self) -> None:
        claim = ExecutionClaim(
            task_id="TSK-INVALID",
            attempt_id="ATT-INVALID",
            log_id="LOG-INVALID",
            phone_id="PHN-MKT",
            attempt_no=1,
            task=base_pipeline_task(phone_account_type="marketing", product_name="Product"),
        )
        with self.assertRaisesRegex(ValueError, "marketing task cannot contain product fields"):
            UploadPipelineAdapter().prepare_plan(
                claim,
                allow_publish=False,
                pipeline_dry_run=True,
                schedule_timezone="Asia/Shanghai",
            )

    def test_showcase_command_contains_showcase_product_args(self) -> None:
        command = UploadPipelineAdapter().build_command(
            base_pipeline_task(
                phone_account_type="showcase",
                publish_mode="immediate",
                product_search_title="Product",
                product_publish_name="Display Product",
            ),
            config_path=Path("config.json"),
            state_path=Path("state.json"),
            allow_publish=True,
            pipeline_dry_run=False,
            schedule_timezone="Asia/Shanghai",
        )
        self.assertIn("workflow_database/scripts/run_showcase_workflow.py", command)
        self.assertIn("--profile", command)
        self.assertIn("--product-search-title", command)
        self.assertEqual(command[command.index("--product-search-title") + 1], "Product")
        self.assertIn("--product-publish-name", command)
        self.assertEqual(command[command.index("--product-publish-name") + 1], "Display Product")
        self.assertIn("--state", command)
        self.assertIn("--run-dir", command)
        self.assertIn("--publish-mode", command)
        self.assertEqual(command[command.index("--publish-mode") + 1], "immediate")
        self.assertIn("--schedule-timezone", command)
        self.assertEqual(command[command.index("--schedule-timezone") + 1], "Asia/Shanghai")
        self.assertNotIn("--scheduled-at", command)

    def test_showcase_scheduled_command_contains_schedule_args(self) -> None:
        command = UploadPipelineAdapter().build_command(
            base_pipeline_task(
                phone_account_type="showcase",
                publish_mode="scheduled",
                product_search_title="Product",
                product_publish_name="Display Product",
                scheduled_at="2026-05-04T10:30:00+08:00",
            ),
            config_path=Path("config.json"),
            state_path=Path("state.json"),
            allow_publish=True,
            pipeline_dry_run=False,
            schedule_timezone="Asia/Shanghai",
        )
        self.assertIn("--publish-mode", command)
        self.assertEqual(command[command.index("--publish-mode") + 1], "scheduled")
        self.assertIn("--scheduled-at", command)
        self.assertEqual(command[command.index("--scheduled-at") + 1], "2026-05-04T10:30:00+08:00")
        self.assertIn("--schedule-timezone", command)
        self.assertEqual(command[command.index("--schedule-timezone") + 1], "Asia/Shanghai")

    def test_showcase_stop_before_final_publish_command_does_not_require_allow_publish(self) -> None:
        task = base_pipeline_task(
            phone_account_type="showcase",
            publish_mode="scheduled",
            product_search_title="Product",
            product_publish_name="Display Product",
            scheduled_at="2026-05-04T10:30:00+08:00",
        )
        command = UploadPipelineAdapter().build_command(
            task,
            config_path=Path("config.json"),
            state_path=Path("state.json"),
            allow_publish=False,
            pipeline_dry_run=False,
            stop_before_final_publish=True,
            schedule_timezone="Asia/Shanghai",
        )
        state = UploadPipelineAdapter().build_state(
            ExecutionClaim(
                task_id="TSK-SHOW",
                attempt_id="ATT-SHOW",
                log_id="LOG-SHOW",
                phone_id="PHN-SHOW",
                attempt_no=1,
                task=task,
            ),
            config_path=Path("config.json"),
            state_path=Path("state.json"),
            attempt_dir=Path(".test_tmp"),
            allow_publish=False,
            pipeline_dry_run=False,
            stop_before_final_publish=True,
            schedule_timezone="Asia/Shanghai",
        )

        self.assertIn("--stop-before-final-publish", command)
        self.assertNotIn("--allow-publish", command)
        self.assertTrue(state["stop_before_final_publish"])
        self.assertFalse(state["allow_publish"])

    def test_showcase_product_search_title_is_truncated_to_normalized_25_prefix(self) -> None:
        task = base_pipeline_task(
            phone_account_type="showcase",
            publish_mode="immediate",
            product_search_title="Upgraded High-Quality Wireless Charger",
            product_publish_name="Display Product",
        )
        command = UploadPipelineAdapter().build_command(
            task,
            config_path=Path("config.json"),
            state_path=Path("state.json"),
            allow_publish=True,
            pipeline_dry_run=False,
            schedule_timezone="Asia/Shanghai",
        )
        state = UploadPipelineAdapter().build_state(
            ExecutionClaim(
                task_id="TSK-SHOW",
                attempt_id="ATT-SHOW",
                log_id="LOG-SHOW",
                phone_id="PHN-SHOW",
                attempt_no=1,
                task=task,
            ),
            config_path=Path("config.json"),
            state_path=Path("state.json"),
            attempt_dir=Path(".test_tmp"),
            allow_publish=True,
            pipeline_dry_run=False,
            schedule_timezone="Asia/Shanghai",
        )

        expected = "Upgraded High-Quality Wirele"
        self.assertEqual(command[command.index("--product-search-title") + 1], expected)
        self.assertEqual(state["product_search_title"], expected)
        self.assertEqual(len(normalize_product_match_text(expected)), 25)

    def test_marketing_command_uses_workflow_database_runner(self) -> None:
        command = UploadPipelineAdapter().build_command(
            base_pipeline_task(phone_account_type="marketing", publish_mode="scheduled"),
            config_path=Path("config.json"),
            state_path=Path("state.json"),
            allow_publish=False,
            pipeline_dry_run=True,
            schedule_timezone="Asia/Shanghai",
        )
        self.assertIn("workflow_database/scripts/run_marketing_workflow.py", command)
        self.assertIn("--profile", command)
        self.assertEqual(command[command.index("--publish-mode") + 1], "scheduled")
        self.assertIn("--force-stop-before-open", command)
        self.assertNotIn("--product-link", command)
        self.assertNotIn("--product-name", command)


class ShowcaseMediaPickerXmlTests(unittest.TestCase):
    def test_showcase_profiles_share_required_workflow_template(self) -> None:
        root = Path(__file__).resolve().parents[1]
        profile_paths = sorted((root / "workflow_database" / "devices").glob("*/showcase_tiktok_app*.json"))
        expected_ids = [
            "open_tiktok",
            "tap_plus",
            "open_gallery_from_camera",
            "ensure_video_picker",
            "tap_video_tab",
            "assert_single_media_picker_video",
            "select_first_video",
            "optional_remove_added_music",
            "tap_next_until_publish_preset",
            "paste_caption",
            "tap_add_link",
            "wait_add_link_panel",
            "tap_add_product",
            "wait_product_search_screen",
            "tap_product_search",
            "wait_product_search_field_ready",
            "input_product_search_title",
            "confirm_product_search_editor_code",
            "ensure_showcase_product_attached",
            "optional_confirm_add_product",
            "wait_product_publish_name_screen",
            "input_product_publish_name",
            "tap_product_final_add",
            "optional_confirm_sponsored_content",
            "optional_close_music_warning",
            "wait_return_to_publish_preset",
            "assert_product_link_attached",
            "open_more_settings",
            "open_schedule_publish",
            "wait_schedule_picker_open",
            "set_schedule_datetime",
            "verify_schedule_configured",
            "close_more_settings",
            "tap_scheduled_publish",
            "tap_publish",
            "minimize_tiktok_app",
        ]

        for profile_path in profile_paths:
            with self.subTest(profile=profile_path):
                profile = json.loads(profile_path.read_text(encoding="utf-8"))
                workflow_ids = [str(step.get("id") or "") for step in profile.get("workflow", [])]
                self.assertEqual(workflow_ids, expected_ids)

    def test_showcase_add_more_products_uses_sticky_bottom_button_point(self) -> None:
        root = Path(__file__).resolve().parents[1]
        profile_paths = sorted((root / "workflow_database" / "devices").glob("*/showcase_tiktok_app*.json"))

        for profile_path in profile_paths:
            with self.subTest(profile=profile_path):
                profile = json.loads(profile_path.read_text(encoding="utf-8"))
                point = profile["coordinate_points"]["add_more_products_entry"]
                self.assertGreaterEqual(float(point["y_ratio"]), 0.93)
                step = next(step for step in profile["workflow"] if step.get("id") == "ensure_showcase_product_attached")
                self.assertGreaterEqual(float(step["add_more_products_point_ratio"]["y_ratio"]), 0.93)

    def test_showcase_media_picker_accepts_existing_gallery_videos(self) -> None:
        root = Path(__file__).resolve().parents[1]
        profile_paths = sorted((root / "workflow_database" / "devices").glob("*/showcase_tiktok_app*.json"))

        for profile_path in profile_paths:
            with self.subTest(profile=profile_path):
                profile = json.loads(profile_path.read_text(encoding="utf-8"))
                step = next(step for step in profile["workflow"] if step.get("id") == "assert_single_media_picker_video")
                self.assertEqual(step["expected_count"], 1)
                self.assertEqual(step["count_mode"], "at_least")

    def test_media_picker_count_modes(self) -> None:
        self.assertTrue(media_picker_count_matches(1, 1, "exact"))
        self.assertFalse(media_picker_count_matches(9, 1, "exact"))
        self.assertTrue(media_picker_count_matches(9, 1, "at_least"))
        self.assertFalse(media_picker_count_matches(0, 1, "at_least"))

    def test_showcase_tiktok_shop_entry_targets_bottom_sheet_row(self) -> None:
        root = Path(__file__).resolve().parents[1]
        profile_paths = sorted((root / "workflow_database" / "devices").glob("*/showcase_tiktok_app*.json"))

        for profile_path in profile_paths:
            with self.subTest(profile=profile_path):
                profile = json.loads(profile_path.read_text(encoding="utf-8"))
                point = profile["coordinate_points"]["tiktok_shop_tab"]
                self.assertLessEqual(float(point["x_ratio"]), 0.35)
                self.assertGreaterEqual(float(point["y_ratio"]), 0.93)
                step = next(step for step in profile["workflow"] if step.get("id") == "ensure_showcase_product_attached")
                self.assertLessEqual(float(step["tiktok_shop_tab_point_ratio"]["x_ratio"]), 0.35)
                self.assertGreaterEqual(float(step["tiktok_shop_tab_point_ratio"]["y_ratio"]), 0.93)

    def test_count_media_picker_items_counts_visible_grid_children(self) -> None:
        xml = """
        <hierarchy>
          <node class="android.widget.GridView" resource-id="com.zhiliaoapp.musically:id/ir_" bounds="[0,331][1080,2074]">
            <node class="android.widget.FrameLayout" clickable="true" long-clickable="true" bounds="[5,336][359,693]" />
            <node class="android.widget.FrameLayout" clickable="true" long-clickable="true" bounds="[364,336][717,693]" />
            <node class="android.widget.FrameLayout" clickable="true" long-clickable="true" bounds="[722,336][1075,693]" />
          </node>
        </hierarchy>
        """
        self.assertEqual(count_media_picker_items(xml), 3)

    def test_count_media_picker_items_supports_a52_current_grid_id(self) -> None:
        xml = """
        <hierarchy>
          <node class="android.widget.GridView" resource-id="com.zhiliaoapp.musically:id/ivd" bounds="[0,331][1080,2074]">
            <node class="android.widget.FrameLayout" clickable="true" long-clickable="true" bounds="[5,336][359,693]">
              <node class="android.widget.ImageView" resource-id="com.zhiliaoapp.musically:id/nq3" bounds="[5,336][359,693]" />
              <node class="android.widget.TextView" text="00:15" bounds="[239,633][359,693]" />
            </node>
          </node>
        </hierarchy>
        """
        self.assertEqual(count_media_picker_items(xml), 1)

    def test_count_media_picker_items_supports_duration_based_grid_fallback(self) -> None:
        xml = """
        <hierarchy>
          <node class="android.widget.GridView" resource-id="com.zhiliaoapp.musically:id/new_media_grid" bounds="[0,331][1080,2074]">
            <node class="android.widget.FrameLayout" clickable="true" bounds="[5,336][359,693]">
              <node class="android.widget.TextView" text="1:23" bounds="[239,633][359,693]" />
            </node>
          </node>
        </hierarchy>
        """
        self.assertEqual(count_media_picker_items(xml), 1)

    def test_count_media_picker_items_from_screenshot_uses_duration_ocr(self) -> None:
        with mock.patch(
            "workflow_database.scripts.run_showcase_workflow.detect_product_ocr_candidates",
            return_value=[
                SimpleNamespace(text="00:18"),
                SimpleNamespace(text="视频"),
                SimpleNamespace(text="1:02:03"),
            ],
        ):
            self.assertEqual(
                count_media_picker_items_from_screenshot(
                    screenshot_path=ROOT / "media_picker.png",
                    step={},
                    step_id="assert_single_media_picker_video",
                ),
                2,
            )

    def test_first_matching_node_center_uses_product_add_button_bounds(self) -> None:
        xml = """
        <hierarchy>
          <node class="android.view.ViewGroup" content-desc="button_add_product" bounds="[744,556][1038,640]" />
          <node class="android.view.ViewGroup" content-desc="button_add_product" bounds="[744,1026][1038,1110]" />
        </hierarchy>
        """
        match = first_matching_node_center(
            xml,
            {
                "content_desc": "button_add_product",
                "class": "android.view.ViewGroup",
                "match_mode": "exact",
            },
        )
        self.assertEqual(match["x"], 891)
        self.assertEqual(match["y"], 598)

    def test_xml_has_package_detects_foreground_package(self) -> None:
        xml = '<hierarchy><node package="com.zhiliaoapp.musically" /></hierarchy>'
        self.assertTrue(xml_has_package(xml, "com.zhiliaoapp.musically"))


def base_task(**overrides: object) -> dict[str, object]:
    task: dict[str, object] = {
        "phone_status": "online_idle",
        "retry_count": 0,
        "max_retries": 3,
        "phone_account_type": "marketing",
        "publish_mode": "scheduled",
        "scheduled_at": "2026-05-04T10:30:00+00:00",
        "product_id": "",
        "product_link": "",
        "product_name": "",
        "product_search_title": "",
        "product_publish_name": "",
    }
    task.update(overrides)
    return task


def base_pipeline_task(**overrides: object) -> dict[str, object]:
    task: dict[str, object] = {
        "video_file_path": "video.mp4",
        "caption_content": "Caption",
        "phone_id": "PHN-PIPELINE",
        "phone_adb_serial": "SERIAL-PIPELINE",
        "phone_account_type": "marketing",
        "publish_mode": "scheduled",
        "scheduled_at": "2026-05-04T10:30:00+08:00",
        "product_id": "",
        "product_link": "",
        "product_name": "",
        "product_search_title": "",
        "product_publish_name": "",
    }
    task.update(overrides)
    return task


def pipeline_claim(**task_overrides: object) -> ExecutionClaim:
    return ExecutionClaim(
        task_id="TSK-VALIDATION",
        attempt_id="ATT-VALIDATION",
        log_id="LOG-VALIDATION",
        phone_id="PHN-SHOW",
        attempt_no=1,
        task=base_pipeline_task(**task_overrides),
    )


def pipeline_plan_with_state(state: dict[str, object]) -> PipelineRunPlan:
    root = Path(".test_tmp") / "pipeline_success_validation"
    root.mkdir(parents=True, exist_ok=True)
    state_path = root / "state.json"
    state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return PipelineRunPlan(
        command=[],
        cwd=root,
        attempt_dir=root,
        config_path=root / "config.json",
        state_path=state_path,
        stdout_path=root / "stdout.log",
        stderr_path=root / "stderr.log",
        manifest_path=root / "manifest.json",
    )


def seed_database(db_path: Path) -> None:
    now = datetime(2026, 5, 4, 10, 0, tzinfo=timezone.utc).replace(microsecond=0).isoformat()
    with session(db_path) as connection:
        connection.execute(
            """
            INSERT INTO videos(video_id, title, file_path, file_name, file_ext, file_size, sha256, created_at, imported_at, updated_at)
            VALUES('VID-1', 'Video 1', 'video-1.mp4', 'video-1.mp4', '.mp4', 100, 'sha-1', ?, ?, ?)
            """,
            (now, now, now),
        )
        connection.execute(
            """
            INSERT INTO phones(phone_id, device_name, adb_serial, account_type, current_status, created_at, updated_at)
            VALUES('PHN-MKT', 'Marketing Phone', 'SERIAL-MKT', 'marketing', 'online_idle', ?, ?)
            """,
            (now, now),
        )
        connection.execute(
            """
            INSERT INTO phones(phone_id, device_name, adb_serial, account_type, current_status, created_at, updated_at)
            VALUES('PHN-SHOW', 'Showcase Phone', 'SERIAL-SHOW', 'showcase', 'online_idle', ?, ?)
            """,
            (now, now),
        )


def insert_invalid_showcase_scheduled_task(db_path: Path) -> None:
    now = datetime(2026, 5, 4, 10, 0, tzinfo=timezone.utc).replace(microsecond=0).isoformat()
    with session(db_path) as connection:
        connection.execute(
            """
            INSERT INTO release_tasks(
                task_id, video_id, phone_id, scheduled_at, publish_mode,
                product_name, product_search_title, status, created_at, updated_at
            )
            VALUES(
                'TSK-BAD-SHOW', 'VID-1', 'PHN-SHOW', '2026-05-04T10:30:00+00:00',
                'scheduled', 'Product', 'Product', 'pending', ?, ?
            )
            """,
            (now, now),
        )


def insert_claim_filter_tasks(db_path: Path) -> None:
    now = datetime(2026, 5, 4, 10, 0, tzinfo=timezone.utc).replace(microsecond=0).isoformat()
    with session(db_path) as connection:
        connection.execute(
            """
            INSERT INTO release_tasks(
                task_id, video_id, phone_id, scheduled_at, publish_mode, status, created_at, updated_at
            )
            VALUES('TSK-MKT-CLAIM', 'VID-1', 'PHN-MKT', '2026-05-04T10:30:00+00:00', 'scheduled', 'pending', ?, ?)
            """,
            (now, now),
        )
        connection.execute(
            """
            INSERT INTO release_tasks(
                task_id, video_id, phone_id, scheduled_at, publish_mode,
                product_name, product_search_title, status, created_at, updated_at
            )
            VALUES(
                'TSK-SHOW-CLAIM', 'VID-1', 'PHN-SHOW', '2026-05-04T09:30:00+00:00',
                'immediate', 'Product', 'Product', 'pending', ?, ?
            )
            """,
            (now, now),
        )


def remove_sqlite_files(db_path: Path) -> None:
    for path in (db_path, db_path.with_name(db_path.name + "-wal"), db_path.with_name(db_path.name + "-shm")):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    unittest.main()
