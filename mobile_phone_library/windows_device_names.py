from __future__ import annotations

import json
import re
import subprocess
from typing import Any


WPD_NAME_QUERY = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$rows = @()
Get-PnpDevice -PresentOnly -Class WPD -ErrorAction Stop | ForEach-Object {
    $parent = (Get-PnpDeviceProperty -InstanceId $_.InstanceId -KeyName 'DEVPKEY_Device_Parent' -ErrorAction SilentlyContinue).Data
    $serial = ''
    if ($parent -match '\\([^\\]+)$') {
        $serial = $matches[1]
    }
    if ($serial -ne '') {
        $rows += [pscustomobject]@{
            serial = $serial
            friendly_name = $_.FriendlyName
            instance_id = $_.InstanceId
            parent = $parent
        }
    }
}
$rows | ConvertTo-Json -Depth 4
"""


def portable_device_names_by_serial(*, timeout: int = 10) -> dict[str, str]:
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", WPD_NAME_QUERY],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if result.returncode != 0:
        return {}
    return parse_wpd_name_rows(result.stdout)


def parse_wpd_name_rows(raw_json: str) -> dict[str, str]:
    text = str(raw_json or "").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    rows = data if isinstance(data, list) else [data]
    mapping: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        serial = extract_serial(str(row.get("serial") or row.get("parent") or ""))
        name = str(row.get("friendly_name") or "").strip()
        if serial and name:
            mapping[serial.casefold()] = name
    return mapping


def extract_serial(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    match = re.search(r"\\([^\\]+)$", text)
    serial = match.group(1) if match else text
    return serial.strip()
