from __future__ import annotations

import base64
import contextlib
import inspect
import json
import os
import re
import subprocess
import sys
import site
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .adb import UINode


class AIDecisionError(RuntimeError):
    pass


@dataclass(frozen=True)
class TextCandidate:
    text: str
    x: int
    y: int
    left: int
    top: int
    right: int
    bottom: int
    confidence: float
    source: str


_PADDLE_OCR_ENGINE_CACHE: dict[str, Any] = {}
PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOCAL_PADDLE_DEPS_ROOT = PROJECT_ROOT / ".paddle_deps"
LOCAL_PADDLE_DEPS = LOCAL_PADDLE_DEPS_ROOT / f"py{sys.version_info.major}{sys.version_info.minor}"
LOCAL_PADDLE_CACHE = PROJECT_ROOT / "storage" / "paddle_cache"
EXTERNAL_PADDLE_OCR_PYTHON_ENV = "GROUP_CONTROL_OCR_PYTHON"
DEFAULT_EXTERNAL_PADDLE_OCR_PYTHON = PROJECT_ROOT / "storage" / "python39" / "python.exe"
PADDLE_OCR_WORKER = PROJECT_ROOT / "workflow_database" / "scripts" / "paddle_ocr_worker.py"


def png_size(path: str | Path) -> tuple[int, int]:
    data = Path(path).read_bytes()[:24]
    if len(data) < 24 or not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError(f"Not a PNG file: {path}")
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    return width, height


def create_button_finder(config: dict[str, Any]) -> Any:
    provider = str(config.get("recognition", {}).get("provider", "paddle_ocr")).lower()
    if provider in {"paddle_ocr", "paddle", "paddleocr", "paddle_vision"}:
        return PaddleOCRButtonFinder(config)
    if provider in {"google_ocr", "google", "google_vision", "cloud_vision"}:
        return GoogleOCRButtonFinder(config)
    if provider in {"openai", "ai"}:
        return OpenAIButtonFinder(config)
    raise AIDecisionError(f"Unsupported recognition provider: {provider}")


class GoogleOCRButtonFinder:
    def __init__(self, config: dict[str, Any]) -> None:
        ocr_config = config.get("recognition", {})
        self.api_base = ocr_config.get("api_base", "https://vision.googleapis.com/v1").rstrip("/")
        api_key_env = ocr_config.get("google_api_key_env", "GOOGLE_CLOUD_VISION_API_KEY")
        self.api_key = str(ocr_config.get("api_key") or "") or os.environ.get(api_key_env, "")
        self.feature_type = ocr_config.get("feature_type", "TEXT_DETECTION")
        self.language_hints = ocr_config.get("language_hints", ["en", "zh"])
        if not self.api_key:
            raise AIDecisionError(f"Missing Google Vision API key environment variable: {api_key_env}")

    def decide(
        self,
        *,
        screenshot_path: Path,
        screen_size: tuple[int, int],
        ui_summary: str,
        ui_nodes: list[UINode] | None = None,
        config: dict[str, Any],
        remote_video_path: str,
        allow_publish: bool,
        step_number: int,
    ) -> dict[str, Any]:
        del ui_summary, remote_video_path, step_number
        ocr_data = self._detect_text(screenshot_path)
        ocr_candidates = _parse_google_ocr_candidates(ocr_data)
        disabled_texts = _disabled_ui_texts(ui_nodes or [])
        ocr_candidates = [
            candidate
            for candidate in ocr_candidates
            if not _matches_any(candidate.text, disabled_texts)
        ]
        ui_candidates = _ui_node_candidates(ui_nodes or [])
        candidates = ocr_candidates + ui_candidates
        visible_text = "\n".join(candidate.text for candidate in candidates)

        done_match = _first_keyword_match(visible_text, config["recognition"].get("done_keywords", []))
        if done_match:
            return {
                "action": "done",
                "x": None,
                "y": None,
                "label": done_match,
                "confidence": 0.9,
                "is_final_publish": False,
                "reason": "Google OCR/UI text indicates the upload flow is complete.",
                "provider": "google_ocr",
                "recognized_text": visible_text,
            }

        wait_match = _first_keyword_match(visible_text, config["recognition"].get("wait_keywords", []))
        if wait_match:
            return {
                "action": "wait",
                "x": None,
                "y": None,
                "label": wait_match,
                "confidence": 0.8,
                "is_final_publish": False,
                "reason": "Google OCR/UI text indicates the screen is busy.",
                "provider": "google_ocr",
                "recognized_text": visible_text,
            }

        target_decision = _choose_text_target(
            candidates=candidates,
            config=config,
            allow_publish=allow_publish,
        )
        if target_decision:
            target_decision["provider"] = "google_ocr"
            target_decision["recognized_text"] = visible_text
            return target_decision

        fallback_decision = _choose_fallback_target(
            visible_text=visible_text,
            screen_size=screen_size,
            config=config,
        )
        if fallback_decision:
            fallback_decision["provider"] = "google_ocr"
            fallback_decision["recognized_text"] = visible_text
            return fallback_decision

        return {
            "action": "manual_review",
            "x": None,
            "y": None,
            "label": "",
            "confidence": 0.0,
            "is_final_publish": False,
            "reason": "Google OCR did not find a configured button target.",
            "provider": "google_ocr",
            "recognized_text": visible_text,
        }

    def _detect_text(self, screenshot_path: Path) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "requests": [
                {
                    "image": {"content": _base64_file(screenshot_path)},
                    "features": [{"type": self.feature_type}],
                }
            ]
        }
        if self.language_hints:
            payload["requests"][0]["imageContext"] = {"languageHints": self.language_hints}
        query = urllib.parse.urlencode({"key": self.api_key})
        return self._post_json(f"/images:annotate?{query}", payload)

    def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            self.api_base + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", "replace")
            raise AIDecisionError(f"Google Vision API error {exc.code}: {error_body}") from exc
        except urllib.error.URLError as exc:
            raise AIDecisionError(f"Google Vision API request failed: {exc}") from exc
        data = json.loads(body)
        first_response = (data.get("responses") or [{}])[0]
        if "error" in first_response:
            raise AIDecisionError(f"Google Vision API error: {first_response['error']}")
        return data


