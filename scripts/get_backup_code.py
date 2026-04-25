#!/usr/bin/env python3
import os
import sys
import requests
import json

API_KEY = os.getenv("HOSPITABLE_API_KEY", "").strip()
BEARER = os.getenv("HOSPITABLE_BEARER", "").strip()

DEVICES_URL = "https://api.hospitable.com/v1/smartlocks/devices/smartlocks?offset=0&query=&limit=20"
CODES_URL = "https://api.hospitable.com/v1/smartlocks/codes"


def _resolve_token():
    # Prefer bearer over legacy API key: smart-lock endpoints accept the bearer,
    # while the older PAT-style API key can return 401 on these routes.
    if BEARER:
        return BEARER
    env_file = os.path.expanduser("~/.openclaw/workspace/secure/evolve-hospitable-sync.env")
    try:
        file_api_key = ""
        with open(env_file, "r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("HOSPITABLE_BEARER="):
                    return line.split("=", 1)[1].strip()
                if line.startswith("HOSPITABLE_API_KEY="):
                    file_api_key = line.split("=", 1)[1].strip()
        if file_api_key:
            return file_api_key
    except OSError:
        pass
    if API_KEY:
        return API_KEY
    return ""


def get_backup_code(guest_name, lock_name):
    token = _resolve_token()
    if not token:
        return {"error": "No Hospitable bearer/API token found in environment."}

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    try:
        # 1) Device list endpoint is currently unreliable (500 from Hospitable),
        # so query codes directly and filter client-side by guest/lock/property.
        response = requests.post(CODES_URL, headers=headers, json={"name": guest_name}, timeout=20)
        response.raise_for_status()
        body = response.json() if response.content else {}
        codes = []
        if isinstance(body, dict):
            for key in ("data", "results", "items", "codes"):
                if isinstance(body.get(key), list):
                    codes = body.get(key) or []
                    break
        if not codes:
            return {"error": "Hospitable returned no smart-lock codes."}

        def _match_lock(entry):
            values = []
            if isinstance(entry.get("device"), dict):
                values.append(entry["device"].get("name", ""))
            if isinstance(entry.get("property"), dict):
                values.append(entry["property"].get("name", ""))
            if isinstance(entry.get("properties"), list):
                values.extend([(p or {}).get("name", "") for p in entry.get("properties", []) if isinstance(p, dict)])
            needle = lock_name.lower()
            hay = " | ".join(v for v in values if v)
            return needle in hay.lower()

        guest_code = next(
            (
                c for c in codes
                if guest_name.lower() in str(c.get("name", "")).lower() and _match_lock(c)
            ),
            None,
        )

        if guest_code:
            lock_label = None
            if isinstance(guest_code.get("device"), dict):
                lock_label = guest_code["device"].get("name")
            if not lock_label and isinstance(guest_code.get("property"), dict):
                lock_label = guest_code["property"].get("name")
            return {
                "guest": guest_code.get("name"),
                "lock": lock_label or lock_name,
                "code": guest_code.get("code"),
                "type": guest_code.get("type"),
                "actions": guest_code.get("actions"),
                "starts": guest_code.get("start"),
                "ends": guest_code.get("end"),
            }

        return {"error": f"No code found for guest '{guest_name}' on lock/property '{lock_name}'."}

    except requests.HTTPError as e:
        return {"error": f"HTTP error: {e.response.status_code} {e.response.text[:300]}"}
    except Exception as e:
        return {"error": str(e)}


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(json.dumps({"error": "Usage: get_backup_code.py <guest_name> <lock_name>"}))
        sys.exit(1)

    result = get_backup_code(sys.argv[1], sys.argv[2])
    print(json.dumps(result))
