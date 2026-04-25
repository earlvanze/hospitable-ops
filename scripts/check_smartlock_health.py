#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

API_ROOT = "https://api.hospitable.com"
SECURE_ENV_PATH = Path("/home/umbrel/.openclaw/workspace/secure/evolve-hospitable-sync.env")
DEFAULT_WHATSAPP_TARGET = "120363214019262017@g.us"  # Earlbnb Repairs & Maintenance
DEFAULT_BATTERY_THRESHOLD = 15
DEFAULT_LIMIT = 100
STATE_PATH = Path("/home/umbrel/.openclaw/workspace/data/hospitable/smartlock_health_state.json")


def resolve_creds() -> tuple[str, str | None]:
    token = os.getenv("HOSPITABLE_BEARER", "").strip()
    cookie = os.getenv("HOSPITABLE_COOKIE", "").strip() or None
    if SECURE_ENV_PATH.exists():
        for line in SECURE_ENV_PATH.read_text(encoding="utf-8").splitlines():
            if not token and line.startswith("HOSPITABLE_BEARER="):
                token = line.split("=", 1)[1].strip()
            if not cookie and line.startswith("HOSPITABLE_COOKIE="):
                cookie = line.split("=", 1)[1].strip()
    if not token:
        raise SystemExit("Missing HOSPITABLE_BEARER")
    return token, cookie


def build_headers(token: str, cookie: str | None) -> dict[str, str]:
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "origin": "https://my.hospitable.com",
        "referer": "https://my.hospitable.com/",
        "user-agent": "Mozilla/5.0 OpenClaw SmartLock Health",
        "Authorization": f"Bearer {token}",
    }
    if cookie:
        headers["cookie"] = cookie
    return headers


def list_all_devices(headers: dict[str, str], limit: int = DEFAULT_LIMIT) -> list[dict[str, Any]]:
    devices: list[dict[str, Any]] = []
    offset = 0
    session = requests.Session()
    session.headers.update(headers)
    while True:
        resp = session.post(
            f"{API_ROOT}/v1/smartlocks/devices/smartlocks",
            json={"filters": []},
            params={"offset": offset, "query": "", "limit": limit},
            timeout=30,
        )
        resp.raise_for_status()
        body = resp.json()
        batch = body if isinstance(body, list) else body.get("data", body.get("smartlocks", []))
        if not isinstance(batch, list):
            batch = []
        devices.extend(batch)
        if len(batch) < limit:
            break
        offset += limit
    return devices


def classify_issues(devices: list[dict[str, Any]], battery_threshold: int) -> dict[str, list[dict[str, Any]]]:
    disconnected: list[dict[str, Any]] = []
    critical_battery: list[dict[str, Any]] = []
    for d in devices:
        props = d.get("device_properties", {}) or {}
        online = props.get("online")
        battery = props.get("battery") or {}
        percentage = battery.get("percentage")
        item = {
            "id": d.get("id"),
            "name": d.get("name"),
            "online": online,
            "locked": props.get("locked"),
            "battery_percentage": percentage,
            "battery_status": battery.get("status"),
            "properties": [p.get("name") for p in (d.get("properties") or []) if isinstance(p, dict) and p.get("name")],
            "issues": d.get("issues") or [],
        }
        if online is False:
            disconnected.append(item)
        if isinstance(percentage, (int, float)) and percentage < battery_threshold:
            critical_battery.append(item)
    return {
        "disconnected": disconnected,
        "critical_battery": critical_battery,
    }


def render_message(result: dict[str, Any], battery_threshold: int) -> str | None:
    disconnected = result["issues"]["disconnected"]
    critical_battery = result["issues"]["critical_battery"]
    if not disconnected and not critical_battery:
        return None

    lines = [
        "Hospitable smart-lock alert",
        f"Checked: {result['checked_at']}",
        f"Total locks: {result['total_devices']}",
    ]

    if disconnected:
        lines.append("")
        lines.append(f"Disconnected ({len(disconnected)}):")
        for d in disconnected:
            props = f" | {'; '.join(d['properties'])}" if d.get("properties") else ""
            issue_codes = ", ".join(i.get("code", "?") for i in d.get("issues", []) if isinstance(i, dict) and i.get("code"))
            issue_suffix = f" | issues: {issue_codes}" if issue_codes else ""
            lines.append(f"- {d['name']}{props}{issue_suffix}")

    if critical_battery:
        lines.append("")
        lines.append(f"Battery <{battery_threshold}% ({len(critical_battery)}):")
        for d in critical_battery:
            props = f" | {'; '.join(d['properties'])}" if d.get("properties") else ""
            lines.append(f"- {d['name']} | {d['battery_percentage']}% ({d['battery_status']}){props}")

    return "\n".join(lines)


def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(result: dict[str, Any], message_text: str | None) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "checked_at": result["checked_at"],
        "total_devices": result["total_devices"],
        "issues": result["issues"],
        "message_text": message_text,
    }
    STATE_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def send_whatsapp(target: str, message_text: str) -> subprocess.CompletedProcess:
    cmd = [
        "openclaw",
        "message",
        "send",
        "--channel",
        "whatsapp",
        "--target",
        target,
        "--message",
        message_text,
    ]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60)


def main() -> int:
    ap = argparse.ArgumentParser(description="Check Hospitable smart-lock battery/connectivity and optionally alert WhatsApp.")
    ap.add_argument("--battery-threshold", type=int, default=DEFAULT_BATTERY_THRESHOLD)
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    ap.add_argument("--send-whatsapp", action="store_true", help="Send alert to WhatsApp target if issues are found.")
    ap.add_argument("--whatsapp-target", default=DEFAULT_WHATSAPP_TARGET)
    ap.add_argument("--only-on-change", action="store_true", help="Suppress alert if rendered alert text is unchanged from last run.")
    ap.add_argument("--json", action="store_true", help="Print JSON result.")
    args = ap.parse_args()

    token, cookie = resolve_creds()
    headers = build_headers(token, cookie)
    devices = list_all_devices(headers, limit=args.limit)
    issues = classify_issues(devices, battery_threshold=args.battery_threshold)
    result = {
        "checked_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "total_devices": len(devices),
        "issues": issues,
    }
    message_text = render_message(result, battery_threshold=args.battery_threshold)
    prior = load_state()
    save_state(result, message_text)

    sent = False
    suppressed = False
    if message_text and args.send_whatsapp:
        if args.only_on_change and prior.get("message_text") == message_text:
            suppressed = True
        else:
            proc = send_whatsapp(args.whatsapp_target, message_text)
            if proc.returncode != 0:
                sys.stderr.write((proc.stdout or "") + (proc.stderr or ""))
                return 1
            sent = True

    output = {
        **result,
        "alert_needed": bool(message_text),
        "alert_sent": sent,
        "alert_suppressed_unchanged": suppressed,
        "whatsapp_target": args.whatsapp_target if args.send_whatsapp else None,
    }

    if args.json:
        print(json.dumps(output, indent=2))
    else:
        print(f"checked={output['checked_at']} total={output['total_devices']} disconnected={len(issues['disconnected'])} critical_battery={len(issues['critical_battery'])} sent={sent} suppressed={suppressed}")
        if message_text:
            print("---alert-preview---")
            print(message_text)
        else:
            print("No disconnected or critical-battery locks.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
