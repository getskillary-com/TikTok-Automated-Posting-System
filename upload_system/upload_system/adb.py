from __future__ import annotations

import os
import re
import shlex
import subprocess
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ANDROID_HOME = PROJECT_ROOT / "storage" / "android_home"
DEFAULT_ADB_SERVER_PORT = "5037"


class ADBError(RuntimeError):
    pass


def no_window_subprocess_kwargs() -> dict[str, int]:
    if os.name != "nt":
        return {}
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}


@dataclass(frozen=True)
class UINode:
    index: int
    text: str
    description: str
    resource_id: str
    class_name: str
    clickable: str
    enabled: str
    bounds: str
    center_x: int | None
    center_y: int | None

    def summary(self) -> str:
        center = ""
        if self.center_x is not None and self.center_y is not None:
            center = f" center=({self.center_x},{self.center_y})"
        return (
            f"[{self.index}] text={self.text!r} desc={self.description!r} "
            f"id={self.resource_id!r} class={self.class_name!r} "
            f"clickable={self.clickable} enabled={self.enabled} "
            f"bounds={self.bounds!r}{center}"
        )


class ADB:
    def __init__(self, adb_path: str = "adb", serial: str = "") -> None:
        self.adb_path = adb_path
        self.serial = serial

    def _base(self) -> list[str]:
        args = [self.adb_path, "-P", adb_server_port()]
        if self.serial:
            args.extend(["-s", self.serial])
        return args

    def run(
        self,
        args: list[str],
        *,
        check: bool = True,
        text: bool = True,
        timeout: int = 60,
    ) -> subprocess.CompletedProcess:
        command = self._base() + args
        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=text,
                encoding="utf-8" if text else None,
                errors="replace" if text else None,
                timeout=timeout,
                env=adb_env(),
                **no_window_subprocess_kwargs(),
            )
        except FileNotFoundError as exc:
            raise ADBError(f"ADB executable not found: {self.adb_path}") from exc
        except subprocess.TimeoutExpired as exc:
            raise ADBError(f"ADB command timed out: {' '.join(command)}") from exc

        if check and result.returncode != 0:
            stderr = result.stderr if isinstance(result.stderr, str) else result.stderr.decode("utf-8", "replace")
            raise ADBError(f"ADB command failed: {' '.join(command)}\n{stderr.strip()}")
        return result

    def exec_bytes(self, args: list[str], *, timeout: int = 60) -> bytes:
        result = self.run(args, text=False, timeout=timeout)
        return result.stdout

    def version(self) -> str:
        return self.run(["version"]).stdout.strip()

    def devices(self) -> list[str]:
        output = self.run(["devices"]).stdout
        devices: list[str] = []
        for line in output.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                devices.append(parts[0])
        return devices

    def shell(self, *args: str, check: bool = True, timeout: int = 60) -> str:
        return self.run(["shell", *args], check=check, timeout=timeout).stdout

    def shell_command(self, command: str, *, check: bool = True, timeout: int = 60) -> str:
        return self.run(["shell", command], check=check, timeout=timeout).stdout

    def detect_packages(self) -> list[str]:
        output = self.shell("pm", "list", "packages", check=False)
        tokens = ("tiktok", "musically", "aweme", "studio", "creator")
        packages = []
        for line in output.splitlines():
            package = line.replace("package:", "").strip()
            lowered = package.lower()
            if package and any(token in lowered for token in tokens):
                packages.append(package)
        return sorted(set(packages))

    def open_app(self, package: str) -> None:
        self.run(
            ["shell", "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1"],
            timeout=30,
        )

    def foreground_package(self) -> str:
        outputs = [
            self.shell("dumpsys", "window", "windows", check=False, timeout=15),
            self.shell("dumpsys", "activity", "activities", check=False, timeout=15),
        ]
        patterns = [
            r"mCurrentFocus=.*?\s([A-Za-z0-9_.]+)/",
            r"mFocusedApp=.*?\s([A-Za-z0-9_.]+)/",
            r"topResumedActivity=.*?\s([A-Za-z0-9_.]+)/",
            r"mResumedActivity=.*?\s([A-Za-z0-9_.]+)/",
        ]
        for output in outputs:
            for pattern in patterns:
                match = re.search(pattern, output)
                if match:
                    return match.group(1)
        return ""

    def push_video(
        self,
        local_video: str | Path,
        remote_dir: str,
        *,
        touch_after_push: bool = True,
        unique_remote_name: bool = True,
    ) -> str:
        local_path = Path(local_video)
        if not local_path.exists():
            raise FileNotFoundError(f"Video not found: {local_path}")
        safe_name = _safe_remote_name(local_path.name)
        if unique_remote_name:
            safe_name = _timestamped_remote_name(safe_name)
        remote_path = str(PurePosixPath(remote_dir) / safe_name)

        self.shell("mkdir", "-p", remote_dir)
        self.run(["push", str(local_path), remote_path], timeout=600)
        if touch_after_push:
            self.shell("touch", remote_path, check=False, timeout=15)
        self.scan_media_file(remote_path)
        return remote_path

    def scan_media_file(self, remote_path: str) -> None:
        paths = [remote_path]
        if remote_path.startswith("/sdcard/"):
            paths.append("/storage/emulated/0/" + remote_path.removeprefix("/sdcard/"))
        for path in paths:
            self.shell("cmd", "media", "scan-file", path, check=False, timeout=30)
            self.shell_command(
                "content call --uri content://media --method scan_file --arg "
                + shlex.quote(path),
                check=False,
                timeout=30,
            )
        self.shell(
            "am",
            "broadcast",
            "-a",
            "android.intent.action.MEDIA_SCANNER_SCAN_FILE",
            "-d",
            f"file://{remote_path}",
            check=False,
            timeout=30,
        )
        for path in paths:
            self.shell_command(
                "am broadcast --receiver-include-background "
                "-a android.intent.action.MEDIA_SCANNER_SCAN_FILE "
                "-d " + shlex.quote(f"file://{path}"),
                check=False,
                timeout=30,
            )

    def wait_for_media_file(
        self,
        remote_path: str,
        *,
        timeout_seconds: float = 30.0,
        poll_seconds: float = 1.0,
    ) -> dict[str, str | int | bool]:
        display_name = PurePosixPath(remote_path).name
        alternate_path = ""
        if remote_path.startswith("/sdcard/"):
            alternate_path = "/storage/emulated/0/" + remote_path.removeprefix("/sdcard/")
        deadline = time.monotonic() + timeout_seconds
        attempts = 0
        last_output = ""
        while True:
            attempts += 1
            found, output = self._query_media(display_name, remote_path, alternate_path)
            last_output = output
            if found:
                return {
                    "found": True,
                    "attempts": attempts,
                    "display_name": display_name,
                    "output": output.strip(),
                }
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return {
                    "found": False,
                    "attempts": attempts,
                    "display_name": display_name,
                    "output": last_output.strip(),
                }
            time.sleep(min(poll_seconds, max(0.0, remaining)))

    def wait_until_media_is_top(
        self,
        remote_path: str,
        *,
        remote_dir: str = "",
        timeout_seconds: float = 30.0,
        poll_seconds: float = 1.0,
        limit: int = 10,
    ) -> dict[str, object]:
        display_name = PurePosixPath(remote_path).name
        alternate_path = ""
        if remote_path.startswith("/sdcard/"):
            alternate_path = "/storage/emulated/0/" + remote_path.removeprefix("/sdcard/")
        deadline = time.monotonic() + timeout_seconds
        attempts = 0
        last_rows: list[dict[str, str]] = []
        while True:
            attempts += 1
            rows = self.recent_video_rows(remote_dir=remote_dir, limit=limit)
            last_rows = rows
            if rows:
                top = rows[0]
                top_name = top.get("_display_name", "")
                top_path = top.get("_data", "")
                if top_name == display_name or top_path in {remote_path, alternate_path}:
                    return {
                        "top_matches": True,
                        "attempts": attempts,
                        "display_name": display_name,
                        "top": top,
                        "rows": rows,
                    }
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return {
                    "top_matches": False,
                    "attempts": attempts,
                    "display_name": display_name,
                    "top": last_rows[0] if last_rows else {},
                    "rows": last_rows,
                }
            time.sleep(min(poll_seconds, max(0.0, remaining)))

    def recent_video_rows(self, *, remote_dir: str = "", limit: int = 10) -> list[dict[str, str]]:
        projection = "_id:_display_name:date_added:date_modified:datetaken:_data:mime_type"
        where = "mime_type LIKE 'video/%'"
        if remote_dir:
            storage_dir = remote_dir
            if remote_dir.startswith("/sdcard/"):
                storage_dir = "/storage/emulated/0/" + remote_dir.removeprefix("/sdcard/")
            where = f"{where} AND _data LIKE '{sql_literal(storage_dir.rstrip('/'))}/%'"
        sort = "datetaken DESC, date_added DESC, date_modified DESC, _id DESC"
        for uri in (
            "content://media/external_primary/video/media",
            "content://media/external/video/media",
        ):
            output = self.content_query(
                uri=uri,
                projection=projection,
                where=where,
                sort=sort,
                check=False,
                timeout=15,
            )
            rows = parse_content_query_rows(output)
            if rows:
                return rows[: max(1, limit)]
        return []

    def _query_media(self, display_name: str, remote_path: str, alternate_path: str = "") -> tuple[bool, str]:
        escaped_name = sql_literal(display_name)
        escaped_path = sql_literal(remote_path)
        escaped_alt_path = sql_literal(alternate_path)
        wheres = [
            f"_display_name='{escaped_name}'",
            f"_data='{escaped_path}'",
        ]
        if escaped_alt_path:
            wheres.insert(2, f"_data='{escaped_alt_path}'")
        outputs: list[str] = []
        uris = (
            "content://media/external/video/media",
            "content://media/external_primary/video/media",
            "content://media/external/file",
            "content://media/external_primary/file",
        )
        projection = "_id:_display_name:date_added:date_modified:datetaken:_data:mime_type"
        for uri in uris:
            for where in wheres:
                result = self.content_query(
                    uri=uri,
                    projection=projection,
                    where=where,
                    check=False,
                    timeout=15,
                )
                outputs.append(f"{uri} where {where}\n{result}")
                if display_name in result or remote_path in result or (alternate_path and alternate_path in result):
                    return True, result
        return False, "\n".join(outputs)

    def content_query(
        self,
        *,
        uri: str,
        projection: str = "",
        where: str = "",
        sort: str = "",
        check: bool = False,
        timeout: int = 15,
    ) -> str:
        command = "content query --uri " + shlex.quote(uri)
        if projection:
            command += " --projection " + shlex.quote(projection)
        if where:
            command += " --where " + shlex.quote(where)
        if sort:
            command += " --sort " + shlex.quote(sort)
        return self.shell_command(command, check=check, timeout=timeout)

    def update_media_timestamps(self, remote_path: str) -> dict[str, str | bool]:
        now_seconds = int(time.time())
        now_millis = now_seconds * 1000
        display_name = PurePosixPath(remote_path).name
        alternate_path = ""
        if remote_path.startswith("/sdcard/"):
            alternate_path = "/storage/emulated/0/" + remote_path.removeprefix("/sdcard/")

        wheres = [
            f"_display_name='{sql_literal(display_name)}'",
            f"_data='{sql_literal(remote_path)}'",
        ]
        if alternate_path:
            wheres.append(f"_data='{sql_literal(alternate_path)}'")

        bindings = [
            ("date_added", "l", str(now_seconds)),
            ("date_modified", "l", str(now_seconds)),
            ("datetaken", "l", str(now_millis)),
        ]
        outputs: list[str] = []
        changed = False
        for uri in (
            "content://media/external_primary/video/media",
            "content://media/external/video/media",
            "content://media/external_primary/file",
            "content://media/external/file",
        ):
            for where in wheres:
                output = self.content_update(uri=uri, bindings=bindings, where=where, check=False, timeout=15)
                outputs.append(f"{uri} where {where}\n{output}")
                if update_output_changed(output):
                    changed = True
        return {"changed": changed, "output": "\n".join(outputs).strip()}

    def insert_media_store_record(self, remote_path: str, mime_type: str = "video/mp4") -> dict[str, str | bool]:
        now_seconds = int(time.time())
        now_millis = now_seconds * 1000
        display_name = PurePosixPath(remote_path).name
        storage_path = remote_path
        if remote_path.startswith("/sdcard/"):
            storage_path = "/storage/emulated/0/" + remote_path.removeprefix("/sdcard/")
        relative_path = media_relative_path(storage_path)

        bindings = [
            ("_display_name", "s", display_name),
            ("mime_type", "s", mime_type),
            ("date_added", "l", str(now_seconds)),
            ("date_modified", "l", str(now_seconds)),
            ("datetaken", "l", str(now_millis)),
            ("_data", "s", storage_path),
        ]
        if relative_path:
            bindings.append(("relative_path", "s", relative_path))

        outputs: list[str] = []
        inserted = False
        for uri in (
            "content://media/external_primary/video/media",
            "content://media/external/video/media",
        ):
            output = self.content_insert(uri=uri, bindings=bindings, check=False, timeout=15)
            outputs.append(f"{uri}\n{output}")
            if "content://" in output or display_name in output:
                inserted = True
                break
        return {"inserted": inserted, "output": "\n".join(outputs).strip()}

    def content_update(
        self,
        *,
        uri: str,
        bindings: list[tuple[str, str, str]],
        where: str,
        check: bool = False,
        timeout: int = 15,
    ) -> str:
        command = "content update --uri " + shlex.quote(uri)
        for column, value_type, value in bindings:
            command += " --bind " + shlex.quote(f"{column}:{value_type}:{value}")
        command += " --where " + shlex.quote(where)
        return self.shell_command(command, check=check, timeout=timeout)

    def content_insert(
        self,
        *,
        uri: str,
        bindings: list[tuple[str, str, str]],
        check: bool = False,
        timeout: int = 15,
    ) -> str:
        command = "content insert --uri " + shlex.quote(uri)
        for column, value_type, value in bindings:
            command += " --bind " + shlex.quote(f"{column}:{value_type}:{value}")
        return self.shell_command(command, check=check, timeout=timeout)

    def content_delete(
        self,
        *,
        uri: str,
        where: str = "",
        check: bool = False,
        timeout: int = 15,
    ) -> str:
        command = "content delete --uri " + shlex.quote(uri)
        if where:
            command += " --where " + shlex.quote(where)
        return self.shell_command(command, check=check, timeout=timeout)

    def grant_media_permissions(self, package: str) -> dict[str, str]:
        permissions = (
            "android.permission.READ_EXTERNAL_STORAGE",
            "android.permission.READ_MEDIA_IMAGES",
            "android.permission.READ_MEDIA_VIDEO",
            "android.permission.READ_MEDIA_VISUAL_USER_SELECTED",
        )
        appops = (
            "READ_EXTERNAL_STORAGE",
            "READ_MEDIA_IMAGES",
            "READ_MEDIA_VIDEO",
            "READ_MEDIA_VISUAL_USER_SELECTED",
        )
        outputs: dict[str, str] = {}
        for permission in permissions:
            outputs[f"pm grant {permission}"] = self.shell(
                "pm",
                "grant",
                package,
                permission,
                check=False,
                timeout=15,
            ).strip()
        for appop in appops:
            outputs[f"appops set {appop}"] = self.shell(
                "appops",
                "set",
                package,
                appop,
                "allow",
                check=False,
                timeout=15,
            ).strip()
        return outputs

    def remote_file_exists(self, remote_path: str) -> bool:
        result = self.shell("test", "-f", remote_path, check=False, timeout=15)
        return result == ""

    def screenshot(self, output_path: str | Path) -> Path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        result = self.run(["exec-out", "screencap", "-p"], check=False, text=False, timeout=90)
        if result.returncode == 0 and result.stdout.startswith(b"\x89PNG"):
            path.write_bytes(result.stdout)
            return path

        remote_path = "/sdcard/upload_system_screen.png"
        self.shell("screencap", "-p", remote_path, timeout=90)
        self.run(["pull", remote_path, str(path)], timeout=120)
        data = path.read_bytes()
        if not data.startswith(b"\x89PNG"):
            raise ADBError("Screenshot did not return PNG data.")
        return path

    def dump_ui_xml(self) -> str:
        self.run(["shell", "uiautomator", "dump", "/sdcard/window.xml"], check=False, timeout=30)
        result = self.run(["exec-out", "cat", "/sdcard/window.xml"], check=False, text=False, timeout=30)
        if result.returncode != 0:
            return ""
        return result.stdout.decode("utf-8", "replace")

    def tap(self, x: int, y: int) -> None:
        self.shell("input", "tap", str(x), str(y), timeout=15)

    def wait(self, seconds: float) -> None:
        time.sleep(seconds)


