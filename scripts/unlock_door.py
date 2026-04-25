#!/usr/bin/env python3
"""
unlock_door.py — Lock or unlock a Hospitable smart lock by name (fuzzy match).

Usage:
  python3 unlock_door.py unlock "front door"       # fuzzy match, dry-run default
  python3 unlock_door.py lock "27 front door" --execute
  python3 unlock_door.py lock "27" --execute       # matches "27 Front Door"

Fuzzy matching: case-insensitive substring match on device name.
Multiple matches → interactive select.
No match → show available devices.

Write safety: --execute required for actual lock/unlock.
             Defaults to dry-run (shows what would happen).
"""
from __future__ import annotations
import argparse
import http.client
import ssl
import sys
import time
from pathlib import Path

import requests

API_ROOT = "https://api.hospitable.com"
SECURE_ENV_PATH = Path("/home/umbrel/.openclaw/workspace/secure/evolve-hospitable-sync.env")

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
def _creds() -> tuple[str, str | None]:
    token = cookie = None
    if SECURE_ENV_PATH.exists():
        for line in SECURE_ENV_PATH.read_text(encoding="utf-8").splitlines():
            if line.startswith("HOSPITABLE_BEARER="):
                token = line.split("=", 1)[1].strip()
            if line.startswith("HOSPITABLE_COOKIE="):
                cookie = line.split("=", 1)[1].strip()
    if not token:
        raise SystemExit("No HOSPITABLE_BEARER found.")
    return token, cookie


# ---------------------------------------------------------------------------
# Raw socket — handles the Hospitable 204 + slow connection drain.
# We read only the status, then close the socket immediately.
# ---------------------------------------------------------------------------
def operate_device(token: str, cookie: str | None, device_id: str, action: str) -> bool:
    host = "api.hospitable.com"
    path = f"/smartlocks/devices/{device_id}/{action}"
    body = f'{{"action":"{action}"}}'.encode()

    headers = {
        "Host": host,
        "Authorization": f"Bearer {token}",
        "origin": "https://my.hospitable.com",
        "referer": "https://my.hospitable.com/",
        "user-agent": "Mozilla/5.0 OpenClaw SmartLock CLI",
        "accept": "application/json, text/plain, */*",
        "content-type": "application/json",
        "content-length": str(len(body)),
    }
    if cookie:
        headers["cookie"] = cookie

    ctx = ssl.create_default_context()
    conn = http.client.HTTPSConnection(host, timeout=15, context=ctx)
    try:
        conn.request("POST", path, body=body, headers=headers)
        resp = conn.getresponse()
        status = resp.status
        conn.close()  # Close immediately without reading body
        return status == 204
    except Exception:
        try:
            conn.close()
        except Exception:
            pass
        raise


# ---------------------------------------------------------------------------
# HTTP helpers (read-only operations via requests)
# ---------------------------------------------------------------------------
def _headers(token: str, cookie: str | None) -> dict:
    h = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "origin": "https://my.hospitable.com",
        "referer": "https://my.hospitable.com/",
        "user-agent": "Mozilla/5.0 OpenClaw SmartLock CLI",
        "Authorization": f"Bearer {token}",
    }
    if cookie:
        h["cookie"] = cookie
    return h


def list_devices(token: str, cookie: str | None, query: str = "", limit: int = 20) -> list[dict]:
    # Fresh session each call to avoid connection-pool issues after raw socket
    s = requests.Session()
    s.headers.update(_headers(token, cookie))
    # Longer timeout + retry for resilience
    for attempt in range(3):
        try:
            resp = s.post(
                f"{API_ROOT}/v1/smartlocks/devices/smartlocks",
                json={"filters": []},
                params={"offset": 0, "query": query, "limit": limit},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, list) else data.get("data", data.get("smartlocks", []))
        except requests.exceptions.Timeout:
            if attempt == 2:
                raise
            time.sleep(1.0 * (attempt + 1))
        finally:
            s.close()
    raise RuntimeError("list_devices failed after retries")


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------
def fuzzy_match(devices: list[dict], query: str) -> list[dict]:
    q = query.lower().strip()
    if not q:
        return devices
    return [d for d in devices if q in d["name"].lower()]


def select_device(matches: list[dict]) -> dict:
    print(f"\n{len(matches)} match(es). Select one:\n")
    for i, d in enumerate(matches, 1):
        online = d.get("device_properties", {}).get("online", False)
        locked = d.get("device_properties", {}).get("locked")
        status = "🟢 online" if online else "🔴 offline"
        lock_str = f" locked={locked}" if locked is not None else ""
        print(f"  [{i}] {d['name']} ({status}{lock_str})")
    print(f"\n  [0] cancel")
    while True:
        try:
            choice = input("Pick number: ").strip()
            idx = int(choice)
            if idx == 0:
                raise SystemExit(0)
            return matches[idx - 1]
        except (ValueError, IndexError):
            print(f"Invalid. Enter 1-{len(matches)} or 0.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="Lock or unlock a Hospitable smart lock by name.")
    ap.add_argument("action", choices=["lock", "unlock"], help="'lock' or 'unlock'")
    ap.add_argument("query", help="Device name to match (fuzzy, case-insensitive substring)")
    ap.add_argument("--limit", type=int, default=20, help="Max devices to fetch (default 20)")
    ap.add_argument("--execute", action="store_true", help="Actually send the command (default: dry-run)")
    args = ap.parse_args()

    token, cookie = _creds()

    # Fetch devices FIRST (before any raw socket ops)
    devices = list_devices(token, cookie, limit=args.limit)
    matches = fuzzy_match(devices, args.query)

    if not matches:
        print(f"No devices match '{args.query}'. Available devices:")
        for d in devices[:15]:
            online = d.get("device_properties", {}).get("online", False)
            print(f"  {'[ONLINE]' if online else '[OFFLINE]'} {d['name']}")
        return 1

    if len(matches) > 1:
        device = select_device(matches)
    else:
        device = matches[0]

    dev_id = device["id"]
    name = device["name"]
    online = device.get("device_properties", {}).get("online", False)

    print(f"\n{'⚡' if args.execute else '🔎'} {args.action.upper():6} → {name} (id={dev_id})")
    print(f"    Online: {online}")

    if not args.execute:
        print("    [DRY-RUN] No command sent. Re-run with --execute to lock/unlock.")
        return 0

    if not online:
        print("    🔴 Device is OFFLINE — command may fail.")

    try:
        ok = operate_device(token, cookie, dev_id, args.action)
        if ok:
            print(f"    ✓ {args.action.upper()} command sent successfully.")
        else:
            print(f"    ✗ Unexpected response — command may not have executed.")
            return 1
    except Exception as exc:
        print(f"    ✗ {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
