from __future__ import annotations

import sys
import unittest
from unittest import mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "upload_system"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from upload_system.ai import TextCandidate
from workflow_database.scripts import run_showcase_workflow as showcase_workflow
from workflow_database.scripts.run_showcase_workflow import (
    DEFAULT_PRODUCT_ADD_TEXTS,
    DEFAULT_PRODUCT_ALREADY_ADDED_TEXTS,
    DEFAULT_PRODUCT_MATCH_PREFIX_CHARS,
    extract_product_cards,
    filter_steps_for_publish_mode,
    first_text_match_center,
    matching_product_cards,
    normalize_product_match_text,
    product_bottom_reached,
    product_match_key,
    product_match_prefix_chars,
    scrollable_product_list_bounds,
)


FIXTURE_XML = (
    ROOT
    / "run_log"
    / "executions"
    / "TSK-F55EE429EB83"
    / "01_ATT-989217FC6531"
    / "xml"
    / "tap_first_product_add.xml"
)


class ShowcaseProductMatchingTests(unittest.TestCase):
    def test_normalize_product_text_removes_layout_noise(self) -> None:
        self.assertEqual(
            normalize_product_match_text("Upgraded High-Quality Wireless Headp..."),
            "upgradedhighqualitywirelessheadp",
        )
        self.assertEqual(normalize_product_match_text("  ＡＢＣ 123 - 促销! "), "abc123促销")

    def test_product_match_key_uses_first_25_normalized_chars(self) -> None:
        key = product_match_key(
            "Upgraded High-Quality Wireless Headphones",
            match_prefix_chars=DEFAULT_PRODUCT_MATCH_PREFIX_CHARS,
        )

        self.assertEqual(key, "upgradedhighqualitywirele")
        self.assertEqual(product_match_prefix_chars({"match_prefix_chars": 30}), 25)

    def test_product_match_is_latin_accent_insensitive_for_ocr_titles(self) -> None:
        self.assertEqual(
            normalize_product_match_text("【B】 Óleo Capilar Proteção Térmica Sem Enxágue"),
            "boleocapilarprotecaotermicasemenxague",
        )
        card = {
            "normalized_title": normalize_product_match_text("[B] Oleo Capilar de Argan Natural Lua&Nev..."),
        }
        target_key = product_match_key(
            "【B】 Óleo Capilar de Argan Natura",
            match_prefix_chars=DEFAULT_PRODUCT_MATCH_PREFIX_CHARS,
        )

        self.assertTrue(showcase_workflow.product_card_matches(card, target_key))

    def test_product_list_swipe_uses_safe_left_edge_x(self) -> None:
        class FakeADB:
            def __init__(self) -> None:
                self.swipes: list[tuple[int, int, int, int, int]] = []

            def swipe(self, start_x: int, start_y: int, end_x: int, end_y: int, duration_ms: int) -> None:
                self.swipes.append((start_x, start_y, end_x, end_y, duration_ms))

        xml_text = """
        <hierarchy>
          <node class="androidx.recyclerview.widget.RecyclerView" scrollable="true" bounds="[0,379][1080,2263]" />
        </hierarchy>
        """
        adb = FakeADB()

        showcase_workflow.swipe_product_list(
            adb=adb,
            xml_text=xml_text,
            screenshot_path="",
            step={"swipe_start_ratio": 0.82, "swipe_end_ratio": 0.30, "swipe_duration_ms": 500},
        )

        self.assertEqual(adb.swipes, [(43, 1923, 43, 944, 500)])

    def test_product_detail_page_visible_detects_detail_page_not_list(self) -> None:
        detail_xml = """
        <hierarchy>
          <node text="概述" />
          <node text="评论" />
          <node text="描述" />
          <node text="推荐" />
          <node text="加入购物车" />
          <node text="立即购买" />
        </hierarchy>
        """
        list_xml = """
        <hierarchy>
          <node text="添加商品链接" />
          <node text="添加更多商品" />
          <node content-desc="添加" />
        </hierarchy>
        """

        self.assertTrue(showcase_workflow.product_detail_page_visible(detail_xml))
        self.assertFalse(showcase_workflow.product_detail_page_visible(list_xml))

    def test_caption_paste_match_detects_written_caption_prefix(self) -> None:
        xml_text = """
        <hierarchy>
          <node class="android.widget.EditText" text="Espuma de Limpeza Facial Anti-Oleosidade com vitamina C" />
          <node text="Adicionar link" />
        </hierarchy>
        """

        result = showcase_workflow.caption_paste_match_details(
            xml_text,
            "Espuma de Limpeza Facial Anti-Oleosidade - veja o produto",
        )

        self.assertTrue(result["found"])
        self.assertEqual(result["reason"], "expected_prefix_in_xml_value")

    def test_caption_paste_match_rejects_placeholder_only_page(self) -> None:
        xml_text = """
        <hierarchy>
          <node class="android.widget.EditText" text="添加描述..." />
          <node text="Adicionar link" />
        </hierarchy>
        """

        result = showcase_workflow.caption_paste_match_details(
            xml_text,
            "Espuma de Limpeza Facial Anti-Oleosidade - veja o produto",
        )

        self.assertFalse(result["found"])
        self.assertEqual(result["reason"], "expected_prefix_not_found")

    def test_caption_paste_match_accepts_truncated_visible_caption(self) -> None:
        xml_text = """
        <hierarchy>
          <node class="android.widget.EditText" text="Espuma de Limpeza Facial" />
        </hierarchy>
        """

        result = showcase_workflow.caption_paste_match_details(
            xml_text,
            "Espuma de Limpeza Facial Anti-Oleosidade - veja o produto",
        )

        self.assertTrue(result["found"])
        self.assertEqual(result["reason"], "xml_value_matches_expected_start")

    def test_product_text_defaults_survive_garbled_profile_values(self) -> None:
        texts = showcase_workflow.product_texts_with_defaults(["??", "Add"], DEFAULT_PRODUCT_ADD_TEXTS)
        xml_text = """
        <hierarchy>
          <node class="android.view.ViewGroup" focusable="true" content-desc=" 添加" bounds="[776,689][1007,773]" />
        </hierarchy>
        """

        self.assertIn("添加", texts)
        self.assertEqual(showcase_workflow.product_action_button_count(xml_text, texts, []), 1)

    def test_extract_cards_matches_current_fixture_with_ocr_title(self) -> None:
        xml_text = FIXTURE_XML.read_text(encoding="utf-8")
        cards = extract_product_cards(
            xml_text=xml_text,
            ocr_candidates=[
                ocr("Upgraded High-Quality Wireless Headphones", 373, 545, 930, 585, 0.93),
                ocr("UK Fast Shipping | 44.4K in stock", 373, 610, 820, 650, 0.91),
            ],
            add_texts=DEFAULT_PRODUCT_ADD_TEXTS,
            already_added_texts=DEFAULT_PRODUCT_ALREADY_ADDED_TEXTS,
            min_ocr_confidence=0.85,
        )

        matches = matching_product_cards(
            cards,
            product_match_key("Upgraded High-Quality Wireless Headphones", match_prefix_chars=30),
        )

        self.assertGreaterEqual(len(cards), 2)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["add_button_bounds"], [761, 745, 1003, 833])

    def test_matching_uses_first_detected_line_only(self) -> None:
        xml_text = """
        <hierarchy bounds="[0,0][1080,2400]">
          <node class="androidx.recyclerview.widget.RecyclerView" scrollable="true" bounds="[0,80][1080,1000]">
            <node class="android.view.ViewGroup" bounds="[0,100][1080,440]">
              <node class="android.view.ViewGroup" focusable="true" content-desc=" Add" bounds="[760,280][1038,364]" />
            </node>
          </node>
        </hierarchy>
        """
        target = "Upgraded High-Quality Wireless Headphones"
        cards = extract_product_cards(
            xml_text=xml_text,
            ocr_candidates=[
                ocr("Store Header", 120, 130, 450, 170, 0.95),
                ocr(target, 120, 190, 720, 230, 0.96),
            ],
            add_texts=["Add"],
            already_added_texts=[],
            min_ocr_confidence=0.85,
        )

        matches = matching_product_cards(
            cards,
            product_match_key(target, match_prefix_chars=DEFAULT_PRODUCT_MATCH_PREFIX_CHARS),
        )

        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["title_text"], "Store Header")
        self.assertEqual(cards[0]["text_variants"], ["Store Header"])
        self.assertEqual(matches, [])

    def test_low_confidence_ocr_title_does_not_match(self) -> None:
        xml_text = FIXTURE_XML.read_text(encoding="utf-8")
        cards = extract_product_cards(
            xml_text=xml_text,
            ocr_candidates=[ocr("Upgraded High-Quality Wireless Headphones", 373, 545, 930, 585, 0.70)],
            add_texts=DEFAULT_PRODUCT_ADD_TEXTS,
            already_added_texts=DEFAULT_PRODUCT_ALREADY_ADDED_TEXTS,
            min_ocr_confidence=0.85,
        )

        matches = matching_product_cards(
            cards,
            product_match_key("Upgraded High-Quality Wireless Headphones", match_prefix_chars=30),
        )

        self.assertEqual(matches, [])

    def test_missing_paddleocr_falls_back_to_xml_only_scan(self) -> None:
        with mock.patch.object(
            showcase_workflow.PaddleOCRButtonFinder,
            "_detect_candidates",
            side_effect=RuntimeError("PaddleOCR is not installed; import error: No module named 'paddleocr'"),
        ):
            candidates = showcase_workflow.detect_product_ocr_candidates(
                screenshot_path=FIXTURE_XML,
                step={},
                step_id="ensure_showcase_product_attached_showcase_ocr_00",
            )

        self.assertEqual(candidates, [])

    def test_multiple_title_matches_are_visible_to_caller(self) -> None:
        xml_text = FIXTURE_XML.read_text(encoding="utf-8")
        title = "Upgraded High-Quality Wireless Headphones"
        cards = extract_product_cards(
            xml_text=xml_text,
            ocr_candidates=[
                ocr(title, 373, 545, 930, 585, 0.93),
                ocr(title, 373, 910, 930, 950, 0.94),
            ],
            add_texts=DEFAULT_PRODUCT_ADD_TEXTS,
            already_added_texts=DEFAULT_PRODUCT_ALREADY_ADDED_TEXTS,
            min_ocr_confidence=0.85,
        )

        matches = matching_product_cards(cards, product_match_key(title, match_prefix_chars=30))

        self.assertEqual(len(matches), 2)

    def test_extract_cards_dedupes_nested_add_nodes_on_same_card(self) -> None:
        xml_text = """
        <hierarchy bounds="[0,0][1080,2186]">
          <node class="android.view.ViewGroup" bounds="[42,489][1080,815]">
            <node class="android.widget.Button" content-desc=" 添加" bounds="[744,710][1038,794]" />
            <node class="android.view.ViewGroup" focusable="true" content-desc=" 添加" bounds="[776,710][1007,794]" />
          </node>
        </hierarchy>
        """

        cards = extract_product_cards(
            xml_text=xml_text,
            ocr_candidates=[ocr("Kit Facial Dia e Noite Lua&Neve: Limpeza ..", 356, 520, 900, 565, 0.96)],
            add_texts=DEFAULT_PRODUCT_ADD_TEXTS,
            already_added_texts=DEFAULT_PRODUCT_ALREADY_ADDED_TEXTS,
            min_ocr_confidence=0.85,
        )

        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["add_button_bounds"], [744, 710, 1038, 794])

    def test_bottom_detection_accepts_xml_and_ocr_markers(self) -> None:
        xml_text = '<hierarchy><node text="No more results" content-desc="" bounds="[0,0][1,1]" /></hierarchy>'

        self.assertTrue(
            product_bottom_reached(
                xml_text=xml_text,
                ocr_candidates=[],
                bottom_texts=["No more results"],
                min_ocr_confidence=0.85,
            )
        )
        self.assertTrue(
            product_bottom_reached(
                xml_text="<hierarchy />",
                ocr_candidates=[ocr("没有更多", 10, 10, 100, 40, 0.95)],
                bottom_texts=["没有更多"],
                min_ocr_confidence=0.85,
            )
        )

    def test_scrollable_product_list_bounds_reads_recycler_view(self) -> None:
        xml_text = FIXTURE_XML.read_text(encoding="utf-8")

        self.assertEqual(scrollable_product_list_bounds(xml_text), (0, 380, 1080, 2356))

    def test_text_match_clicks_clickable_parent_row(self) -> None:
        xml_text = """
        <hierarchy bounds="[0,0][1080,2186]">
          <node class="android.widget.Button" clickable="true" content-desc="more options" bounds="[0,1441][1080,1581]">
            <node class="android.widget.TextView" text="more options" bounds="[116,1486][264,1536]" />
          </node>
        </hierarchy>
        """

        match = first_text_match_center(xml_text, ["more options"], match_mode="contains")

        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match["bounds"], [0, 1441, 1080, 1581])
        self.assertEqual(match["matched_node_bounds"], [0, 1441, 1080, 1581])
        self.assertFalse(match["click_target_from_ancestor"])

    def test_text_match_promotes_non_clickable_text_to_parent_row(self) -> None:
        xml_text = """
        <hierarchy bounds="[0,0][1080,2186]">
          <node class="android.widget.Button" clickable="true" bounds="[0,1441][1080,1581]">
            <node class="android.widget.TextView" text="more options" bounds="[116,1486][264,1536]" />
          </node>
        </hierarchy>
        """

        match = first_text_match_center(xml_text, ["more options"], match_mode="contains")

        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match["bounds"], [0, 1441, 1080, 1581])
        self.assertEqual(match["matched_node_bounds"], [116, 1486, 264, 1536])
        self.assertTrue(match["click_target_from_ancestor"])

    def test_showcase_product_search_steps_are_skipped(self) -> None:
        steps = [
            {"id": "tap_add_product", "type": "tap_text_or_point"},
            {"id": "tap_product_search", "type": "tap_text_or_point"},
            {"id": "input_product_search_title", "type": "paste_text"},
            {"id": "confirm_product_search_editor_code", "type": "adb_editor_code"},
            {"id": "ensure_showcase_product_attached", "type": "ensure_showcase_product_attached"},
            {"id": "tap_scheduled_publish", "type": "tap_text_or_point", "publish_modes": ["scheduled"]},
            {"id": "tap_publish", "type": "tap_text_or_point", "publish_modes": ["immediate"]},
        ]

        selected = filter_steps_for_publish_mode(steps, "scheduled")

        self.assertEqual(
            [step["id"] for step in selected],
            ["tap_add_product", "ensure_showcase_product_attached", "tap_scheduled_publish"],
        )

    def test_shop_post_add_product_name_page_is_not_treated_as_product_list(self) -> None:
        xml_text = """
        <hierarchy bounds="[0,0][1080,2263]">
          <node class="android.widget.EditText" text="【B】Shampoo Rosa Mosqueta 300ml" focused="true" bounds="[44,954][1036,1003]" />
          <node class="android.view.ViewGroup" content-desc=" 添加" focusable="true" bounds="[44,2180][1036,2263]" />
        </hierarchy>
        """

        screen = {"xml_text": xml_text, "screenshot": "post_add.png", "xml": "post_add.xml"}
        result = showcase_workflow.classify_shop_post_add_screen(
            screen=screen,
            add_texts=DEFAULT_PRODUCT_ADD_TEXTS,
            already_added_texts=DEFAULT_PRODUCT_ALREADY_ADDED_TEXTS,
            expected_package="",
        )

        self.assertTrue(showcase_workflow.product_publish_name_page_visible(xml_text, DEFAULT_PRODUCT_ADD_TEXTS))
        self.assertEqual(result["status"], "product_publish_name_page")

    def test_redundant_schedule_picker_wait_step_is_skipped(self) -> None:
        steps = [
            {"id": "open_schedule_publish", "type": "tap_text_or_point", "publish_modes": ["scheduled"]},
            {"id": "wait_schedule_picker_open", "type": "wait_schedule_picker_open", "publish_modes": ["scheduled"]},
            {"id": "set_schedule_datetime", "type": "set_schedule_datetime", "publish_modes": ["scheduled"]},
            {"id": "close_more_settings", "type": "tap_text_or_point", "publish_modes": ["scheduled"]},
        ]

        selected = filter_steps_for_publish_mode(steps, "scheduled")

        self.assertEqual(
            [step["id"] for step in selected],
            ["open_schedule_publish", "set_schedule_datetime", "close_more_settings"],
        )

    def test_tap_until_any_text_stops_when_foreground_package_changes(self) -> None:
        class FakeADB:
            def __init__(self) -> None:
                self.taps: list[tuple[int, int]] = []

            def tap(self, x: int, y: int) -> None:
                self.taps.append((x, y))

            def dump_xml(self, _path: Path) -> str:
                return '<hierarchy><node package="com.android.chrome" /></hierarchy>'

            def foreground_package(self) -> str:
                return "com.android.chrome"

        adb = FakeADB()
        trace: dict[str, object] = {}

        with mock.patch.object(showcase_workflow.time, "sleep", return_value=None):
            with self.assertRaises(showcase_workflow.WorkflowError):
                showcase_workflow.tap_until_any_text(
                    adb=adb,
                    run_dir=ROOT,
                    step_id="tap_product_final_add",
                    point={
                        "x": 540,
                        "y": 2146,
                        "x_ratio": 0.5,
                        "y_ratio": 0.8942,
                        "description": "Final product add button",
                        "calibration_status": "verified",
                    },
                    texts=["发布"],
                    tap_texts=[],
                    max_taps=3,
                    wait_between_seconds=0,
                    match_mode="contains",
                    tap_match_mode="contains",
                    dry_run=False,
                    trace=trace,
                    initial_xml='<hierarchy><node package="com.zhiliaoapp.musically" /></hierarchy>',
                    required_package="com.zhiliaoapp.musically",
                )

        self.assertEqual(adb.taps, [(540, 2146)])
        self.assertEqual(trace["found_after_taps"], 1)
        self.assertEqual(trace["foreground_package_mismatch"]["expected"], "com.zhiliaoapp.musically")
        self.assertEqual(trace["foreground_package_mismatch"]["xml_packages"], ["com.android.chrome"])
        self.assertEqual(trace["foreground_package_mismatch"]["foreground_package"], "com.android.chrome")
        self.assertFalse(trace["foreground_package_mismatch"]["found"])

    def test_tap_until_any_text_redumps_before_foreground_failure(self) -> None:
        class FakeADB:
            def __init__(self) -> None:
                self.taps: list[tuple[int, int]] = []
                self.dumps = 0

            def tap(self, x: int, y: int) -> None:
                self.taps.append((x, y))

            def dump_xml(self, _path: Path) -> str:
                self.dumps += 1
                if self.dumps == 1:
                    return '<hierarchy><node package="com.sec.android.app.launcher" /></hierarchy>'
                return '<hierarchy><node package="com.zhiliaoapp.musically" text="娣诲姞閾炬帴" /></hierarchy>'

            def foreground_package(self) -> str:
                return "com.sec.android.app.launcher"

        adb = FakeADB()
        trace: dict[str, object] = {}

        with mock.patch.object(showcase_workflow.time, "sleep", return_value=None):
            found = showcase_workflow.tap_until_any_text(
                adb=adb,
                run_dir=ROOT,
                step_id="tap_next_until_publish_preset",
                point={"x": 532, "y": 1439, "x_ratio": 0.7389, "y_ratio": 0.8994},
                texts=["娣诲姞閾炬帴"],
                tap_texts=[],
                max_taps=1,
                wait_between_seconds=0,
                match_mode="contains",
                tap_match_mode="contains",
                dry_run=False,
                trace=trace,
                initial_xml='<hierarchy><node package="com.zhiliaoapp.musically" /></hierarchy>',
                required_package="com.zhiliaoapp.musically",
            )

        self.assertTrue(found)
        self.assertEqual(adb.taps, [(532, 1439)])
        self.assertEqual(adb.dumps, 2)
        self.assertTrue(trace["attempts"][0]["foreground_check"]["found"])

    def test_tap_until_any_text_prefers_live_text_button_when_available(self) -> None:
        class FakeADB:
            def __init__(self) -> None:
                self.taps: list[tuple[int, int]] = []

            def tap(self, x: int, y: int) -> None:
                self.taps.append((x, y))

            def dump_xml(self, _path: Path) -> str:
                return '<hierarchy><node package="com.zhiliaoapp.musically" text="添加链接" bounds="[100,100][220,140]" /></hierarchy>'

            def foreground_package(self) -> str:
                return "com.zhiliaoapp.musically"

        adb = FakeADB()
        trace: dict[str, object] = {}
        initial_xml = """
        <hierarchy>
          <node package="com.zhiliaoapp.musically" text="下一步" clickable="true" bounds="[360,1390][690,1480]" />
        </hierarchy>
        """

        with mock.patch.object(showcase_workflow.time, "sleep", return_value=None):
            found = showcase_workflow.tap_until_any_text(
                adb=adb,
                run_dir=ROOT,
                step_id="tap_next_until_publish_preset",
                point={"x": 532, "y": 1439, "x_ratio": 0.7389, "y_ratio": 0.8994},
                texts=["添加链接"],
                tap_texts=["下一步", "Next"],
                max_taps=1,
                wait_between_seconds=0,
                match_mode="contains",
                tap_match_mode="contains",
                dry_run=False,
                trace=trace,
                initial_xml=initial_xml,
                required_package="com.zhiliaoapp.musically",
            )

        self.assertTrue(found)
        self.assertEqual(adb.taps, [(525, 1435)])
        self.assertEqual(trace["attempts"][0]["tap_source"], "text")

    def test_wait_for_package_foreground_uses_dumpsys_focus_before_xml(self) -> None:
        class FakeADB:
            def foreground_package(self) -> str:
                return "com.zhiliaoapp.musically"

            def dump_xml(self, _path: Path) -> str:
                raise AssertionError("XML should not be required when dumpsys already has foreground focus")

        found, attempts = showcase_workflow.wait_for_package_foreground(
            adb=FakeADB(),
            run_dir=ROOT,
            step_id="open_tiktok",
            package="com.zhiliaoapp.musically",
            timeout_seconds=1,
            poll_seconds=0,
        )

        self.assertTrue(found)
        self.assertEqual(attempts[0]["source"], "dumpsys_window")

    def test_gallery_entry_switches_camera_to_video_mode_before_tapping_upload(self) -> None:
        calls: list[tuple[int, int]] = []
        xml_text = """
        <hierarchy>
          <node text="60 秒" bounds="[108,1090][178,1140]" />
          <node text="照片" bounds="[315,1090][405,1140]" selected="true" />
        </hierarchy>
        """

        class FakeADB:
            def tap(self, x: int, y: int) -> None:
                calls.append((x, y))

        with mock.patch.object(showcase_workflow.time, "sleep", return_value=None):
            result = showcase_workflow.ensure_camera_video_mode_before_gallery(
                adb=FakeADB(),
                run_dir=ROOT,
                step={},
                screen={"screenshot": "", "xml": "", "xml_text": xml_text},
                dry_run=False,
                wait_scale=1.0,
            )

        self.assertTrue(result["tapped"])
        self.assertEqual(calls, [(143, 1115)])

    def test_tiktok_shop_supplement_scans_without_search_field(self) -> None:
        calls: list[tuple[str, str]] = []

        class FakeADB:
            def editor_code(self, code: int) -> None:
                raise AssertionError(f"editor_code should not be used, got {code}")

        def fake_tap_text_or_configured_point(**kwargs):
            calls.append(("tap", str(kwargs["step_id"])))
            return {"action": "tap_text", "step_id": str(kwargs["step_id"])}

        def fake_capture(_adb, _run_dir, step_id: str):
            calls.append(("capture", step_id))
            return {"screenshot": "", "xml": "", "xml_text": ""}

        def fake_search_visible_product_list(**kwargs):
            calls.append(("scan", str(kwargs["area"])))
            return {"status": "matched", "_last_screen": {}}

        def fail_if_called(*_args, **_kwargs):
            raise AssertionError("search field input path should not be used")

        with (
            mock.patch.object(showcase_workflow, "tap_text_or_configured_point", side_effect=fake_tap_text_or_configured_point),
            mock.patch.object(showcase_workflow, "capture", side_effect=fake_capture),
            mock.patch.object(showcase_workflow, "search_visible_product_list", side_effect=fake_search_visible_product_list),
            mock.patch.object(showcase_workflow, "confirm_shop_add_dialog_if_present", return_value={"status": "absent"}),
            mock.patch.object(showcase_workflow, "return_to_showcase_product_list", return_value={"status": "showcase_visible"}) as return_to_showcase,
            mock.patch.object(showcase_workflow, "clear_focused_text", side_effect=fail_if_called),
            mock.patch.object(showcase_workflow, "paste_text", side_effect=fail_if_called),
            mock.patch.object(showcase_workflow.time, "sleep", return_value=None),
        ):
            result = showcase_workflow.add_product_from_tiktok_shop(
                adb=FakeADB(),
                profile={"apps": {"tiktok": {"package": "com.zhiliaoapp.musically"}}},
                step={},
                context={"product_search_title": "Target Product", "wait_scale": 1.0},
                run_dir=ROOT,
                target_key="targetproduct",
                starting_screen={"screenshot": "", "xml": "", "xml_text": ""},
            )

        self.assertEqual(result["shop_scan_mode"], "direct_list_scan_without_search")
        self.assertEqual(
            [call for call in calls if call[0] == "tap"],
            [
                ("tap", "ensure_showcase_product_attached_add_more_products"),
                ("tap", "ensure_showcase_product_attached_tiktok_shop_tab"),
            ],
        )
        self.assertIn(("scan", "tiktok_shop"), calls)
        self.assertNotIn(("capture", "ensure_showcase_product_attached_shop_search"), calls)
        self.assertEqual(result["result"], "selected_from_tiktok_shop")
        return_to_showcase.assert_not_called()

    def test_visible_product_scan_resets_to_top_before_matching(self) -> None:
        calls: list[tuple] = []

        def screen(xml_text: str, name: str) -> dict[str, str]:
            return {"screenshot": f"{name}.png", "xml": f"{name}.xml", "xml_text": xml_text}

        def product_xml(title: str) -> str:
            safe_title = title.replace("&", "&amp;")
            return f"""
            <hierarchy bounds="[0,0][1080,2400]">
              <node class="androidx.recyclerview.widget.RecyclerView" scrollable="true" bounds="[0,380][1080,2186]">
                <node class="android.view.ViewGroup" bounds="[0,360][1080,730]">
                  <node class="android.widget.TextView" text="{safe_title}" bounds="[356,417][720,466]" />
                  <node class="android.widget.TextView" text="你的商店 | 库存 2000 件" bounds="[356,470][720,520]" />
                  <node class="android.widget.Button" content-desc="button_add_product" bounds="[744,607][1038,691]" />
                </node>
              </node>
            </hierarchy>
            """

        loading_screen = screen('<hierarchy bounds="[0,0][1080,2400]"><node text="加载中..." bounds="[400,120][700,180]" /></hierarchy>', "loading")
        middle_screen = screen(product_xml("[Frete Gratis] Oleo Capilar de Argan Nat..."), "middle")
        top_screen = screen(product_xml("Kit Facial Dia e Noite Lua&Neve: Limpeza .."), "top")
        captures = [middle_screen, top_screen]

        class FakeADB:
            def swipe(self, *args) -> None:
                calls.append(("swipe", *args))

            def tap(self, x: int, y: int) -> None:
                calls.append(("tap", x, y))

        def fake_capture(_adb, _run_dir, step_id: str):
            calls.append(("capture", step_id))
            return captures.pop(0)

        step = {
            "ocr_min_confidence": 0.85,
            "reset_to_top_swipes": 1,
            "product_list_ready_attempts": 2,
            "product_list_ready_wait_seconds": 0,
            "wait_after_reset_swipe_seconds": 0,
            "add_button_texts": ["Add"],
            "already_added_texts": ["Added"],
            "add_more_products_texts": ["Add more products"],
        }

        with (
            mock.patch.object(showcase_workflow, "capture", side_effect=fake_capture),
            mock.patch.object(showcase_workflow, "detect_product_ocr_candidates", return_value=[]),
            mock.patch.object(showcase_workflow, "write_product_scan_progress", return_value=None),
            mock.patch.object(showcase_workflow.time, "sleep", return_value=None),
        ):
            result = showcase_workflow.search_visible_product_list(
                adb=FakeADB(),
                run_dir=ROOT,
                step=step,
                area="showcase",
                target_key=product_match_key("Kit Facial Dia e Noite Lua&Nev", match_prefix_chars=30),
                dry_run=False,
                initial_screen=loading_screen,
                max_swipes=0,
            )

        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["ready_attempts"][-1]["reason"], "product_action_button")
        self.assertEqual(result["reset_to_top"]["swipes"], 1)
        self.assertEqual(result["selected"]["title_text"], "Kit Facial Dia e Noite Lua&Neve: Limpeza ..")
        self.assertIn(("tap", 891, 649), calls)
        self.assertEqual([call[0] for call in calls].count("swipe"), 1)

    def test_visible_product_scan_does_not_reset_to_top_by_default(self) -> None:
        calls: list[tuple] = []
        xml_text = """
        <hierarchy bounds="[0,0][1080,2400]">
          <node class="androidx.recyclerview.widget.RecyclerView" scrollable="true" bounds="[0,380][1080,2186]">
            <node class="android.view.ViewGroup" bounds="[0,360][1080,730]">
              <node class="android.widget.TextView" text="Kit Facial Dia e Noite Lua&amp;Neve: Limpeza .." bounds="[356,417][720,466]" />
              <node class="android.view.ViewGroup" focusable="true" content-desc=" 添加" bounds="[744,607][1038,691]" />
            </node>
          </node>
        </hierarchy>
        """
        initial_screen = {"screenshot": "start.png", "xml": "start.xml", "xml_text": xml_text}

        class FakeADB:
            def swipe(self, *args) -> None:
                calls.append(("swipe", *args))

            def tap(self, x: int, y: int) -> None:
                calls.append(("tap", x, y))

        with (
            mock.patch.object(showcase_workflow, "detect_product_ocr_candidates", return_value=[]),
            mock.patch.object(showcase_workflow, "write_product_scan_progress", return_value=None),
        ):
            result = showcase_workflow.search_visible_product_list(
                adb=FakeADB(),
                run_dir=ROOT,
                step={"ocr_min_confidence": 0.85},
                area="showcase",
                target_key=product_match_key("Kit Facial Dia e Noite Lua&Nev", match_prefix_chars=30),
                dry_run=False,
                initial_screen=initial_screen,
                max_swipes=0,
            )

        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["reset_to_top"]["reason"], "disabled")
        self.assertNotIn("swipe", [call[0] for call in calls])

    def test_visible_product_scan_waits_for_titles_before_swiping(self) -> None:
        calls: list[tuple] = []
        target = "Espuma de Limpeza Facial Vita C com Escova"
        skeleton_xml = """
        <hierarchy bounds="[0,0][720,1600]">
          <node class="androidx.recyclerview.widget.RecyclerView" scrollable="true" bounds="[0,236][720,1457]">
            <node class="android.view.ViewGroup" focusable="true" content-desc="添加,button" bounds="[525,439][690,491]" />
          </node>
        </hierarchy>
        """
        ready_xml = f"""
        <hierarchy bounds="[0,0][720,1600]">
          <node class="androidx.recyclerview.widget.RecyclerView" scrollable="true" bounds="[0,236][720,1457]">
            <node class="android.view.ViewGroup" bounds="[0,236][720,514]">
              <node class="android.widget.TextView" text="{target}" bounds="[278,250][690,320]" />
              <node class="android.view.ViewGroup" focusable="true" content-desc="添加,button" bounds="[525,439][690,491]" />
            </node>
          </node>
        </hierarchy>
        """

        class FakeADB:
            def swipe(self, *args) -> None:
                calls.append(("swipe", *args))

            def tap(self, x: int, y: int) -> None:
                calls.append(("tap", x, y))

        def fake_capture(_adb, _run_dir, step_id: str):
            calls.append(("capture", step_id))
            return {"screenshot": "ready.png", "xml": "ready.xml", "xml_text": ready_xml}

        with (
            mock.patch.object(showcase_workflow, "capture", side_effect=fake_capture),
            mock.patch.object(showcase_workflow, "detect_product_ocr_candidates", return_value=[]),
            mock.patch.object(showcase_workflow, "write_product_scan_progress", return_value=None),
            mock.patch.object(showcase_workflow.time, "sleep", return_value=None),
        ):
            result = showcase_workflow.search_visible_product_list(
                adb=FakeADB(),
                run_dir=ROOT,
                step={
                    "ocr_min_confidence": 0.85,
                    "product_title_ready_attempts": 2,
                    "product_title_ready_wait_seconds": 0,
                },
                area="tiktok_shop",
                target_key=product_match_key(target, match_prefix_chars=30),
                dry_run=False,
                initial_screen={"screenshot": "skeleton.png", "xml": "skeleton.xml", "xml_text": skeleton_xml},
                max_swipes=1,
            )

        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["attempts"][0]["title_wait_attempts"][0]["titled_card_count"], 1)
        self.assertIn(("capture", "ensure_showcase_product_attached_tiktok_shop_title_ready_00_01"), calls)
        self.assertNotIn("swipe", [call[0] for call in calls])

    def test_visible_product_scan_fails_without_swiping_when_titles_unreadable_and_ocr_missing(self) -> None:
        calls: list[tuple] = []
        xml_text = """
        <hierarchy bounds="[0,0][720,1600]">
          <node class="androidx.recyclerview.widget.RecyclerView" scrollable="true" bounds="[0,236][720,1457]">
            <node class="android.view.ViewGroup" focusable="true" content-desc="添加,button" bounds="[525,439][690,491]" />
            <node class="android.view.ViewGroup" focusable="true" content-desc="添加,button" bounds="[525,717][690,769]" />
          </node>
        </hierarchy>
        """

        class FakeADB:
            def swipe(self, *args) -> None:
                calls.append(("swipe", *args))

        def fake_capture(_adb, _run_dir, step_id: str):
            calls.append(("capture", step_id))
            return {"screenshot": f"{step_id}.png", "xml": f"{step_id}.xml", "xml_text": xml_text}

        with (
            mock.patch.object(showcase_workflow, "capture", side_effect=fake_capture),
            mock.patch.object(showcase_workflow, "detect_product_ocr_candidates", return_value=[]),
            mock.patch.object(showcase_workflow, "product_title_ocr_available", return_value=False),
            mock.patch.object(showcase_workflow, "write_product_scan_progress", return_value=None),
            mock.patch.object(showcase_workflow.time, "sleep", return_value=None),
        ):
            result = showcase_workflow.search_visible_product_list(
                adb=FakeADB(),
                run_dir=ROOT,
                step={
                    "ocr_min_confidence": 0.85,
                    "product_title_ready_attempts": 2,
                    "product_title_ready_wait_seconds": 0,
                },
                area="tiktok_shop",
                target_key=product_match_key("Espuma de Limpeza Facial Vita", match_prefix_chars=30),
                dry_run=False,
                initial_screen={"screenshot": "skeleton.png", "xml": "skeleton.xml", "xml_text": xml_text},
                max_swipes=8,
            )

        self.assertEqual(result["status"], "titles_unreadable")
        self.assertEqual(result["reason"], "titles_unreadable_ocr_unavailable")
        self.assertEqual(result["attempts"][0]["titled_card_count"], 0)
        self.assertEqual(len(result["attempts"][0]["title_wait_attempts"]), 2)
        self.assertNotIn("swipe", [call[0] for call in calls])

    def test_visible_product_scan_stops_if_reset_leaves_tiktok(self) -> None:
        calls: list[tuple] = []
        tiktok_xml = """
        <hierarchy bounds="[0,0][1080,2400]">
          <node package="com.zhiliaoapp.musically" class="androidx.recyclerview.widget.RecyclerView" scrollable="true" bounds="[0,380][1080,2186]">
            <node package="com.zhiliaoapp.musically" class="android.view.ViewGroup" focusable="true" content-desc=" 添加" bounds="[744,607][1038,691]" />
          </node>
        </hierarchy>
        """
        launcher_xml = """
        <hierarchy bounds="[0,0][1080,2400]">
          <node package="com.sec.android.app.launcher" class="android.widget.FrameLayout" bounds="[0,0][1080,2400]" />
        </hierarchy>
        """

        class FakeADB:
            def foreground_package(self) -> str:
                return "com.sec.android.app.launcher"

            def dump_xml(self, _path: Path) -> str:
                return launcher_xml

            def swipe(self, *args) -> None:
                calls.append(("swipe", *args))

        def fake_capture(_adb, _run_dir, step_id: str):
            calls.append(("capture", step_id))
            return {"screenshot": "launcher.png", "xml": "launcher.xml", "xml_text": launcher_xml}

        with (
            mock.patch.object(showcase_workflow, "capture", side_effect=fake_capture),
            mock.patch.object(showcase_workflow, "detect_product_ocr_candidates", return_value=[]),
            mock.patch.object(showcase_workflow, "write_product_scan_progress", return_value=None),
            mock.patch.object(showcase_workflow.time, "sleep", return_value=None),
        ):
            with self.assertRaisesRegex(showcase_workflow.WorkflowError, "product scan left the expected app"):
                showcase_workflow.search_visible_product_list(
                    adb=FakeADB(),
                    run_dir=ROOT,
                    step={
                        "reset_to_top_swipes": 1,
                        "wait_after_reset_swipe_seconds": 0,
                    },
                    area="showcase",
                    target_key=product_match_key("Kit Facial Dia e Noite Lua&Nev", match_prefix_chars=30),
                    dry_run=False,
                    initial_screen={"screenshot": "start.png", "xml": "start.xml", "xml_text": tiktok_xml},
                    max_swipes=0,
                    expected_package="com.zhiliaoapp.musically",
                )

        self.assertEqual([call[0] for call in calls].count("swipe"), 1)

    def test_visible_product_scan_scrolls_when_matched_add_button_is_obstructed(self) -> None:
        calls: list[tuple] = []
        title = "Kit Facial Dia e Noite Lua&Neve: Limpeza .."

        def product_xml(button_bounds: str, card_bounds: str, title_bounds: str) -> str:
            safe_title = title.replace("&", "&amp;")
            return f"""
            <hierarchy bounds="[0,0][1080,2186]">
              <node class="androidx.recyclerview.widget.RecyclerView" scrollable="true" bounds="[0,339][1080,2186]">
                <node class="android.view.ViewGroup" bounds="{card_bounds}">
                  <node class="android.widget.TextView" text="{safe_title}" bounds="{title_bounds}" />
                  <node class="android.view.ViewGroup" focusable="true" content-desc=" 添加" bounds="{button_bounds}" />
                </node>
              </node>
            </hierarchy>
            """

        initial_screen = {
            "screenshot": "bottom.png",
            "xml": "bottom.xml",
            "xml_text": product_xml("[776,2075][1007,2159]", "[0,1854][1080,2180]", "[356,1886][720,1936]"),
        }
        safe_screen = {
            "screenshot": "safe.png",
            "xml": "safe.xml",
            "xml_text": product_xml("[776,571][1007,655]", "[0,350][1080,676]", "[356,382][720,432]"),
        }

        class FakeADB:
            def swipe(self, *args) -> None:
                calls.append(("swipe", *args))

            def tap(self, x: int, y: int) -> None:
                calls.append(("tap", x, y))

        def fake_capture(_adb, _run_dir, step_id: str):
            calls.append(("capture", step_id))
            return safe_screen

        step = {
            "ocr_min_confidence": 0.85,
            "reset_to_top_swipes": 0,
            "wait_after_swipe_seconds": 0,
            "add_button_texts": ["??", "Add"],
            "already_added_texts": ["Added"],
            "add_button_safe_bottom_ratio": 0.90,
        }

        with (
            mock.patch.object(showcase_workflow, "capture", side_effect=fake_capture),
            mock.patch.object(showcase_workflow, "detect_product_ocr_candidates", return_value=[]),
            mock.patch.object(showcase_workflow, "write_product_scan_progress", return_value=None),
            mock.patch.object(showcase_workflow.time, "sleep", return_value=None),
        ):
            result = showcase_workflow.search_visible_product_list(
                adb=FakeADB(),
                run_dir=ROOT,
                step=step,
                area="showcase",
                target_key=product_match_key("Kit Facial Dia e Noite Lua&Nev", match_prefix_chars=30),
                dry_run=False,
                initial_screen=initial_screen,
                max_swipes=1,
            )

        self.assertEqual(result["status"], "matched")
        self.assertTrue(result["attempts"][0]["selected_unsafe"])
        self.assertEqual(result["attempts"][0]["unsafe_reason"], "add_button_near_bottom_obstruction")
        self.assertIn(("tap", 891, 613), calls)
        self.assertEqual([call[0] for call in calls].count("swipe"), 1)

    def test_showcase_scan_stops_when_add_more_products_is_visible(self) -> None:
        calls: list[tuple] = []
        xml_text = """
        <hierarchy>
          <node package="com.zhiliaoapp.musically" class="android.widget.FrameLayout" bounds="[0,0][720,1457]">
            <node package="com.zhiliaoapp.musically" class="androidx.recyclerview.widget.RecyclerView" scrollable="true" bounds="[0,223][720,1457]" />
          </node>
        </hierarchy>
        """

        class FakeADB:
            def foreground_package(self) -> str:
                return "com.zhiliaoapp.musically"

            def swipe(self, *args) -> None:
                calls.append(("swipe", *args))

        with (
            mock.patch.object(
                showcase_workflow,
                "detect_product_ocr_candidates",
                return_value=[ocr("添加更多商品", 30, 1407, 690, 1489, 0.96)],
            ),
            mock.patch.object(showcase_workflow, "write_product_scan_progress", return_value=None),
            mock.patch.object(showcase_workflow.time, "sleep", return_value=None),
        ):
            result = showcase_workflow.search_visible_product_list(
                adb=FakeADB(),
                run_dir=ROOT,
                step={"product_list_ready_attempts": 1},
                area="showcase",
                target_key=product_match_key("Kit Facial Dia e Noite Lua&Nev", match_prefix_chars=30),
                dry_run=False,
                initial_screen={"screenshot": "bottom.png", "xml": "bottom.xml", "xml_text": xml_text},
                max_swipes=8,
                expected_package="com.zhiliaoapp.musically",
            )

        self.assertEqual(result["status"], "not_found")
        self.assertEqual(result["reason"], "add_more_products_visible")
        self.assertTrue(result["attempts"][0]["add_more_products_visible"])
        self.assertNotIn("swipe", [call[0] for call in calls])

    def test_tiktok_shop_scan_rejects_ordinary_search_results_page(self) -> None:
        calls: list[tuple] = []
        xml_text = """
        <hierarchy>
          <node package="com.zhiliaoapp.musically" text="综合" bounds="[10,100][90,150]" />
          <node package="com.zhiliaoapp.musically" text="用户" bounds="[100,100][180,150]" />
          <node package="com.zhiliaoapp.musically" text="购物" bounds="[190,100][270,150]" />
          <node package="com.zhiliaoapp.musically" text="视频" bounds="[280,100][360,150]" />
          <node package="com.zhiliaoapp.musically" text="话题标签" bounds="[370,100][520,150]" />
        </hierarchy>
        """

        class FakeADB:
            def foreground_package(self) -> str:
                return "com.zhiliaoapp.musically"

            def swipe(self, *args) -> None:
                calls.append(("swipe", *args))

        with (
            mock.patch.object(showcase_workflow, "detect_product_ocr_candidates", return_value=[]),
            mock.patch.object(showcase_workflow, "write_product_scan_progress", return_value=None),
            mock.patch.object(showcase_workflow.time, "sleep", return_value=None),
        ):
            result = showcase_workflow.search_visible_product_list(
                adb=FakeADB(),
                run_dir=ROOT,
                step={"product_list_ready_attempts": 1},
                area="tiktok_shop",
                target_key=product_match_key("Target Product", match_prefix_chars=30),
                dry_run=False,
                initial_screen={"screenshot": "search.png", "xml": "search.xml", "xml_text": xml_text},
                max_swipes=3,
                expected_package="com.zhiliaoapp.musically",
            )

        self.assertEqual(result["status"], "not_product_list")
        self.assertEqual(result["reason"], "ordinary_tiktok_search_results")
        self.assertTrue(result["attempts"][0]["ordinary_search_results_visible"])
        self.assertNotIn("swipe", [call[0] for call in calls])

    def test_tiktok_shop_post_add_list_state_returns_to_showcase(self) -> None:
        calls: list[tuple[str, str]] = []
        product_list_xml = """
        <hierarchy>
          <node package="com.zhiliaoapp.musically" class="android.view.ViewGroup" focusable="true" content-desc=" Add" bounds="[520,700][650,760]" />
        </hierarchy>
        """

        class FakeADB:
            def foreground_package(self) -> str:
                return "com.zhiliaoapp.musically"

        def fake_tap_text_or_configured_point(**kwargs):
            calls.append(("tap", str(kwargs["step_id"])))
            return {"action": "tap_text", "step_id": str(kwargs["step_id"])}

        def fake_capture(_adb, _run_dir, step_id: str):
            calls.append(("capture", step_id))
            if step_id == "ensure_showcase_product_attached_shop_post_add":
                return {"screenshot": "post.png", "xml": "post.xml", "xml_text": product_list_xml}
            return {"screenshot": "", "xml": "", "xml_text": ""}

        with (
            mock.patch.object(showcase_workflow, "tap_text_or_configured_point", side_effect=fake_tap_text_or_configured_point),
            mock.patch.object(showcase_workflow, "capture", side_effect=fake_capture),
            mock.patch.object(showcase_workflow, "search_visible_product_list", return_value={"status": "matched"}),
            mock.patch.object(showcase_workflow, "confirm_shop_add_dialog_if_present", return_value={"status": "absent"}),
            mock.patch.object(showcase_workflow, "return_to_showcase_product_list", return_value={"status": "showcase_visible"}) as return_to_showcase,
            mock.patch.object(showcase_workflow.time, "sleep", return_value=None),
        ):
            result = showcase_workflow.add_product_from_tiktok_shop(
                adb=FakeADB(),
                profile={"apps": {"tiktok": {"package": "com.zhiliaoapp.musically"}}},
                step={},
                context={"product_search_title": "Target Product", "wait_scale": 1.0},
                run_dir=ROOT,
                target_key="targetproduct",
                starting_screen={"screenshot": "", "xml": "", "xml_text": ""},
            )

        self.assertEqual(result["result"], "returned_to_showcase_after_shop_add")
        self.assertEqual(result["shop_post_add"]["status"], "product_list")
        return_to_showcase.assert_called_once()

    def test_shop_confirm_add_dialog_taps_modal_confirm_point(self) -> None:
        calls: list[tuple] = []
        xml_text = """
        <hierarchy>
          <node class="android.widget.FrameLayout" bounds="[0,0][720,1457]">
            <node class="android.view.ViewGroup" bounds="[98,597][623,1003]" />
          </node>
        </hierarchy>
        """
        after_xml = "<hierarchy bounds=\"[0,0][720,1457]\"></hierarchy>"

        class FakeADB:
            def tap(self, x: int, y: int) -> None:
                calls.append(("tap", x, y))

        with (
            mock.patch.object(
                showcase_workflow,
                "capture",
                side_effect=[
                    {"screenshot": "confirm.png", "xml": "confirm.xml", "xml_text": xml_text},
                    {"screenshot": "after.png", "xml": "after.xml", "xml_text": after_xml},
                ],
            ),
            mock.patch.object(showcase_workflow, "paddle_ocr_optional_gate", return_value={"present": True}),
            mock.patch.object(showcase_workflow.time, "sleep", return_value=None),
        ):
            result = showcase_workflow.confirm_shop_add_dialog_if_present(
                adb=FakeADB(),
                profile={
                    "coordinate_points": {
                        "product_optional_confirm_add": {"x": 360, "y": 863, "x_ratio": 0.5, "y_ratio": 0.5392}
                    }
                },
                run_dir=ROOT,
                step={},
                wait_scale=1.0,
            )

        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(result["method"], "ocr_presence_fixed_point")
        self.assertEqual(calls, [("tap", 360, 863)])

    def test_shop_confirm_add_dialog_prefers_xml_confirm_row(self) -> None:
        calls: list[tuple] = []
        xml_text = """
        <hierarchy bounds="[0,0][1080,2186]">
          <node class="android.view.ViewGroup" bounds="[172,916][907,1483]" />
          <node class="android.widget.ScrollView" bounds="[172,1076][907,1176]" />
          <node class="android.view.View" bounds="[172,1230][907,1231]" />
          <node class="android.view.View" bounds="[172,1356][907,1357]" />
        </hierarchy>
        """
        after_xml = "<hierarchy bounds=\"[0,0][1080,2186]\"></hierarchy>"

        class FakeADB:
            def tap(self, x: int, y: int) -> None:
                calls.append(("tap", x, y))

        with (
            mock.patch.object(
                showcase_workflow,
                "capture",
                side_effect=[
                    {"screenshot": ROOT / "confirm.png", "xml": "confirm.xml", "xml_text": xml_text},
                    {"screenshot": ROOT / "after.png", "xml": "after.xml", "xml_text": after_xml},
                ],
            ),
            mock.patch.object(showcase_workflow, "paddle_ocr_optional_gate", return_value={"present": True}),
            mock.patch.object(showcase_workflow, "png_size", return_value=(1080, 2400)),
            mock.patch.object(showcase_workflow.time, "sleep", return_value=None),
        ):
            result = showcase_workflow.confirm_shop_add_dialog_if_present(
                adb=FakeADB(),
                profile={
                    "coordinate_points": {
                        "product_optional_confirm_add": {"x": 360, "y": 863, "x_ratio": 0.5, "y_ratio": 0.5392}
                    }
                },
                run_dir=ROOT,
                step={},
                wait_scale=1.0,
            )

        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(result["method"], "xml_centered_add_cancel_popup")
        self.assertTrue(result["used_dynamic_tap"])
        self.assertEqual(calls, [("tap", 539, 1293)])

    def test_shop_confirm_add_dialog_fails_when_modal_remains_after_tap(self) -> None:
        calls: list[tuple] = []
        xml_text = """
        <hierarchy bounds="[0,0][1080,2186]">
          <node class="android.view.ViewGroup" bounds="[172,916][907,1483]" />
          <node class="android.widget.ScrollView" bounds="[172,1076][907,1176]" />
          <node class="android.view.View" bounds="[172,1230][907,1231]" />
          <node class="android.view.View" bounds="[172,1356][907,1357]" />
        </hierarchy>
        """

        class FakeADB:
            def tap(self, x: int, y: int) -> None:
                calls.append(("tap", x, y))

        with (
            mock.patch.object(
                showcase_workflow,
                "capture",
                side_effect=[
                    {"screenshot": ROOT / "confirm.png", "xml": "confirm.xml", "xml_text": xml_text},
                    {"screenshot": ROOT / "after.png", "xml": "after.xml", "xml_text": xml_text},
                ],
            ),
            mock.patch.object(showcase_workflow, "paddle_ocr_optional_gate", return_value={"present": True}),
            mock.patch.object(showcase_workflow, "png_size", return_value=(1080, 2400)),
            mock.patch.object(showcase_workflow.time, "sleep", return_value=None),
        ):
            with self.assertRaisesRegex(showcase_workflow.WorkflowError, "dialog is still visible"):
                showcase_workflow.confirm_shop_add_dialog_if_present(
                    adb=FakeADB(),
                    profile={
                        "coordinate_points": {
                            "product_optional_confirm_add": {"x": 360, "y": 863, "x_ratio": 0.5, "y_ratio": 0.5392}
                        }
                    },
                    run_dir=ROOT,
                    step={},
                    wait_scale=1.0,
                )

        self.assertEqual(calls, [("tap", 539, 1293)])

    def test_schedule_verification_taps_done_dialog_then_retries(self) -> None:
        calls: list[tuple] = []
        dialog_xml = """
        <hierarchy>
          <node text="发布时间" bounds="[100,80][300,130]" />
          <node text="完成" clickable="true" bounds="[600,80][700,130]" />
        </hierarchy>
        """
        publish_xml = """
        <hierarchy>
          <node text="预约发布作品" />
          <node text="2026-5-18 09:35" />
        </hierarchy>
        """

        class FakeADB:
            def tap(self, x: int, y: int) -> None:
                calls.append(("tap", x, y))

        def fake_capture(_adb, _run_dir, step_id: str):
            calls.append(("capture", step_id))
            return {"screenshot": "publish.png", "xml": "publish.xml", "xml_text": publish_xml}

        with (
            mock.patch.object(showcase_workflow, "capture", side_effect=fake_capture),
            mock.patch.object(showcase_workflow.time, "sleep", return_value=None),
        ):
            result = showcase_workflow.verify_showcase_schedule_configured_from_current_ui(
                adb=FakeADB(),
                run_dir=ROOT,
                step_id="verify_schedule_configured",
                step={},
                initial_screen={"screenshot": "dialog.png", "xml": "dialog.xml", "xml_text": dialog_xml},
                context={
                    "schedule_date": "2026-05-18",
                    "schedule_time": "09:36",
                    "schedule_verification_tolerance_minutes": 15,
                    "wait_scale": 1.0,
                },
                dry_run=False,
            )

        self.assertTrue(result["found"])
        self.assertEqual(result["attempts"][0]["page_type"], "schedule_dialog")
        self.assertTrue(result["attempts"][0]["handled_schedule_dialog"])
        self.assertEqual(calls[0], ("tap", 650, 105))

    def test_schedule_verification_does_not_treat_done_substring_as_dialog_button(self) -> None:
        xml_text = """
        <hierarchy>
          <node text="Bar Do Neymar" bounds="[1058,1021][1080,1065]" />
          <node text="预约发布作品" />
          <node text="2026-5-19 10:00" />
        </hierarchy>
        """

        self.assertEqual(
            showcase_workflow.classify_schedule_verification_page(xml_text),
            "publish_or_more_settings",
        )

        class FakeADB:
            def tap(self, x: int, y: int) -> None:
                raise AssertionError(f"unexpected tap at {x},{y}")

        result = showcase_workflow.verify_showcase_schedule_configured_from_current_ui(
            adb=FakeADB(),
            run_dir=ROOT,
            step_id="verify_schedule_configured",
            step={},
            initial_screen={"screenshot": "settings.png", "xml": "settings.xml", "xml_text": xml_text},
            context={
                "schedule_date": "2026-05-19",
                "schedule_time": "10:00",
                "schedule_verification_tolerance_minutes": 15,
                "wait_scale": 1.0,
            },
            dry_run=False,
        )

        self.assertTrue(result["found"])
        self.assertEqual(result["attempts"][0]["page_type"], "publish_or_more_settings")

    def test_schedule_verification_accepts_time_within_15_minutes(self) -> None:
        xml_text = """
        <hierarchy>
          <node text="预约发布作品" />
          <node text="2026-5-18 09:35" />
        </hierarchy>
        """

        result = showcase_workflow.verify_showcase_schedule_configured(
            xml_text,
            {
                "schedule_date": "2026-05-18",
                "schedule_time": "09:36",
                "schedule_verification_tolerance_minutes": 15,
            },
        )

        self.assertTrue(result["found"])
        self.assertEqual(result["matched"]["time_24"], "09:35")
        self.assertLessEqual(result["matched"]["delta_minutes"], 15)

    def test_schedule_verification_rejects_time_outside_15_minutes(self) -> None:
        xml_text = """
        <hierarchy>
          <node text="预约发布作品" />
          <node text="2026/5/18 09:00" />
        </hierarchy>
        """

        with self.assertRaisesRegex(showcase_workflow.WorkflowError, "outside tolerance"):
            showcase_workflow.verify_showcase_schedule_configured(
                xml_text,
                {
                    "schedule_date": "2026-05-18",
                    "schedule_time": "09:36",
                    "schedule_verification_tolerance_minutes": 15,
                },
            )

    def test_optional_add_product_popup_uses_centered_add_cancel_structure(self) -> None:
        candidates = [
            ocr("Super Kit Leve 3", 405, 593, 1167, 633, 0.98),
            ocr("添加", 557, 1427, 666, 1494, 0.99),
            ocr("取消", 557, 1574, 666, 1641, 0.99),
            ocr("添加", 953, 2003, 1056, 2099, 0.99),
        ]

        with mock.patch.object(showcase_workflow, "png_size", return_value=(1220, 2712)):
            tap = showcase_workflow.optional_add_product_confirm_popup_tap(
                candidates=candidates,
                screenshot_path=ROOT / "popup.png",
                min_confidence=0.3,
            )

        self.assertIsNotNone(tap)
        assert tap is not None
        self.assertEqual(tap["x"], 611)
        self.assertEqual(tap["y"], 1460)
        self.assertEqual(tap["reason"], "centered_two_button_add_cancel_popup")

    def test_optional_add_product_popup_prefers_xml_button_row_over_body_ocr(self) -> None:
        candidates = [
            ocr("body text", 340, 1120, 720, 1184, 0.98),
            ocr("cancel", 486, 1390, 594, 1460, 0.99),
        ]
        xml_text = """
        <hierarchy bounds="[0,0][1080,2186]">
          <node class="android.view.ViewGroup" bounds="[172,916][907,1483]" />
          <node class="android.widget.ScrollView" bounds="[172,1076][907,1176]" />
          <node class="android.view.View" bounds="[172,1230][907,1231]" />
          <node class="android.view.View" bounds="[172,1356][907,1357]" />
        </hierarchy>
        """

        with mock.patch.object(showcase_workflow, "png_size", return_value=(1080, 2400)):
            tap = showcase_workflow.optional_add_product_confirm_popup_tap(
                candidates=candidates,
                screenshot_path=ROOT / "popup.png",
                xml_text=xml_text,
                min_confidence=0.3,
            )

        self.assertIsNotNone(tap)
        assert tap is not None
        self.assertEqual(tap["x"], 539)
        self.assertEqual(tap["y"], 1293)
        self.assertEqual(tap["reason"], "xml_centered_add_cancel_popup")

    def test_optional_add_product_popup_does_not_match_product_list_buttons(self) -> None:
        candidates = [
            ocr("Super Kit Leve 3", 405, 593, 1167, 633, 0.98),
            ocr("添加", 953, 836, 1056, 890, 0.99),
            ocr("添加", 953, 1232, 1056, 1286, 0.99),
            ocr("添加更多商品", 460, 2520, 760, 2588, 0.99),
        ]

        with mock.patch.object(showcase_workflow, "png_size", return_value=(1220, 2712)):
            tap = showcase_workflow.optional_add_product_confirm_popup_tap(
                candidates=candidates,
                screenshot_path=ROOT / "list.png",
                min_confidence=0.3,
            )

        self.assertIsNone(tap)


def ocr(text: str, left: int, top: int, right: int, bottom: int, confidence: float) -> TextCandidate:
    return TextCandidate(
        text=text,
        x=(left + right) // 2,
        y=(top + bottom) // 2,
        left=left,
        top=top,
        right=right,
        bottom=bottom,
        confidence=confidence,
        source="ocr",
    )


if __name__ == "__main__":
    unittest.main()
