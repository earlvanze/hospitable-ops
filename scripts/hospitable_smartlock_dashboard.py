#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, parse_qs

import requests
from requests import HTTPError

from check_smartlock_health import API_ROOT, build_headers, resolve_creds, SmartlockAuthError

SECRET_KEYS = re.compile(r"(^code$|pin|password|token|secret|credential|backup|access_code|door_code)", re.I)


def redact(obj: Any, show_secrets: bool = False) -> Any:
    if show_secrets:
        return obj
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if SECRET_KEYS.search(str(k)):
                out[k] = "<redacted>"
            else:
                out[k] = redact(v, show_secrets=False)
        return out
    if isinstance(obj, list):
        return [redact(x, show_secrets=False) for x in obj]
    return obj


def load_filters(args: argparse.Namespace) -> list[dict[str, Any]]:
    if args.filters_file:
        return json.loads(Path(args.filters_file).read_text(encoding="utf-8"))
    if args.filters_json:
        return json.loads(args.filters_json)
    return []


def request_json(session: requests.Session, method: str, path: str, *, params: dict[str, Any] | None = None, json_body: Any = None) -> Any:
    resp = session.request(method, f"{API_ROOT}{path}", params=params, json=json_body, timeout=30)
    resp.raise_for_status()
    if resp.status_code == 204 or not resp.text.strip():
        return {"ok": True, "status_code": resp.status_code}
    return resp.json()