def adb_env() -> dict[str, str]:
    ANDROID_HOME.mkdir(parents=True, exist_ok=True)
    android_dir = ANDROID_HOME / ".android"
    android_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["ANDROID_USER_HOME"] = str(ANDROID_HOME)
    env["USERPROFILE"] = str(ANDROID_HOME)
    env["HOME"] = str(ANDROID_HOME)
    env["ADB_VENDOR_KEYS"] = str(android_dir / "adbkey")
    env["ADB_SERVER_PORT"] = os.environ.get("GROUP_CONTROL_ADB_SERVER_PORT", DEFAULT_ADB_SERVER_PORT)
    return env


def adb_server_port() -> str:
    return os.environ.get("GROUP_CONTROL_ADB_SERVER_PORT", DEFAULT_ADB_SERVER_PORT)


def _safe_remote_name(name: str) -> str:
    stem, ext = os.path.splitext(name)
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-") or "video"
    ext = re.sub(r"[^A-Za-z0-9.]+", "", ext) or ".mp4"
    return f"{stem}{ext}"


def _timestamped_remote_name(name: str) -> str:
    stem, ext = os.path.splitext(name)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"gcs_{stamp}_{stem}{ext}"


def sql_literal(value: str) -> str:
    return value.replace("'", "''")


def update_output_changed(output: str) -> bool:
    match = re.search(r"Updated\s+(\d+)", output, flags=re.IGNORECASE)
    if match:
        return int(match.group(1)) > 0
    return "content://" in output