class PaddleOCRButtonFinder:
    def __init__(self, config: dict[str, Any]) -> None:
        recognition_config = config.get("recognition", {})
        paddle_config = recognition_config.get("paddle", {})
        if not isinstance(paddle_config, dict):
            paddle_config = {}
        model_source = str(paddle_config.get("model_source") or "").strip()
        if model_source:
            os.environ.setdefault("PADDLE_PDX_MODEL_SOURCE", model_source)
        self.min_confidence = float(paddle_config.get("min_confidence", 0.0) or 0.0)
        self.use_angle_cls = bool(paddle_config.get("use_angle_cls", False))
        external_python = _resolve_external_paddle_python(paddle_config)
        if external_python:
            self.engine = _ExternalPaddleOCRRunner(external_python, paddle_config)
        else:
            self.engine = _get_paddle_ocr_engine(paddle_config)

    def decide(
        self,
        *,
        screenshot_path: Path,
        screen_size: tuple[int, int],
        ui_summary: str,
        ui_nodes: list[UINode] | None = None,
        config: dict[str, Any],
        remote_video_path: str,
        allow_publish: bool,
        step_number: int,
    ) -> dict[str, Any]:
        del ui_summary, remote_video_path, step_number
        ocr_candidates = self._detect_candidates(screenshot_path)
        disabled_texts = _disabled_ui_texts(ui_nodes or [])
        ocr_candidates = [
            candidate
            for candidate in ocr_candidates
            if not _matches_any(candidate.text, disabled_texts)
        ]
        ui_candidates = _ui_node_candidates(ui_nodes or [])
        candidates = ocr_candidates + ui_candidates
        visible_text = "\n".join(candidate.text for candidate in candidates)

        done_match = _first_keyword_match(visible_text, config["recognition"].get("done_keywords", []))
        if done_match:
            return {
                "action": "done",
                "x": None,
                "y": None,
                "label": done_match,
                "confidence": 0.9,
                "is_final_publish": False,
                "reason": "PaddleOCR/UI text indicates the upload flow is complete.",
                "provider": "paddle_ocr",
                "recognized_text": visible_text,
            }

        wait_match = _first_keyword_match(visible_text, config["recognition"].get("wait_keywords", []))
        if wait_match:
            return {
                "action": "wait",
                "x": None,
                "y": None,
                "label": wait_match,
                "confidence": 0.8,
                "is_final_publish": False,
                "reason": "PaddleOCR/UI text indicates the screen is busy.",
                "provider": "paddle_ocr",
                "recognized_text": visible_text,
            }

        target_decision = _choose_text_target(
            candidates=candidates,
            config=config,
            allow_publish=allow_publish,
        )
        if target_decision:
            target_decision["provider"] = "paddle_ocr"
            target_decision["recognized_text"] = visible_text
            return target_decision

        fallback_decision = _choose_fallback_target(
            visible_text=visible_text,
            screen_size=screen_size,
            config=config,
        )
        if fallback_decision:
            fallback_decision["provider"] = "paddle_ocr"
            fallback_decision["recognized_text"] = visible_text
            return fallback_decision

        return {
            "action": "manual_review",
            "x": None,
            "y": None,
            "label": "",
            "confidence": 0.0,
            "is_final_publish": False,
            "reason": "PaddleOCR did not find a configured button target.",
            "provider": "paddle_ocr",
            "recognized_text": visible_text,
        }

    def _detect_candidates(self, screenshot_path: Path) -> list[TextCandidate]:
        if isinstance(self.engine, _ExternalPaddleOCRRunner):
            candidates = self.engine.detect_candidates(screenshot_path)
        elif hasattr(self.engine, "predict"):
            raw_result = list(self.engine.predict(str(screenshot_path)))
            candidates = _parse_paddle_ocr_candidates(raw_result)
        else:
            try:
                raw_result = self.engine.ocr(str(screenshot_path), cls=self.use_angle_cls)
            except TypeError:
                raw_result = self.engine.ocr(str(screenshot_path))
            candidates = _parse_paddle_ocr_candidates(raw_result)
        if self.min_confidence <= 0:
            return candidates
        return [candidate for candidate in candidates if candidate.confidence >= self.min_confidence]


