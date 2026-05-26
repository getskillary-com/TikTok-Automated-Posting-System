from __future__ import annotations

import json
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .routes import route_list
from .serializers import to_jsonable
from .service import ControlCenterService


class ControlCenterRequestHandler(BaseHTTPRequestHandler):
    service: ControlCenterService
    routes = route_list()

    server_version = "GroupControlCenter/0.1"

    def do_GET(self) -> None:  # noqa: N802
        self.handle_request("GET")

    def do_POST(self) -> None:  # noqa: N802
        self.handle_request("POST")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(HTTPStatus.NO_CONTENT)
        self.add_common_headers()
        self.end_headers()

    def handle_request(self, method: str) -> None:
        parsed = urlparse(self.path)
        query = flatten_query(parse_qs(parsed.query, keep_blank_values=True))
        body = self.read_request_body() if method in {"POST", "PUT", "PATCH"} else {}
        for route in self.routes:
            match = route.match(method, parsed.path)
            if not match:
                continue
            try:
                data = route.handler(self.service, query, body, match)
                self.write_json({"ok": True, "data": to_jsonable(data)}, status=HTTPStatus.OK)
            except KeyError as exc:
                self.write_json({"ok": False, "error": clean_error(exc)}, status=HTTPStatus.NOT_FOUND)
            except ValueError as exc:
                self.write_json({"ok": False, "error": clean_error(exc)}, status=HTTPStatus.BAD_REQUEST)
            except RuntimeError as exc:
                self.write_json({"ok": False, "error": clean_error(exc)}, status=HTTPStatus.CONFLICT)
            except Exception as exc:  # noqa: BLE001
                self.write_json({"ok": False, "error": clean_error(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        self.write_json({"ok": False, "error": f"Route not found: {method} {parsed.path}"}, status=HTTPStatus.NOT_FOUND)

    def read_request_body(self) -> dict[str, Any]:
        content_type = self.headers.get("Content-Type", "")
        if content_type.lower().startswith("multipart/form-data"):
            return self.read_multipart_body(content_type)
        return self.read_json_body()

    def read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        if not raw.strip():
            return {}
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("JSON body must be an object.")
        return data

    def read_multipart_body(self, content_type: str) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        boundary_match = re.search(r'boundary=(?:"([^"]+)"|([^;]+))', content_type, flags=re.IGNORECASE)
        if not boundary_match:
            raise ValueError("Multipart request is missing boundary.")
        boundary = (boundary_match.group(1) or boundary_match.group(2)).strip().encode("utf-8")
        raw = self.rfile.read(length)
        return parse_multipart(raw, boundary)

    def write_json(self, payload: dict[str, Any], *, status: HTTPStatus) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.add_common_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def add_common_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "no-store")

    def log_message(self, format: str, *args: object) -> None:
        print(f"{self.address_string()} - {format % args}")


def create_server(host: str, port: int, *, db_path: str | Path | None = None) -> ThreadingHTTPServer:
    class Handler(ControlCenterRequestHandler):
        pass

    Handler.service = ControlCenterService(db_path)
    Handler.service.start_usb_monitor()
    return ThreadingHTTPServer((host, port), Handler)


def serve(host: str = "127.0.0.1", port: int = 8766, *, db_path: str | Path | None = None) -> None:
    server = create_server(host, port, db_path=db_path)
    print(f"control_center listening on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("stopping control_center")
    finally:
        server.server_close()


def flatten_query(values: dict[str, list[str]]) -> dict[str, str]:
    return {key: items[-1] if items else "" for key, items in values.items()}


def clean_error(exc: Exception) -> str:
    text = str(exc)
    if text.startswith("'") and text.endswith("'"):
        return text[1:-1]
    return text


def parse_multipart(raw: bytes, boundary: bytes) -> dict[str, Any]:
    result: dict[str, Any] = {}
    marker = b"--" + boundary
    for part in raw.split(marker):
        if not part or part in {b"--", b"--\r\n"}:
            continue
        part = part.strip(b"\r\n")
        if part.endswith(b"--"):
            part = part[:-2].rstrip(b"\r\n")
        header_blob, separator, body = part.partition(b"\r\n\r\n")
        if not separator:
            continue
        headers = parse_part_headers(header_blob)
        disposition = headers.get("content-disposition", "")
        name = disposition_param(disposition, "name")
        if not name:
            continue
        filename = disposition_param(disposition, "filename")
        if filename:
            result[name] = {
                "filename": filename,
                "content_type": headers.get("content-type", "application/octet-stream"),
                "content": body,
            }
        else:
            result[name] = body.decode("utf-8", errors="replace")
    return result


def parse_part_headers(header_blob: bytes) -> dict[str, str]:
    headers: dict[str, str] = {}
    for line in header_blob.decode("utf-8", errors="replace").split("\r\n"):
        key, separator, value = line.partition(":")
        if separator:
            headers[key.strip().lower()] = value.strip()
    return headers


def disposition_param(disposition: str, name: str) -> str:
    pattern = rf'{re.escape(name)}=(?:"([^"]*)"|([^;]*))'
    match = re.search(pattern, disposition)
    if not match:
        return ""
    return (match.group(1) if match.group(1) is not None else match.group(2) or "").strip()
