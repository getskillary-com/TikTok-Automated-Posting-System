from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from .service import ControlCenterService

Handler = Callable[[ControlCenterService, dict[str, Any], dict[str, Any], re.Match[str]], Any]


@dataclass(frozen=True)
class Route:
    method: str
    pattern: str
    handler: Handler
    description: str

    def match(self, method: str, path: str) -> re.Match[str] | None:
        if method != self.method:
            return None
        return re.fullmatch(self.pattern, path)


def route_list() -> list[Route]:
    return [
        Route("GET", r"/api/health", lambda svc, q, b, m: svc.health(), "System health"),
        Route("GET", r"/api/summary", lambda svc, q, b, m: svc.summary_counts(), "Summary counts"),
        Route("GET", r"/api/routes", lambda svc, q, b, m: public_routes(), "API routes"),
        Route("GET", r"/api/videos", lambda svc, q, b, m: svc.list_videos(q), "List videos"),
        Route("POST", r"/api/videos/import", lambda svc, q, b, m: svc.import_video(b), "Import video by path"),
        Route("POST", r"/api/videos/upload", lambda svc, q, b, m: svc.import_uploaded_video(b), "Upload video file"),
        Route("POST", r"/api/videos/choose-folder", lambda svc, q, b, m: svc.choose_video_folder(b), "Choose local video folder"),
        Route("POST", r"/api/videos/scan-folder", lambda svc, q, b, m: svc.scan_video_folder(b), "Scan video folder"),
        Route("POST", r"/api/videos/remove-batch", lambda svc, q, b, m: svc.remove_videos(b), "Remove videos in batch"),
        Route("GET", r"/api/videos/(?P<video_id>[^/]+)", lambda svc, q, b, m: svc.get_video(m.group("video_id")), "Video detail"),
        Route("POST", r"/api/videos/(?P<video_id>[^/]+)/status", lambda svc, q, b, m: svc.update_video_status(m.group("video_id"), b), "Update video status"),
        Route("POST", r"/api/videos/(?P<video_id>[^/]+)/remove", lambda svc, q, b, m: svc.remove_video(m.group("video_id")), "Remove video"),
        Route("GET", r"/api/captions", lambda svc, q, b, m: svc.list_captions(q), "List captions"),
        Route("POST", r"/api/captions", lambda svc, q, b, m: svc.add_caption(b), "Add caption"),
        Route("POST", r"/api/captions/import", lambda svc, q, b, m: svc.import_captions(b), "Import captions"),
        Route("POST", r"/api/captions/bind-video", lambda svc, q, b, m: svc.bind_caption_video(b), "Bind caption to video"),
        Route("POST", r"/api/captions/remove-batch", lambda svc, q, b, m: svc.remove_captions(b), "Remove captions in batch"),
        Route("GET", r"/api/caption-bindings", lambda svc, q, b, m: svc.list_caption_bindings(q), "List caption/video bindings"),
        Route("GET", r"/api/captions/(?P<caption_id>[^/]+)", lambda svc, q, b, m: svc.get_caption(m.group("caption_id")), "Caption detail"),
        Route("POST", r"/api/captions/(?P<caption_id>[^/]+)/edit", lambda svc, q, b, m: svc.edit_caption(m.group("caption_id"), b), "Edit caption"),
        Route("POST", r"/api/captions/(?P<caption_id>[^/]+)/status", lambda svc, q, b, m: svc.update_caption_status(m.group("caption_id"), b), "Update caption status"),
        Route("POST", r"/api/captions/(?P<caption_id>[^/]+)/remove", lambda svc, q, b, m: svc.remove_caption(m.group("caption_id")), "Remove caption"),
        Route("GET", r"/api/products", lambda svc, q, b, m: svc.list_products(q), "List products"),
        Route("POST", r"/api/products", lambda svc, q, b, m: svc.add_product(b), "Add product"),
        Route("GET", r"/api/products/(?P<product_id>[^/]+)", lambda svc, q, b, m: svc.get_product(m.group("product_id")), "Product detail"),
        Route("POST", r"/api/products/(?P<product_id>[^/]+)/edit", lambda svc, q, b, m: svc.edit_product(m.group("product_id"), b), "Edit product"),
        Route("POST", r"/api/products/(?P<product_id>[^/]+)/status", lambda svc, q, b, m: svc.update_product_status(m.group("product_id"), b), "Update product status"),
        Route("POST", r"/api/products/(?P<product_id>[^/]+)/remove", lambda svc, q, b, m: svc.remove_product(m.group("product_id")), "Remove product"),
        Route("GET", r"/api/phones", lambda svc, q, b, m: svc.list_phones(q), "List phones"),
        Route("POST", r"/api/phones", lambda svc, q, b, m: svc.add_phone(b), "Add phone"),
        Route("POST", r"/api/phones/health-all", lambda svc, q, b, m: svc.phone_health_all(), "Check all phone health"),
        Route("POST", r"/api/phones/usb-refresh", lambda svc, q, b, m: svc.refresh_registered_usb_devices(), "Refresh registered USB phones"),
        Route("GET", r"/api/phones/usb-monitor", lambda svc, q, b, m: svc.usb_monitor_status(), "Registered USB monitor status"),
        Route("GET", r"/api/phones/(?P<phone_id>[^/]+)", lambda svc, q, b, m: svc.get_phone(m.group("phone_id")), "Phone detail"),
        Route("POST", r"/api/phones/(?P<phone_id>[^/]+)/edit", lambda svc, q, b, m: svc.edit_phone(m.group("phone_id"), b), "Edit phone"),
        Route("POST", r"/api/phones/(?P<phone_id>[^/]+)/status", lambda svc, q, b, m: svc.update_phone_status(m.group("phone_id"), b), "Update phone status"),
        Route("POST", r"/api/phones/(?P<phone_id>[^/]+)/remove", lambda svc, q, b, m: svc.remove_phone(m.group("phone_id")), "Remove phone"),
        Route("POST", r"/api/phones/(?P<phone_id>[^/]+)/health", lambda svc, q, b, m: svc.phone_health(m.group("phone_id")), "Check phone health"),
        Route("GET", r"/api/phones/(?P<phone_id>[^/]+)/checks", lambda svc, q, b, m: svc.phone_checks(m.group("phone_id"), q), "Phone health check history"),
        Route("POST", r"/api/phones/(?P<phone_id>[^/]+)/checks", lambda svc, q, b, m: svc.phone_checks(m.group("phone_id"), b), "Phone health check history"),
        Route("GET", r"/api/adb/status", lambda svc, q, b, m: svc.adb_status(), "ADB status"),
        Route("GET", r"/api/adb/devices", lambda svc, q, b, m: svc.adb_devices(q), "ADB devices"),
        Route("POST", r"/api/adb/pair-wireless/qr/start", lambda svc, q, b, m: svc.wireless_pair_qr_start(b), "Start wireless ADB QR pairing"),
        Route("POST", r"/api/adb/pair-wireless/qr/complete", lambda svc, q, b, m: svc.wireless_pair_qr_complete(b), "Complete wireless ADB QR pairing"),
        Route("POST", r"/api/adb/pair-wireless", lambda svc, q, b, m: svc.pair_wireless(b), "Pair wireless ADB"),
        Route("POST", r"/api/adb/connect-wireless", lambda svc, q, b, m: svc.connect_wireless(b), "Connect wireless ADB"),
        Route("GET", r"/api/tasks", lambda svc, q, b, m: svc.list_tasks(q), "List release tasks"),
        Route("POST", r"/api/tasks", lambda svc, q, b, m: svc.create_task(b), "Create release task"),
        Route("POST", r"/api/tasks/from-binding", lambda svc, q, b, m: svc.create_task_from_binding(b), "Create task from binding"),
        Route("POST", r"/api/tasks/import-csv", lambda svc, q, b, m: svc.import_tasks_csv(b), "Import tasks from CSV"),
        Route("POST", r"/api/tasks/remove-batch", lambda svc, q, b, m: svc.remove_published_tasks(b), "Remove tasks in batch"),
        Route("GET", r"/api/tasks/(?P<task_id>[^/]+)", lambda svc, q, b, m: svc.get_task(m.group("task_id")), "Task detail"),
        Route("POST", r"/api/tasks/(?P<task_id>[^/]+)/reschedule", lambda svc, q, b, m: svc.reschedule_task(m.group("task_id"), b), "Reschedule task"),
        Route("POST", r"/api/tasks/(?P<task_id>[^/]+)/cancel", lambda svc, q, b, m: svc.cancel_task(m.group("task_id"), b), "Cancel task"),
        Route("POST", r"/api/tasks/(?P<task_id>[^/]+)/requeue", lambda svc, q, b, m: svc.requeue_task(m.group("task_id"), b), "Requeue task"),
        Route("POST", r"/api/tasks/(?P<task_id>[^/]+)/status", lambda svc, q, b, m: svc.update_task_status(m.group("task_id"), b), "Update task status"),
        Route("POST", r"/api/tasks/(?P<task_id>[^/]+)/assign-phone", lambda svc, q, b, m: svc.assign_task_phone(m.group("task_id"), b), "Assign task phone"),
        Route("GET", r"/api/queue/preview", lambda svc, q, b, m: svc.queue_preview(q), "Queue preview"),
        Route("POST", r"/api/queue/run-once", lambda svc, q, b, m: svc.queue_run_once(b), "Run one queue task"),
        Route("POST", r"/api/queue/claim-next", lambda svc, q, b, m: svc.queue_claim_next(b), "Claim next task"),
        Route("POST", r"/api/queue/workers/start", lambda svc, q, b, m: svc.queue_workers_start(b), "Start serial workers per phone"),
        Route("GET", r"/api/queue/workers/status", lambda svc, q, b, m: svc.queue_workers_status(q), "Queue worker status"),
        Route("POST", r"/api/queue/workers/stop", lambda svc, q, b, m: svc.queue_workers_stop(b), "Stop queue workers"),
        Route("POST", r"/api/queue/workers/recover-stale", lambda svc, q, b, m: svc.queue_workers_recover_stale(b), "Recover stale workers"),
        Route("GET", r"/api/logs", lambda svc, q, b, m: svc.list_logs(q), "List logs"),
        Route("GET", r"/api/logs/failures", lambda svc, q, b, m: svc.list_failures(q), "List failure logs"),
        Route("GET", r"/api/logs/latest", lambda svc, q, b, m: svc.latest_log(q), "Latest log"),
        Route("GET", r"/api/logs/task/(?P<task_id>[^/]+)", lambda svc, q, b, m: svc.task_report(m.group("task_id")), "Task log report"),
        Route("POST", r"/api/logs/remove-batch", lambda svc, q, b, m: svc.remove_logs(b), "Remove logs in batch"),
        Route("GET", r"/api/logs/(?P<log_id>[^/]+)/attempts", lambda svc, q, b, m: svc.log_attempts(m.group("log_id"), q), "Log attempts"),
        Route("GET", r"/api/logs/(?P<log_id>[^/]+)/artifacts", lambda svc, q, b, m: svc.log_artifacts(m.group("log_id"), q), "Log artifacts"),
        Route("GET", r"/api/logs/(?P<log_id>[^/]+)/tail", lambda svc, q, b, m: svc.log_tail(m.group("log_id"), q), "Log tail"),
        Route("GET", r"/api/logs/(?P<log_id>[^/]+)/artifact", lambda svc, q, b, m: svc.log_artifact(m.group("log_id"), q), "Read log artifact"),
        Route("GET", r"/api/logs/(?P<log_id>[^/]+)", lambda svc, q, b, m: svc.get_log(m.group("log_id"), q), "Log detail"),
        Route("GET", r"/api/settings/ocr", lambda svc, q, b, m: svc.get_ocr_settings(), "OCR settings"),
        Route("POST", r"/api/settings/ocr", lambda svc, q, b, m: svc.update_ocr_settings(b), "Update OCR settings"),
        Route("GET", r"/api/upload/config", lambda svc, q, b, m: svc.get_upload_config(), "Upload config"),
        Route("POST", r"/api/upload/config", lambda svc, q, b, m: svc.update_upload_config(b), "Update upload config"),
        Route("GET", r"/api/upload/caption-presets", lambda svc, q, b, m: svc.get_caption_presets(), "Caption presets"),
        Route("POST", r"/api/upload/caption-presets", lambda svc, q, b, m: svc.update_caption_presets(b), "Update caption presets"),
        Route("POST", r"/api/upload/doctor", lambda svc, q, b, m: svc.upload_doctor(b), "Upload system doctor"),
        Route("POST", r"/api/upload/push", lambda svc, q, b, m: svc.upload_push(b), "Push video to phone"),
        Route("POST", r"/api/upload/run", lambda svc, q, b, m: svc.upload_run(b), "Run upload system directly"),
    ]


def public_routes() -> list[dict[str, str]]:
    return [
        {"method": route.method, "path": route.pattern, "description": route.description}
        for route in route_list()
        if route.pattern != r"/api/routes"
    ]
