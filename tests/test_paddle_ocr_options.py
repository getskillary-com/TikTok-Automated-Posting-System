from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "upload_system"
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from upload_system.ai import (
    DEFAULT_EXTERNAL_PADDLE_OCR_PYTHON,
    _filter_paddle_init_options,
    _instantiate_paddle_ocr,
    _paddle_new_api_options,
    _paddle_runtime_diagnostic,
    _resolve_external_paddle_python,
    _uses_new_paddle_ocr_api,
)


class PaddleOcrOptionCompatibilityTests(unittest.TestCase):
    def test_filter_removes_arguments_missing_from_new_paddle_signature(self) -> None:
        class NewPaddleOCR:
            def __init__(
                self,
                *,
                lang: str = "ch",
                device: str = "cpu",
                use_doc_orientation_classify: bool = False,
                use_doc_unwarping: bool = False,
                use_textline_orientation: bool = False,
                text_rec_score_thresh: float = 0.3,
            ) -> None:
                pass

        options = {
            "lang": "ch",
            "device": "cpu",
            "use_gpu": False,
            "use_angle_cls": False,
            "show_log": False,
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": False,
            "use_textline_orientation": False,
            "text_rec_score_thresh": 0.3,
        }
        filtered = _filter_paddle_init_options(NewPaddleOCR, options)

        self.assertNotIn("use_gpu", filtered)
        self.assertNotIn("use_angle_cls", filtered)
        self.assertNotIn("show_log", filtered)
        self.assertEqual(filtered["lang"], "ch")
        self.assertEqual(filtered["device"], "cpu")
        self.assertIn("use_textline_orientation", filtered)

    def test_instantiate_retries_after_unknown_argument_error(self) -> None:
        class RejectsUnknownUseGpu:
            def __init__(self, **kwargs) -> None:
                if "use_gpu" in kwargs:
                    raise ValueError("Unknown argument: use_gpu")
                self.kwargs = kwargs

        engine, used_options = _instantiate_paddle_ocr(
            RejectsUnknownUseGpu,
            {"lang": "ch", "use_gpu": False, "show_log": False},
        )

        self.assertIsInstance(engine, RejectsUnknownUseGpu)
        self.assertNotIn("use_gpu", used_options)
        self.assertEqual(used_options["lang"], "ch")

    def test_new_api_detection_handles_kwargs_signature(self) -> None:
        class NewPaddleOCRWithKwargs:
            def __init__(
                self,
                *,
                lang: str = "ch",
                use_doc_orientation_classify: bool = False,
                use_doc_unwarping: bool = False,
                use_textline_orientation: bool = False,
                **kwargs,
            ) -> None:
                pass

        self.assertTrue(_uses_new_paddle_ocr_api(NewPaddleOCRWithKwargs))

    def test_new_api_options_strip_legacy_only_arguments(self) -> None:
        filtered = _paddle_new_api_options(
            {
                "lang": "ch",
                "ocr_version": "PP-OCRv4",
                "device": "cpu",
                "use_gpu": False,
                "use_angle_cls": False,
                "show_log": False,
                "enable_mkldnn": False,
                "cpu_threads": 4,
                "use_doc_orientation_classify": False,
                "use_doc_unwarping": False,
                "use_textline_orientation": False,
                "text_rec_score_thresh": 0.3,
            }
        )

        self.assertNotIn("use_gpu", filtered)
        self.assertNotIn("use_angle_cls", filtered)
        self.assertNotIn("show_log", filtered)
        self.assertNotIn("enable_mkldnn", filtered)
        self.assertNotIn("cpu_threads", filtered)
        self.assertEqual(filtered["lang"], "ch")
        self.assertEqual(filtered["use_textline_orientation"], False)

    def test_runtime_diagnostic_reports_python_dependency_and_model_state(self) -> None:
        diagnostic = _paddle_runtime_diagnostic()

        self.assertIn(f"python={sys.version_info.major}.{sys.version_info.minor}", diagnostic)
        self.assertIn(f"py{sys.version_info.major}{sys.version_info.minor}", diagnostic)
        self.assertIn("available_deps=", diagnostic)
        self.assertIn("models=", diagnostic)

    def test_external_paddle_python_ignores_current_executable(self) -> None:
        self.assertIsNone(_resolve_external_paddle_python({"python": sys.executable}))

    def test_default_external_paddle_python_is_workspace_scoped(self) -> None:
        self.assertTrue(str(DEFAULT_EXTERNAL_PADDLE_OCR_PYTHON).endswith(r"storage\python39\python.exe"))


if __name__ == "__main__":
    unittest.main()