class _ExternalPaddleOCRRunner:
    def __init__(self, python_path: Path, paddle_config: dict[str, Any]) -> None:
        self.python_path = python_path
        self.paddle_config = {
            key: value
            for key, value in paddle_config.items()
            if key not in {"python", "python_path", "runtime_python", "external_python"}
        }
        self.timeout_seconds = int(paddle_config.get("external_timeout_seconds") or 120)

    def detect_candidates(self, screenshot_path: Path) -> list[TextCandidate]:
        payload = {
            "screenshot_path": str(Path(screenshot_path).resolve()),
            "paddle_config": self.paddle_config,
        }
        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join(
            [
                str(PROJECT_ROOT / "upload_system"),
                str(PROJECT_ROOT),
                env.get("PYTHONPATH", ""),
            ]
        )
        env.pop(EXTERNAL_PADDLE_OCR_PYTHON_ENV, None)
        result = subprocess.run(
            [str(self.python_path), str(PADDLE_OCR_WORKER)],
            input=json.dumps(payload, ensure_ascii=False),
            cwd=str(PROJECT_ROOT),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=self.timeout_seconds,
            check=False,
        )
        if result.returncode != 0:
            raise AIDecisionError(
                "External PaddleOCR worker failed: "
                f"python={self.python_path} returncode={result.returncode} "
                f"stderr={result.stderr.strip() or result.stdout.strip()}"
            )
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise AIDecisionError(f"External PaddleOCR worker returned non-JSON output: {result.stdout[:500]}") from exc
        if not data.get("ok"):
            raise AIDecisionError(str(data.get("error") or "External PaddleOCR worker failed without error text."))
        return [
            TextCandidate(
                text=str(item.get("text") or ""),
                x=int(item.get("x") or 0),
                y=int(item.get("y") or 0),
                left=int(item.get("left") or 0),
                top=int(item.get("top") or 0),
                right=int(item.get("right") or 0),
                bottom=int(item.get("bottom") or 0),
                confidence=float(item.get("confidence") or 0.0),
                source=str(item.get("source") or "ocr"),
            )
            for item in data.get("candidates", [])
        ]


class OpenAIButtonFinder:
    def __init__(self, config: dict[str, Any]) -> None:
        ai_config = config["ai"]
        self.model = ai_config["model"]
        self.api_base = ai_config.get("api_base", "https://api.openai.com/v1").rstrip("/")
        api_key_env = ai_config.get("api_key_env", "OPENAI_API_KEY")
        self.api_key = str(ai_config.get("api_key") or "") or os.environ.get(api_key_env, "")
        if not self.api_key:
            raise AIDecisionError(f"Missing API key environment variable: {api_key_env}")

    def decide(
        self,
        *,
        screenshot_path: Path,
        screen_size: tuple[int, int],
        ui_summary: str,
        ui_nodes: list[UINode] | None = None,
        config: dict[str, Any],
        remote_video_path: str,
        allow_publish: bool,
        step_number: int,
    ) -> dict[str, Any]:
        del ui_nodes
        prompt = _build_prompt(
            config=config,
            screen_size=screen_size,
            ui_summary=ui_summary,
            remote_video_path=remote_video_path,
            allow_publish=allow_publish,
            step_number=step_number,
        )
        payload = {
            "model": self.model,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {
                            "type": "input_image",
                            "image_url": _image_data_url(screenshot_path),
                        },
                    ],
                }
            ],
        }
        data = self._post_json("/responses", payload)
        output_text = _extract_output_text(data)
        decision = _parse_json_object(output_text)
        return _normalize_decision(decision)

    def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            self.api_base + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", "replace")
            raise AIDecisionError(f"OpenAI API error {exc.code}: {error_body}") from exc
        except urllib.error.URLError as exc:
            raise AIDecisionError(f"OpenAI API request failed: {exc}") from exc
        return json.loads(body)


def _get_paddle_ocr_engine(paddle_config: dict[str, Any]) -> Any:
    _prepare_local_paddle_runtime()
    try:
        from paddleocr import PaddleOCR
    except Exception as exc:
        raise AIDecisionError(
            "PaddleOCR is not installed. Install PaddlePaddle and PaddleOCR, for example: "
            "python -m pip install paddlepaddle==2.6.2 -i https://www.paddlepaddle.org.cn/packages/stable/cpu/ "
            "then python -m pip install paddleocr==2.8.1; "
            f"import error: {type(exc).__name__}: {exc}; "
            + _paddle_runtime_diagnostic()
        ) from exc

    uses_new_api = _uses_new_paddle_ocr_api(PaddleOCR)
    raw_options = _paddle_init_options(paddle_config)
    options = (
        _paddle_new_api_options(raw_options)
        if uses_new_api
        else _filter_paddle_init_options(PaddleOCR, raw_options)
    )
    cache_key = json.dumps(options, sort_keys=True, default=str)
    if cache_key in _PADDLE_OCR_ENGINE_CACHE:
        return _PADDLE_OCR_ENGINE_CACHE[cache_key]

    try:
        engine, options = _instantiate_paddle_ocr(PaddleOCR, options)
    except (AssertionError, TypeError, ValueError) as exc:
        if uses_new_api:
            raise AIDecisionError(f"PaddleOCR initialization failed with options {options!r}: {exc}") from exc
        legacy_options = _filter_paddle_init_options(PaddleOCR, _paddle_legacy_init_options(options, paddle_config))
        legacy_cache_key = json.dumps({"legacy": legacy_options}, sort_keys=True, default=str)
        if legacy_cache_key in _PADDLE_OCR_ENGINE_CACHE:
            return _PADDLE_OCR_ENGINE_CACHE[legacy_cache_key]
        try:
            engine, legacy_options = _instantiate_paddle_ocr(PaddleOCR, legacy_options)
        except (AssertionError, TypeError, ValueError) as legacy_exc:
            raise AIDecisionError(
                f"PaddleOCR initialization failed with options {options!r}; "
                f"legacy options {legacy_options!r}: {legacy_exc}"
            ) from exc
        _PADDLE_OCR_ENGINE_CACHE[legacy_cache_key] = engine
        return engine

    _PADDLE_OCR_ENGINE_CACHE[cache_key] = engine
    return engine