def extract_items(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return [data]
    return []


def next_cursor(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    # Hospitable notifications use cursor links in pagination metadata.
    meta = payload.get("meta") or {}
    candidates = [
        meta.get("next_cursor"),
        meta.get("cursor"),
        ((meta.get("pagination") or {}).get("cursor") or {}).get("next"),
        ((meta.get("pagination") or {}).get("_links") or {}).get("next"),
        (payload.get("links") or {}).get("next"),
    ]
    for c in candidates:
        if not c:
            continue
        if isinstance(c, str) and c.startswith("http"):
            qs = parse_qs(urlparse(c).query)
            return (qs.get("cursor") or [None])[0]
        if isinstance(c, str):
            return c
    return None


def post_paginated(session: requests.Session, path: str, *, filters: list[dict[str, Any]], query: str, limit: int, max_pages: int) -> dict[str, Any]:
    items: list[Any] = []
    pages = []
    offset = 0
    for _ in range(max_pages):
        params = {"offset": offset, "query": query, "limit": limit}
        payload = request_json(session, "POST", path, params=params, json_body={"filters": filters})
        batch = extract_items(payload)
        pages.append({"offset": offset, "count": len(batch)})
        items.extend(batch)
        if len(batch) < limit:
            break
        offset += limit
    return {"data": items, "meta": {"pages": pages, "count": len(items)}}


def get_notifications(session: requests.Session, *, cursor: str | None, max_pages: int) -> dict[str, Any]:
    items: list[Any] = []
    pages = []
    cur = cursor
    for _ in range(max_pages):
        params = {"cursor": cur} if cur else None
        payload = request_json(session, "GET", "/v1/smartlocks/notifications", params=params)
        batch = extract_items(payload)
        pages.append({"cursor": cur, "count": len(batch)})
        items.extend(batch)
        cur = next_cursor(payload)
        if not cur:
            break
    return {"data": items, "meta": {"pages": pages, "count": len(items), "next_cursor": cur}}


def summarize(command: str, payload: Any) -> dict[str, Any]:
    items = extract_items(payload)
    if command in {"devices", "thermostats"}:
        return {
            "count": len(items),
            "items": [
                {
                    "id": x.get("id"),
                    "name": x.get("name"),
                    "online": ((x.get("device_properties") or {}).get("online")),
                    "locked": ((x.get("device_properties") or {}).get("locked")),
                    "battery": (((x.get("device_properties") or {}).get("battery") or {}).get("percentage")),
                    "battery_status": (((x.get("device_properties") or {}).get("battery") or {}).get("status")),
                    "properties": [p.get("name") for p in (x.get("properties") or []) if isinstance(p, dict)],
                    "issue_codes": [i.get("code") for i in (x.get("issues") or []) if isinstance(i, dict)],
                }
                for x in items
                if isinstance(x, dict)
            ],
        }
    if command in {"reservation-codes", "manual-codes"}:
        return {
            "count": len(items),
            "items": [
                {
                    "guest": x.get("name"),
                    "property": ((x.get("property") or {}).get("name")),
                    "reservation_id": ((x.get("reservation") or {}).get("reservation_id")),
                    "status": ((x.get("reservation") or {}).get("advanced_status")),
                    "start": x.get("start"),
                    "end": x.get("end"),
                    "locks": [d.get("name") for d in (x.get("devices") or x.get("locks") or []) if isinstance(d, dict)],
                }
                for x in items
                if isinstance(x, dict)
            ],
        }
    if command == "notifications":
        return {
            "count": len(items),
            "items": [
                {
                    "id": x.get("id"),
                    "type": x.get("type"),
                    "message": x.get("message"),
                    "created_at": x.get("created_at"),
                    "read_url": x.get("read_url"),
                }
                for x in items
                if isinstance(x, dict)
            ],
        }
    return {"count": len(items), "data": items if items else payload}


def main() -> int:
    ap = argparse.ArgumentParser(description="Hospitable smart-lock dashboard API helper based on captured my.hospitable.com HAR routes.")
    sub = ap.add_subparsers(dest="command", required=True)

    for name in ["devices", "thermostats", "reservation-codes", "manual-codes"]:
        sp = sub.add_parser(name)
        sp.add_argument("--query", default="")
        sp.add_argument("--limit", type=int, default=20)
        sp.add_argument("--max-pages", type=int, default=10)
        sp.add_argument("--filters-json")
        sp.add_argument("--filters-file")
        sp.add_argument("--summary", action="store_true")
        sp.add_argument("--show-secrets", action="store_true")

    for name in ["settings", "connections", "filters-devices", "filters-codes"]:
        sp = sub.add_parser(name)
        sp.add_argument("--summary", action="store_true")
        sp.add_argument("--show-secrets", action="store_true")

    sp = sub.add_parser("notifications")
    sp.add_argument("--cursor")
    sp.add_argument("--max-pages", type=int, default=1)
    sp.add_argument("--summary", action="store_true")
    sp.add_argument("--show-secrets", action="store_true")

    sp = sub.add_parser("mark-notification-read")
    sp.add_argument("notification_id")
    sp.add_argument("--execute", action="store_true", help="Actually mark notification read. Without this, prints dry-run target only.")
    sp.add_argument("--show-secrets", action="store_true")

    args = ap.parse_args()

    try:
        token, cookie = resolve_creds()
    except SmartlockAuthError as e:
        print(json.dumps({"ok": False, "error": "smartlock_auth_unavailable", "detail": e.detail}, indent=2))
        return 2

    session = requests.Session()
    session.headers.update(build_headers(token, cookie))

    try:
        if args.command == "devices":
            payload = post_paginated(session, "/v1/smartlocks/devices/smartlocks", filters=load_filters(args), query=args.query, limit=args.limit, max_pages=args.max_pages)
        elif args.command == "thermostats":
            payload = post_paginated(session, "/v1/smartlocks/devices/thermostats", filters=load_filters(args), query=args.query, limit=args.limit, max_pages=args.max_pages)
        elif args.command == "reservation-codes":
            payload = post_paginated(session, "/v1/smartlocks/codes/reservation", filters=load_filters(args), query=args.query, limit=args.limit, max_pages=args.max_pages)
        elif args.command == "manual-codes":
            payload = post_paginated(session, "/v1/smartlocks/codes/manual", filters=load_filters(args), query=args.query, limit=args.limit, max_pages=args.max_pages)
        elif args.command == "settings":
            payload = request_json(session, "GET", "/v1/settings/smartlocks")
        elif args.command == "connections":
            payload = request_json(session, "GET", "/smartlocks/connections")
        elif args.command == "filters-devices":
            payload = request_json(session, "GET", "/smartlocks/filters/devices")
        elif args.command == "filters-codes":
            payload = request_json(session, "GET", "/smartlocks/filters/codes")
        elif args.command == "notifications":
            payload = get_notifications(session, cursor=args.cursor, max_pages=args.max_pages)
        elif args.command == "mark-notification-read":
            path = f"/v1/smartlocks/notifications/{args.notification_id}"
            if not args.execute:
                payload = {"dry_run": True, "method": "PUT", "path": path, "note": "add --execute to mark read"}
            else:
                payload = request_json(session, "PUT", path, json_body={})
        else:
            raise AssertionError(args.command)
    except HTTPError as e:
        resp = e.response
        print(json.dumps({"ok": False, "error": "http_error", "status_code": getattr(resp, "status_code", None), "detail": (resp.text[:500] if resp is not None else str(e))}, indent=2))
        return 2

    out = summarize(args.command, payload) if getattr(args, "summary", False) else payload
    print(json.dumps(redact(out, show_secrets=getattr(args, "show_secrets", False)), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
