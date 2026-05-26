from __future__ import annotations

import cgi
import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .portal import SharedDrivePortal, UploadedFile
from .static import ACCOUNT_HTML, INDEX_HTML


class SharedDriveWebHandler(BaseHTTPRequestHandler):
    portal: SharedDrivePortal
    access_token: str = ""
    max_request_bytes: int = 2 * 1024 * 1024 * 1024

    server_version = "SharedDriveWeb/0.1"

    def do_GET(self) -> None:  # noqa: N802
        self.handle_get()

    def do_POST(self) -> None:  # noqa: N802
        self.handle_post()

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(HTTPStatus.NO_CONTENT)
        self.add_common_headers()
        self.end_headers()

    def handle_get(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/":
                self.write_html(INDEX_HTML)
                return
            if parsed.path == "/accounts":
                self.write_html(ACCOUNT_HTML)
                return
            if not self.authorized(parsed):
                self.write_json({"ok": False, "error": "unauthorized"}, status=HTTPStatus.UNAUTHORIZED)
                return
            if parsed.path == "/api/health":
                self.write_json({"ok": True, "data": self.portal.health()}, status=HTTPStatus.OK)
                return
            if parsed.path == "/api/phones":
                self.write_json({"ok": True, "data": self.portal.phones()}, status=HTTPStatus.OK)
                return
            if parsed.path == "/api/batches":
                query = flatten_query(parse_qs(parsed.query, keep_blank_values=True))
                limit = parse_int(query.get("limit", ""), default=100)
                self.write_json({"ok": True, "data": self.portal.list_batches(limit=limit)}, status=HTTPStatus.OK)
                return
            if parsed.path.startswith("/api/batches/"):
                batch_id = unquote(parsed.path.removeprefix("/api/batches/"))
                self.write_json({"ok": True, "data": self.portal.get_batch(batch_id)}, status=HTTPStatus.OK)
                return
            if parsed.path.startswith("/templates/"):
                name = Path(unquote(parsed.path.removeprefix("/templates/"))).name
                self.write_file(self.portal.template_path(name), download_name=name)
                return
            if parsed.path.startswith("/status/") and parsed.path.endswith("/status.xlsx"):
                batch_id = unquote(parsed.path.removeprefix("/status/").removesuffix("/status.xlsx"))
                self.write_file(self.portal.status_path(batch_id), download_name=f"{batch_id}-status.xlsx")
                return
            self.write_json({"ok": False, "error": "route not found"}, status=HTTPStatus.NOT_FOUND)
        except KeyError as exc:
            self.write_json({"ok": False, "error": clean_error(exc)}, status=HTTPStatus.NOT_FOUND)
        except ValueError as exc:
            self.write_json({"ok": False, "error": clean_error(exc)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as exc:  # noqa: BLE001
            self.write_json({"ok": False, "error": clean_error(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def handle_post(self) -> None:
        parsed = urlparse(self.path)
        try:
            if not self.authorized(parsed):
                self.write_json({"ok": False, "error": "unauthorized"}, status=HTTPStatus.UNAUTHORIZED)
                return
            if parsed.path == "/api/upload":
                data = self.read_upload_form()
                result = self.portal.upload_batch(
                    batch_id=str(data.get("batch_id") or ""),
                    tasks_file=data["tasks_file"],
                    video_files=data["video_files"],
                    auto_import=str(data.get("auto_import") or "1") != "0",
                )
                self.write_json({"ok": True, "data": result}, status=HTTPStatus.OK)
                return
            if parsed.path == "/api/tasks":
                data = self.read_task_form()
                result = self.portal.submit_task(
                    phone_id=str(data.get("phone_id") or ""),
                    scheduled_at=str(data.get("scheduled_at") or ""),
                    caption=str(data.get("caption") or ""),
                    note=str(data.get("note") or ""),
                    product_search_title=str(data.get("product_search_title") or ""),
                    product_publish_name=str(data.get("product_publish_name") or ""),
                    video_file=data["video_file"],
                )
                self.write_json({"ok": True, "data": result}, status=HTTPStatus.OK)
                return
            if parsed.path == "/api/phones/sync":
                self.write_json({"ok": True, "data": self.portal.sync_phone_names_now()}, status=HTTPStatus.OK)
                return
            if parsed.path.startswith("/api/phones/") and parsed.path.endswith("/account"):
                phone_id = unquote(parsed.path.removeprefix("/api/phones/").removesuffix("/account"))
                body = self.read_json_body(max_bytes=64 * 1024)
                self.write_json(
                    {
                        "ok": True,
                        "data": self.portal.update_phone_account(
                            phone_id=phone_id,
                            account_name=str(body.get("account_name") or body.get("account") or ""),
                            account_type=str(body.get("account_type") or ""),
                        ),
                    },
                    status=HTTPStatus.OK,
                )
                return
            if parsed.path == "/api/scan":
                result = self.portal.importer.scan_once(export_status=True)
                self.write_json({"ok": True, "data": result.__dict__}, status=HTTPStatus.OK)
                return
            self.write_json({"ok": False, "error": "route not found"}, status=HTTPStatus.NOT_FOUND)
        except KeyError as exc:
            self.write_json({"ok": False, "error": clean_error(exc)}, status=HTTPStatus.BAD_REQUEST)
        except ValueError as exc:
            self.write_json({"ok": False, "error": clean_error(exc)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as exc:  # noqa: BLE001
            self.write_json({"ok": False, "error": clean_error(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def authorized(self, parsed: Any) -> bool:
        if not self.access_token:
            return True
        header_token = self.headers.get("X-Upload-Token", "")
        query_token = flatten_query(parse_qs(parsed.query, keep_blank_values=True)).get("token", "")
        cookie_token = ""
        for item in self.headers.get("Cookie", "").split(";"):
            key, separator, value = item.strip().partition("=")
            if separator and key == "upload_token":
                cookie_token = value
                break
        return self.access_token in {header_token, query_token, cookie_token}

    def read_upload_form(self) -> dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", "0") or "0")
        if content_length <= 0:
            raise ValueError("empty upload request")
        if content_length > self.max_request_bytes:
            raise ValueError("upload is larger than server limit")
        content_type = self.headers.get("Content-Type", "")
        if not content_type.lower().startswith("multipart/form-data"):
            raise ValueError("upload must use multipart/form-data")
        form = cgi.FieldStorage(
            fp=self.rfile,
            headers=self.headers,
            environ={
                "REQUEST_METHOD": "POST",
                "CONTENT_TYPE": content_type,
                "CONTENT_LENGTH": str(content_length),
            },
            keep_blank_values=True,
        )
        tasks_item = first_file(form, "tasks")
        if tasks_item is None:
            tasks_item = first_file(form, "tasks_xlsx")
        if tasks_item is None:
            raise ValueError("tasks.xlsx is required")
        videos = file_list(form, "videos")
        return {
            "_form": form,
            "batch_id": first_text(form, "batch_id"),
            "auto_import": first_text(form, "auto_import") or "1",
            "tasks_file": UploadedFile(filename=str(tasks_item.filename or "tasks.xlsx"), stream=tasks_item.file),
            "video_files": [UploadedFile(filename=str(item.filename or ""), stream=item.file) for item in videos],
        }

    def read_task_form(self) -> dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", "0") or "0")
        if content_length <= 0:
            raise ValueError("empty upload request")
        if content_length > self.max_request_bytes:
            raise ValueError("upload is larger than server limit")
        content_type = self.headers.get("Content-Type", "")
        if not content_type.lower().startswith("multipart/form-data"):
            raise ValueError("upload must use multipart/form-data")
        form = cgi.FieldStorage(
            fp=self.rfile,
            headers=self.headers,
            environ={
                "REQUEST_METHOD": "POST",
                "CONTENT_TYPE": content_type,
                "CONTENT_LENGTH": str(content_length),
            },
            keep_blank_values=True,
        )
        video_item = first_file(form, "video")
        if video_item is None:
            raise ValueError("video is required")
        if first_text(form, "product_link") or first_text(form, "product_name"):
            raise ValueError("/api/tasks does not accept product_link or product_name")
        return {
            "_form": form,
            "phone_id": first_text(form, "phone_id"),
            "scheduled_at": first_text(form, "scheduled_at"),
            "caption": first_text(form, "caption"),
            "note": first_text(form, "note"),
            "product_search_title": first_text(form, "product_search_title"),
            "product_publish_name": first_text(form, "product_publish_name"),
            "video_file": UploadedFile(filename=str(video_item.filename or ""), stream=video_item.file),
        }

    def read_json_body(self, *, max_bytes: int = 1024 * 1024) -> dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", "0") or "0")
        if content_length <= 0:
            return {}
        if content_length > max_bytes:
            raise ValueError("request body is too large")
        content_type = self.headers.get("Content-Type", "")
        if content_type and "application/json" not in content_type.lower():
            raise ValueError("request must use application/json")
        raw = self.rfile.read(content_length).decode("utf-8")
        try:
            data = json.loads(raw or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError("invalid json body") from exc
        if not isinstance(data, dict):
            raise ValueError("json body must be an object")
        return data

    def write_html(self, html: str) -> None:
        encoded = html.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.add_common_headers()
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def write_json(self, payload: dict[str, Any], *, status: HTTPStatus) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.add_common_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def write_file(self, path: Path, *, download_name: str) -> None:
        size = path.stat().st_size
        self.send_response(HTTPStatus.OK)
        self.add_common_headers()
        self.send_header("Content-Type", mimetypes.guess_type(str(path))[0] or "application/octet-stream")
        self.send_header("Content-Length", str(size))
        self.send_header("Content-Disposition", f'attachment; filename="{download_name}"')
        self.end_headers()
        with path.open("rb") as file:
            shutil_copy(file, self.wfile)

    def add_common_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Upload-Token")
        self.send_header("Cache-Control", "no-store")

    def log_message(self, format: str, *args: object) -> None:
        print(f"{self.address_string()} - {format % args}")


def create_server(
    host: str = "127.0.0.1",
    port: int = 8080,
    *,
    share_root: str | Path | None = None,
    db_path: str | Path | None = None,
    timezone: str = "America/Sao_Paulo",
    access_token: str = "",
    max_request_mb: int = 2048,
    phone_sync_enabled: bool = False,
    phone_sync_seconds: int = 60,
    phone_full_sync_seconds: int = 300,
) -> ThreadingHTTPServer:
    class Handler(SharedDriveWebHandler):
        pass

    kwargs: dict[str, Any] = {}
    if share_root is not None:
        kwargs["share_root"] = share_root
    Handler.portal = SharedDrivePortal(
        db_path=db_path,
        timezone=timezone,
        phone_sync_enabled=phone_sync_enabled,
        phone_sync_seconds=phone_sync_seconds,
        phone_full_sync_seconds=phone_full_sync_seconds,
        **kwargs,
    )
    Handler.portal.start_phone_sync()
    Handler.access_token = access_token
    Handler.max_request_bytes = max(1, max_request_mb) * 1024 * 1024
    return ThreadingHTTPServer((host, port), Handler)


def serve(
    host: str = "127.0.0.1",
    port: int = 8080,
    *,
    share_root: str | Path | None = None,
    db_path: str | Path | None = None,
    timezone: str = "America/Sao_Paulo",
    access_token: str = "",
    max_request_mb: int = 2048,
    phone_sync_enabled: bool = True,
    phone_sync_seconds: int = 60,
    phone_full_sync_seconds: int = 300,
) -> None:
    server = create_server(
        host=host,
        port=port,
        share_root=share_root,
        db_path=db_path,
        timezone=timezone,
        access_token=access_token,
        max_request_mb=max_request_mb,
        phone_sync_enabled=phone_sync_enabled,
        phone_sync_seconds=phone_sync_seconds,
        phone_full_sync_seconds=phone_full_sync_seconds,
    )
    print(f"shared_drive_web listening on http://{host}:{server.server_address[1]}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("stopping shared_drive_web")
    finally:
        server.server_close()


def first_text(form: cgi.FieldStorage, name: str) -> str:
    item = form[name] if name in form else None
    if item is None:
        return ""
    if isinstance(item, list):
        item = item[0] if item else None
    if item is None or getattr(item, "filename", None):
        return ""
    return str(item.value or "").strip()


def first_file(form: cgi.FieldStorage, name: str) -> Any | None:
    items = file_list(form, name)
    return items[0] if items else None


def file_list(form: cgi.FieldStorage, name: str) -> list[Any]:
    if name not in form:
        return []
    raw = form[name]
    items = raw if isinstance(raw, list) else [raw]
    return [item for item in items if getattr(item, "filename", None)]


def parse_int(value: object, *, default: int) -> int:
    try:
        text = str(value or "").strip()
        return int(text) if text else default
    except ValueError:
        return default


def flatten_query(values: dict[str, list[str]]) -> dict[str, str]:
    return {key: items[-1] if items else "" for key, items in values.items()}


def clean_error(exc: Exception) -> str:
    text = str(exc)
    if text.startswith("'") and text.endswith("'"):
        return text[1:-1]
    return text


def shutil_copy(source: Any, target: Any) -> None:
    while True:
        chunk = source.read(1024 * 1024)
        if not chunk:
            return
        target.write(chunk)