def _uses_new_paddle_ocr_api(paddle_ocr_cls: Any) -> bool:
    try:
        parameters = inspect.signature(paddle_ocr_cls).parameters
    except (TypeError, ValueError):
        return False
    return any(
        name in parameters
        for name in {
            "use_doc_orientation_classify",
            "use_doc_unwarping",
            "use_textline_orientation",
            "text_detection_model_dir",
            "text_recognition_model_dir",
        }
    )


def _paddle_new_api_options(options: dict[str, Any]) -> dict[str, Any]:
    supported_keys = {
        "lang",
        "ocr_version",
        "device",
        "enable_hpi",
        "use_tensorrt",
        "precision",
        "paddlex_config",
        "use_doc_orientation_classify",
        "use_doc_unwarping",
        "use_textline_orientation",
        "text_detection_model_dir",
        "text_recognition_model_dir",
        "textline_orientation_model_dir",
        "text_det_limit_side_len",
        "text_det_limit_type",
        "text_det_thresh",
        "text_det_box_thresh",
        "text_det_unclip_ratio",
        "text_rec_score_thresh",
        "text_recognition_batch_size",
    }
    return {
        key: value
        for key, value in options.items()
        if key in supported_keys and value is not None and value != ""
    }


def _filter_paddle_init_options(paddle_ocr_cls: Any, options: dict[str, Any]) -> dict[str, Any]:
    try:
        signature = inspect.signature(paddle_ocr_cls)
    except (TypeError, ValueError):
        return dict(options)
    parameters = signature.parameters
    if any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()):
        return dict(options)
    supported = {
        name
        for name, parameter in parameters.items()
        if parameter.kind
        in {
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        }
    }
    return {key: value for key, value in options.items() if key in supported}