def media_relative_path(storage_path: str) -> str:
    prefix = "/storage/emulated/0/"
    if not storage_path.startswith(prefix):
        return ""
    parent = str(PurePosixPath(storage_path.removeprefix(prefix)).parent)
    if parent in ("", "."):
        return ""
    return parent.strip("/") + "/"


def parse_content_query_rows(output: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line.startswith("Row:"):
            continue
        _, _, payload = line.partition(" ")
        _, _, payload = payload.partition(" ")
        row: dict[str, str] = {}
        for match in re.finditer(r"([A-Za-z0-9_]+)=([^,]*)(?:,\s*|$)", payload):
            row[match.group(1)] = match.group(2)
        if row:
            rows.append(row)
    return rows


def parse_bounds(bounds: str) -> tuple[int, int, int, int] | None:
    match = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds or "")
    if not match:
        return None
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def summarize_ui_xml(xml_text: str, max_nodes: int = 300) -> list[UINode]:
    if not xml_text.strip().startswith("<"):
        return []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    nodes: list[UINode] = []
    for element in root.iter("node"):
        text = element.attrib.get("text", "")
        description = element.attrib.get("content-desc", "")
        resource_id = element.attrib.get("resource-id", "")
        clickable = element.attrib.get("clickable", "")
        enabled = element.attrib.get("enabled", "")
        bounds = element.attrib.get("bounds", "")
        class_name = element.attrib.get("class", "")
        has_signal = any((text, description, resource_id)) or clickable == "true"
        if not has_signal:
            continue

        parsed = parse_bounds(bounds)
        center_x = center_y = None
        if parsed:
            left, top, right, bottom = parsed
            center_x = (left + right) // 2
            center_y = (top + bottom) // 2

        nodes.append(
            UINode(
                index=len(nodes),
                text=text,
                description=description,
                resource_id=resource_id,
                class_name=class_name,
                clickable=clickable,
                enabled=enabled,
                bounds=bounds,
                center_x=center_x,
                center_y=center_y,
            )
        )
        if len(nodes) >= max_nodes:
            break
    return nodes