def _instantiate_paddle_ocr(paddle_ocr_cls: Any, options: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    current = dict(options)
    removed_unknown: list[str] = []
    while True:
        try:
            return paddle_ocr_cls(**current), current
        except (AssertionError, TypeError, ValueError) as exc:
            unknown = _unknown_paddle_argument(exc)
            if unknown and unknown in current and unknown not in removed_unknown:
                removed_unknown.append(unknown)
                current.pop(unknown, None)
                continue
            raise


def _unknown_paddle_argument(exc: BaseException) -> str:
    match = re.search(r"Unknown argument:\s*([A-Za-z_][A-Za-z0-9_]*)", str(exc))
    return match.group(1) if match else ""


def _prepare_local_paddle_runtime() -> None:
    deps_dir = LOCAL_PADDLE_DEPS if LOCAL_PADDLE_DEPS.exists() else LOCAL_PADDLE_DEPS_ROOT
    if deps_dir.exists():
        deps_path = str(deps_dir)
        if deps_path not in sys.path:
            sys.path.insert(0, deps_path)
    LOCAL_PADDLE_CACHE.mkdir(parents=True, exist_ok=True)
    cache_path = str(LOCAL_PADDLE_CACHE)
    local_app_data = LOCAL_PADDLE_CACHE / "AppData" / "Local"
    roaming_app_data = LOCAL_PADDLE_CACHE / "AppData" / "Roaming"
    mpl_config = LOCAL_PADDLE_CACHE / "matplotlib"
    for path in (local_app_data, roaming_app_data, mpl_config):
        path.mkdir(parents=True, exist_ok=True)
    os.environ["PADDLE_HOME"] = str(LOCAL_PADDLE_CACHE / "paddle")
    os.environ["XDG_CACHE_HOME"] = str(LOCAL_PADDLE_CACHE / ".cache")
    os.environ["MPLCONFIGDIR"] = str(mpl_config)
    os.environ["LOCALAPPDATA"] = str(local_app_data)
    os.environ["APPDATA"] = str(roaming_app_data)
    os.environ["HOME"] = cache_path
    os.environ["USERPROFILE"] = cache_path
    user_site = local_app_data / "Python" / f"Python{sys.version_info.major}{sys.version_info.minor}" / "site-packages"
    user_site.mkdir(parents=True, exist_ok=True)
    site.USER_BASE = cache_path
    site.USER_SITE = str(user_site)
    existing_path = os.environ.get("path") or os.environ.get("PATH") or ""
    paddle_libs = deps_dir / "paddle" / "libs"
    runtime_paths = [
        str(Path(sys.executable).resolve().parent),
        str(deps_dir),
        str(paddle_libs),
    ]
    minimal_path = os.pathsep.join([*runtime_paths, existing_path]) if existing_path else os.pathsep.join(runtime_paths)
    os.environ["PATH"] = minimal_path
    os.environ["path"] = minimal_path
    if hasattr(os, "add_dll_directory") and paddle_libs.exists():
        os.add_dll_directory(str(paddle_libs))


def _resolve_external_paddle_python(paddle_config: dict[str, Any] | None = None) -> Path | None:
    paddle_config = paddle_config or {}
    value = str(
        paddle_config.get("python")
        or paddle_config.get("python_path")
        or paddle_config.get("runtime_python")
        or paddle_config.get("external_python")
        or os.environ.get(EXTERNAL_PADDLE_OCR_PYTHON_ENV, "")
    ).strip()
    if not value and not LOCAL_PADDLE_DEPS.exists() and DEFAULT_EXTERNAL_PADDLE_OCR_PYTHON.exists():
        value = str(DEFAULT_EXTERNAL_PADDLE_OCR_PYTHON)
    if not value:
        return None
    path = Path(value).expanduser()
    if not path.exists():
        raise AIDecisionError(f"Configured PaddleOCR Python does not exist: {path}")
    if path.resolve() == Path(sys.executable).resolve():
        return None
    return path


def paddle_ocr_runtime_available(paddle_config: dict[str, Any] | None = None) -> bool:
    try:
        if _resolve_external_paddle_python(paddle_config):
            return True
        _prepare_local_paddle_runtime()
        import importlib.util

        return importlib.util.find_spec("paddleocr") is not None and importlib.util.find_spec("paddle") is not None
    except Exception:
        return False


def _paddle_runtime_diagnostic() -> str:
    available_deps = sorted(path.name for path in LOCAL_PADDLE_DEPS_ROOT.glob("py*") if path.is_dir())
    models = {
        "det": LOCAL_PADDLE_CACHE / ".paddleocr" / "whl" / "det" / "ch" / "ch_PP-OCRv4_det_infer",
        "rec": LOCAL_PADDLE_CACHE / ".paddleocr" / "whl" / "rec" / "ch" / "ch_PP-OCRv4_rec_infer",
        "cls": LOCAL_PADDLE_CACHE / ".paddleocr" / "whl" / "cls" / "ch_ppocr_mobile_v2.0_cls_infer",
    }
    model_status = ", ".join(f"{name}={'present' if path.exists() else 'missing'}" for name, path in models.items())
    return (
        "PaddleOCR runtime diagnostic: "
        f"python={sys.version_info.major}.{sys.version_info.minor}, "
        f"executable={sys.executable}, "
        f"expected_deps={LOCAL_PADDLE_DEPS}, "
        f"expected_deps_exists={LOCAL_PADDLE_DEPS.exists()}, "
        f"available_deps={available_deps or []}, "
        f"{EXTERNAL_PADDLE_OCR_PYTHON_ENV}={os.environ.get(EXTERNAL_PADDLE_OCR_PYTHON_ENV, '') or '<unset>'}, "
        f"models=({model_status})."
    )


def paddle_ocr_worker_main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        screenshot_path = Path(str(payload["screenshot_path"]))
        paddle_config = payload.get("paddle_config") or {}
        if not isinstance(paddle_config, dict):
            paddle_config = {}
        with contextlib.redirect_stdout(sys.stderr):
            engine = _get_paddle_ocr_engine(paddle_config)
            if hasattr(engine, "predict"):
                raw_result = list(engine.predict(str(screenshot_path)))
            else:
                try:
                    raw_result = engine.ocr(str(screenshot_path), cls=bool(paddle_config.get("use_angle_cls", False)))
                except TypeError:
                    raw_result = engine.ocr(str(screenshot_path))
            candidates = _parse_paddle_ocr_candidates(raw_result)
        print(
            json.dumps(
                {"ok": True, "candidates": [asdict(candidate) for candidate in candidates]},
                ensure_ascii=False,
            )
        )
        return 0
    except Exception as exc:  # noqa: BLE001
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    "diagnostic": _paddle_runtime_diagnostic(),
                },
                ensure_ascii=False,
            )
        )
        return 1


def _paddle_init_options(paddle_config: dict[str, Any]) -> dict[str, Any]:
    defaults: dict[str, Any] = {
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
    supported_keys = {
        "lang",
        "ocr_version",
        "device",
        "use_gpu",
        "use_angle_cls",
        "show_log",
        "enable_hpi",
        "use_tensorrt",
        "precision",
        "enable_mkldnn",
        "cpu_threads",
        "paddlex_config",
        "use_doc_orientation_classify",
        "use_doc_unwarping",
        "use_textline_orientation",
        "text_detection_model_dir",
        "text_recognition_model_dir",
        "textline_orientation_model_dir",
        "text_det_limit_side_len",
        "text_det_limit_type",
        "text_det_thresh",
        "text_det_box_thresh",
        "text_det_unclip_ratio",
        "text_rec_score_thresh",
        "text_recognition_batch_size",
    }
    options = defaults | {
        key: value
        for key, value in paddle_config.items()
        if key in supported_keys and value is not None and value != ""
    }
    return options


def _paddle_legacy_init_options(options: dict[str, Any], paddle_config: dict[str, Any]) -> dict[str, Any]:
    device = str(options.get("device") or "").casefold()
    legacy_options: dict[str, Any] = {
        "lang": options.get("lang", "ch"),
        "use_angle_cls": bool(
            paddle_config.get("use_angle_cls", options.get("use_textline_orientation", False))
        ),
        "show_log": bool(paddle_config.get("show_log", False)),
    }
    if device:
        legacy_options["use_gpu"] = device.startswith("gpu")
    legacy_key_map = {
        "det_model_dir": "det_model_dir",
        "rec_model_dir": "rec_model_dir",
        "cls_model_dir": "cls_model_dir",
    }
    for source_key, target_key in legacy_key_map.items():
        value = paddle_config.get(source_key)
        if value:
            legacy_options[target_key] = value
    return legacy_options


def _base64_file(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def _image_data_url(path: Path) -> str:
    return f"data:image/png;base64,{_base64_file(path)}"


def _parse_google_ocr_candidates(data: dict[str, Any]) -> list[TextCandidate]:
    responses = data.get("responses") or []
    if not responses:
        return []
    annotations = responses[0].get("textAnnotations") or []
    candidates: list[TextCandidate] = []
    for annotation in annotations[1:]:
        text = str(annotation.get("description", "")).strip()
        vertices = annotation.get("boundingPoly", {}).get("vertices", [])
        bounds = _vertices_to_bounds(vertices)
        if not text or not bounds:
            continue
        left, top, right, bottom = bounds
        candidates.append(
            TextCandidate(
                text=text,
                x=(left + right) // 2,
                y=(top + bottom) // 2,
                left=left,
                top=top,
                right=right,
                bottom=bottom,
                confidence=float(annotation.get("score", 0.75) or 0.75),
                source="ocr",
            )
        )
    return candidates


def _vertices_to_bounds(vertices: list[dict[str, Any]]) -> tuple[int, int, int, int] | None:
    if not vertices:
        return None
    xs = [int(vertex.get("x", 0)) for vertex in vertices]
    ys = [int(vertex.get("y", 0)) for vertex in vertices]
    return min(xs), min(ys), max(xs), max(ys)


def _parse_paddle_ocr_candidates(raw_result: Any) -> list[TextCandidate]:
    data = _plain_data(raw_result)
    candidates = _parse_paddle_structured_candidates(data)
    if candidates:
        return candidates
    return _parse_paddle_legacy_candidates(data)


def _plain_data(value: Any) -> Any:
    if hasattr(value, "json"):
        json_value = getattr(value, "json")
        try:
            return _plain_data(json_value() if callable(json_value) else json_value)
        except TypeError:
            return _plain_data(json_value)
    if hasattr(value, "tolist"):
        return _plain_data(value.tolist())
    if isinstance(value, dict):
        return {str(key): _plain_data(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_data(item) for item in value]
    return value


def _parse_paddle_structured_candidates(data: Any) -> list[TextCandidate]:
    candidates: list[TextCandidate] = []
    seen: set[tuple[str, int, int, int, int]] = set()
    for result in _iter_dicts(data):
        result_data = result.get("res") if isinstance(result.get("res"), dict) else result
        texts = result_data.get("rec_texts")
        if not isinstance(texts, list):
            continue
        scores = result_data.get("rec_scores") if isinstance(result_data.get("rec_scores"), list) else []
        boxes = result_data.get("rec_boxes") if isinstance(result_data.get("rec_boxes"), list) else []
        polys = result_data.get("rec_polys") if isinstance(result_data.get("rec_polys"), list) else []
        for index, raw_text in enumerate(texts):
            text = str(raw_text or "").strip()
            if not text:
                continue
            box = boxes[index] if index < len(boxes) else polys[index] if index < len(polys) else None
            bounds = _paddle_box_to_bounds(box)
            if not bounds:
                continue
            left, top, right, bottom = bounds
            key = (text, left, top, right, bottom)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                TextCandidate(
                    text=text,
                    x=(left + right) // 2,
                    y=(top + bottom) // 2,
                    left=left,
                    top=top,
                    right=right,
                    bottom=bottom,
                    confidence=_float_at(scores, index, 0.75),
                    source="ocr",
                )
            )
    return candidates


def _iter_dicts(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        found.append(value)
        for item in value.values():
            found.extend(_iter_dicts(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_iter_dicts(item))
    return found


def _parse_paddle_legacy_candidates(data: Any) -> list[TextCandidate]:
    candidates: list[TextCandidate] = []
    for line in _iter_paddle_legacy_lines(data):
        box = line[0]
        recognition = line[1]
        text = str(recognition[0] if recognition else "").strip()
        if not text:
            continue
        bounds = _paddle_box_to_bounds(box)
        if not bounds:
            continue
        left, top, right, bottom = bounds
        candidates.append(
            TextCandidate(
                text=text,
                x=(left + right) // 2,
                y=(top + bottom) // 2,
                left=left,
                top=top,
                right=right,
                bottom=bottom,
                confidence=float(recognition[1] if len(recognition) > 1 else 0.75),
                source="ocr",
            )
        )
    return candidates


def _iter_paddle_legacy_lines(value: Any) -> list[list[Any]]:
    if _is_paddle_legacy_line(value):
        return [value]
    lines: list[list[Any]] = []
    if isinstance(value, list):
        for item in value:
            lines.extend(_iter_paddle_legacy_lines(item))
    return lines


def _is_paddle_legacy_line(value: Any) -> bool:
    if not isinstance(value, list) or len(value) < 2:
        return False
    recognition = value[1]
    return isinstance(recognition, list) and len(recognition) >= 1 and isinstance(recognition[0], str)


def _paddle_box_to_bounds(box: Any) -> tuple[int, int, int, int] | None:
    if box is None:
        return None
    value = _plain_data(box)
    if not isinstance(value, list) or not value:
        return None
    if len(value) == 4 and all(_is_number(item) for item in value):
        left, top, right, bottom = [int(round(float(item))) for item in value]
        return left, top, right, bottom
    if len(value) % 2 == 0 and all(_is_number(item) for item in value):
        points = list(zip(value[0::2], value[1::2]))
        return _points_to_bounds(points)
    points: list[tuple[float, float]] = []
    for point in value:
        if isinstance(point, list) and len(point) >= 2 and _is_number(point[0]) and _is_number(point[1]):
            points.append((float(point[0]), float(point[1])))
    return _points_to_bounds(points)


def _points_to_bounds(points: list[tuple[float, float]]) -> tuple[int, int, int, int] | None:
    if not points:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return (
        int(round(min(xs))),
        int(round(min(ys))),
        int(round(max(xs))),
        int(round(max(ys))),
    )


def _float_at(values: list[Any], index: int, default: float) -> float:
    if index >= len(values):
        return default
    try:
        return float(values[index])
    except (TypeError, ValueError):
        return default


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _ui_node_candidates(nodes: list[UINode]) -> list[TextCandidate]:
    candidates: list[TextCandidate] = []
    for node in nodes:
        if node.center_x is None or node.center_y is None:
            continue
        if node.enabled == "false":
            continue
        texts = [node.text, node.description]
        for text in texts:
            text = text.strip()
            if not text:
                continue
            bounds = _parse_bounds(node.bounds, node.center_x, node.center_y)
            candidates.append(
                TextCandidate(
                    text=text,
                    x=node.center_x,
                    y=node.center_y,
                    left=bounds[0],
                    top=bounds[1],
                    right=bounds[2],
                    bottom=bounds[3],
                    confidence=0.85 if node.clickable == "true" else 0.65,
                    source="ui",
                )
            )
    return candidates


def _disabled_ui_texts(nodes: list[UINode]) -> list[str]:
    texts: list[str] = []
    for node in nodes:
        if node.enabled != "false":
            continue
        texts.extend(text for text in (node.text, node.description) if text.strip())
    return texts


def _parse_bounds(bounds: str, fallback_x: int, fallback_y: int) -> tuple[int, int, int, int]:
    match = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds or "")
    if not match:
        return fallback_x, fallback_y, fallback_x, fallback_y
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def _choose_text_target(
    *,
    candidates: list[TextCandidate],
    config: dict[str, Any],
    allow_publish: bool,
) -> dict[str, Any] | None:
    targets = config["recognition"].get("click_targets", [])
    final_keywords = config["automation"].get("final_publish_keywords", [])
    for target in targets:
        target_texts = target.get("texts", [])
        best = _best_candidate_match(candidates, target_texts)
        if not best:
            continue
        candidate, matched_text = best
        is_final_publish = bool(target.get("final_publish")) or _matches_any(candidate.text, final_keywords)
        if is_final_publish and not allow_publish:
            return {
                "action": "manual_review",
                "x": candidate.x,
                "y": candidate.y,
                "label": candidate.text,
                "confidence": candidate.confidence,
                "is_final_publish": True,
                "reason": "OCR/UI found a final publish button; --allow-publish is not set.",
                "matched_text": matched_text,
                "source": candidate.source,
            }
        return {
            "action": "tap",
            "x": candidate.x,
            "y": candidate.y,
            "label": candidate.text,
            "confidence": candidate.confidence,
            "is_final_publish": is_final_publish,
            "reason": f"Matched configured OCR/UI target '{target.get('name', matched_text)}'.",
            "matched_text": matched_text,
            "source": candidate.source,
        }
    return None


def _best_candidate_match(
    candidates: list[TextCandidate],
    target_texts: list[str],
) -> tuple[TextCandidate, str] | None:
    matches: list[tuple[float, TextCandidate, str]] = []
    for candidate in candidates:
        candidate_text = _normalize_text(candidate.text)
        for target_text in target_texts:
            normalized_target = _normalize_text(str(target_text))
            if not normalized_target:
                continue
            if normalized_target in candidate_text:
                matches.append((_candidate_score(candidate, normalized_target), candidate, str(target_text)))
    if not matches:
        return None
    matches.sort(key=lambda item: item[0], reverse=True)
    return matches[0][1], matches[0][2]


def _candidate_score(candidate: TextCandidate, target_text: str) -> float:
    score = candidate.confidence
    if _normalize_text(candidate.text) == target_text:
        score += 0.3
    score += min(len(target_text), 20) / 100
    if candidate.source == "ui":
        score += 0.15
    score += min(candidate.bottom - candidate.top, 80) / 1000
    return score


def _choose_fallback_target(
    *,
    visible_text: str,
    screen_size: tuple[int, int],
    config: dict[str, Any],
) -> dict[str, Any] | None:
    fallbacks = config["recognition"].get("fallback_targets", [])
    for fallback in fallbacks:
        screen_hints = fallback.get("screen_hints", [])
        if screen_hints and not _matches_any(visible_text, screen_hints):
            continue
        x_ratio = float(fallback["x_ratio"])
        y_ratio = float(fallback["y_ratio"])
        x = int(screen_size[0] * x_ratio)
        y = int(screen_size[1] * y_ratio)
        return {
            "action": "tap",
            "x": x,
            "y": y,
            "label": fallback.get("name", "fallback"),
            "confidence": float(fallback.get("confidence", 0.45)),
            "is_final_publish": bool(fallback.get("final_publish", False)),
            "reason": "Used configured fallback coordinates after OCR/UI target matching failed.",
            "source": "fallback",
        }
    return None


def _first_keyword_match(text: str, keywords: list[str]) -> str:
    for keyword in keywords:
        if _normalize_text(str(keyword)) in _normalize_text(text):
            return str(keyword)
    return ""


def _matches_any(text: str, keywords: list[str]) -> bool:
    normalized = _normalize_text(text)
    return any(_normalize_text(str(keyword)) in normalized for keyword in keywords)


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", text).casefold()


def _extract_output_text(data: dict[str, Any]) -> str:
    if isinstance(data.get("output_text"), str):
        return data["output_text"]

    parts: list[str] = []
    for item in data.get("output", []):
        for content in item.get("content", []):
            if content.get("type") in {"output_text", "text"} and isinstance(content.get("text"), str):
                parts.append(content["text"])
    if parts:
        return "\n".join(parts)
    raise AIDecisionError("OpenAI response did not include output text.")


def _parse_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL | re.IGNORECASE)
    if fence:
        cleaned = fence.group(1).strip()
    if not cleaned.startswith("{"):
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            cleaned = cleaned[start : end + 1]
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise AIDecisionError(f"AI did not return valid JSON: {text}") from exc
    if not isinstance(value, dict):
        raise AIDecisionError(f"AI returned non-object JSON: {value!r}")
    return value


def _normalize_decision(decision: dict[str, Any]) -> dict[str, Any]:
    action = str(decision.get("action", "")).lower().strip()
    if action not in {"tap", "wait", "done", "manual_review"}:
        raise AIDecisionError(f"Unsupported AI action: {action!r}")
    normalized = {
        "action": action,
        "x": _optional_int(decision.get("x")),
        "y": _optional_int(decision.get("y")),
        "label": str(decision.get("label", ""))[:120],
        "confidence": float(decision.get("confidence", 0) or 0),
        "is_final_publish": bool(decision.get("is_final_publish", False)),
        "reason": str(decision.get("reason", ""))[:300],
    }
    if action == "tap" and (normalized["x"] is None or normalized["y"] is None):
        raise AIDecisionError(f"Tap action missing coordinates: {decision!r}")
    return normalized


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(round(float(value)))


def _build_prompt(
    *,
    config: dict[str, Any],
    screen_size: tuple[int, int],
    ui_summary: str,
    remote_video_path: str,
    allow_publish: bool,
    step_number: int,
) -> str:
    workflow = config["workflow"]
    known_steps = "\n".join(f"- {step}" for step in workflow.get("known_steps", []))
    final_policy = (
        "Final publishing is allowed in this run."
        if allow_publish
        else "Final publishing is NOT allowed. If the next best action is the final Post/Publish button, return manual_review."
    )
    return f"""
You are controlling an Android phone through ADB taps.
Return exactly one JSON object and no markdown.

Coordinate system:
- Screenshot width={screen_size[0]}, height={screen_size[1]}.
- Origin is top-left. x increases right, y increases down.

Objective:
{workflow.get("objective", "")}

Context:
{workflow.get("context", "")}
Remote video path pushed to phone: {remote_video_path or "(not pushed in this run)"}
Step number: {step_number}
{final_policy}

Expected high-level flow:
{known_steps}

Rules:
- Prefer visible enabled controls.
- If UI XML gives a relevant node with bounds, use that node center.
- If a media picker is visible, select the newest/recent video thumbnail, usually the first video.
- Do not change unrelated privacy, ads, monetization, effects, delete, discard, login, or settings controls.
- Do not tap the final Post/Publish/Share button unless final publishing is allowed.
- If the screen is loading, return wait.
- If upload/publish is complete, return done.
- If unsure or human review is needed, return manual_review.

UI XML summary:
{ui_summary or "(no UI XML available)"}

JSON shape:
{{
  "action": "tap" | "wait" | "done" | "manual_review",
  "x": 123,
  "y": 456,
  "label": "short visible label or target name",
  "confidence": 0.0,
  "is_final_publish": false,
  "reason": "short reason"
}}
""".strip()
