#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import os
import requests

BASE_URL = "https://public.api.hospitable.com/v2"
DEFAULT_OUT_DIR = Path("/home/umbrel/.openclaw/workspace-discord-public/hospitable/live")

STATE_FILE = "state.json"
STORE_DIRNAME = "store"

DATE_KEYS = {
    "reservations": ["booking_date", "check_in", "arrival_date", "created_at", "updated_at"],
    "payouts": ["date", "created_at", "updated_at"],
    "transactions": ["date", "created_at", "updated_at", "start_date"],
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Pull live Hospitable metrics with rate-limited incremental sync.")
    p.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="Directory for snapshots/store/latest.")
    p.add_argument("--days", type=int, default=7, help="Upcoming check-in/check-out window for summary metrics.")
    p.add_argument("--timeout", type=int, default=30, help="HTTP timeout seconds.")
    p.add_argument("--rate-delay-ms", type=int, default=250, help="Minimum delay between API requests.")
    p.add_argument("--max-retries", type=int, default=4, help="Max retries for transient/429 responses.")
    p.add_argument("--reservation-prop-chunk", type=int, default=25, help="properties[] count per reservations request.")
    p.add_argument("--force-full", action="store_true", help="Force full backfill (all pages) regardless of saved state.")
    return p.parse_args()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if value > 10_000_000_000:
            value = value / 1000.0
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except Exception:
            return None
    s = str(value).strip()
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    try:
        d = datetime.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc)
    except Exception:
        return None


def dt_to_iso(d: datetime | None) -> str | None:
    if not d:
        return None
    return d.astimezone(timezone.utc).isoformat()


def first_date(record: dict[str, Any], keys: list[str]) -> datetime | None:
    lower = {str(k).lower(): v for k, v in record.items()}
    for k in keys:
        v = record.get(k)
        d = parse_dt(v)
        if d:
            return d
        lv = lower.get(k.lower())
        d = parse_dt(lv)
        if d:
            return d
    return None


def to_records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        if isinstance(data, dict):
            return [data]
    return []


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")


def dedupe_merge(existing: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    no_id: list[dict[str, Any]] = []

    for r in existing:
        rid = str(r.get("id") or "")
        if rid:
            by_id[rid] = r
        else:
            no_id.append(r)

    for r in incoming:
        rid = str(r.get("id") or "")
        if rid:
            by_id[rid] = r
        else:
            no_id.append(r)

    return list(by_id.values()) + no_id


def max_date(records: list[dict[str, Any]], keys: list[str]) -> datetime | None:
    mx: datetime | None = None
    for r in records:
        d = first_date(r, keys)
        if d and (mx is None or d > mx):
            mx = d
    return mx


def min_max_dates(records: list[dict[str, Any]], keys: list[str]) -> tuple[datetime | None, datetime | None]:
    mn: datetime | None = None
    mx: datetime | None = None
    for r in records:
        d = first_date(r, keys)
        if not d:
            continue
        if mn is None or d < mn:
            mn = d
        if mx is None or d > mx:
            mx = d
    return mn, mx


def rate_wait(last_request_ts: float, delay_ms: int) -> float:
    if delay_ms <= 0:
        return time.time()
    elapsed = (time.time() - last_request_ts) * 1000.0
    if elapsed < delay_ms:
        time.sleep((delay_ms - elapsed) / 1000.0)
    return time.time()


def fetch_with_retries(
    session: requests.Session,
    url: str,
    timeout: int,
    rate_delay_ms: int,
    max_retries: int,
    last_request_ts: float,
    params: list[tuple[str, str]] | None = None,
) -> tuple[dict[str, Any], float]:
    attempt = 0
    last_err = None

    while attempt <= max_retries:
        attempt += 1
        last_request_ts = rate_wait(last_request_ts, rate_delay_ms)
        try:
            r = session.get(url, params=params, timeout=timeout)
            ct = r.headers.get("content-type", "")
            info: dict[str, Any] = {
                "ok": r.ok,
                "status": r.status_code,
                "url": r.url,
                "content_type": ct,
                "retry_count": attempt - 1,
            }
            try:
                info["payload"] = r.json()
            except Exception:
                info["payload"] = None
                info["text"] = r.text[:1000]

            if r.status_code == 429 and attempt <= max_retries:
                ra = r.headers.get("Retry-After")
                try:
                    wait_s = float(ra) if ra else min(2 * attempt, 10)
                except Exception:
                    wait_s = min(2 * attempt, 10)
                time.sleep(wait_s)
                continue

            if r.status_code >= 500 and attempt <= max_retries:
                time.sleep(min(1.5 * attempt, 8))
                continue

            return info, last_request_ts
        except Exception as e:
            last_err = str(e)
            if attempt <= max_retries:
                time.sleep(min(1.5 * attempt, 8))
                continue
            break

    return {
        "ok": False,
        "status": None,
        "url": url,
        "error": last_err or "request_failed",
        "payload": None,
        "retry_count": max_retries,
    }, last_request_ts


def paginate_sync(
    *,
    session: requests.Session,
    endpoint_name: str,
    endpoint_path: str,
    params_base: list[tuple[str, str]] | None,
    timeout: int,
    rate_delay_ms: int,
    max_retries: int,
    last_request_ts: float,
    mode: str,
    cutoff: datetime | None,
    existing_ids: set[str],
    date_keys: list[str],
) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]], float]:
    url = f"{BASE_URL}{endpoint_path}"

    # Always fetch page 1 first to discover last_page.
    first_params = list(params_base or []) + [("page", "1")]
    first_res, last_request_ts = fetch_with_retries(
        session, url, timeout, rate_delay_ms, max_retries, last_request_ts, params=first_params
    )

    statuses: list[dict[str, Any]] = [{k: v for k, v in first_res.items() if k != "payload"}]
    payload = first_res.get("payload")
    first_records = to_records(payload)

    last_page = 1
    if isinstance(payload, dict):
        meta = payload.get("meta")
        if isinstance(meta, dict):
            try:
                lp = int(meta.get("last_page") or 1)
                last_page = max(1, lp)
            except Exception:
                last_page = 1

    collected_new: list[dict[str, Any]] = []
    fetched_pages = 0

    def process_page_records(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
        nonlocal existing_ids
        page_new: list[dict[str, Any]] = []

        if mode == "full":
            return records, False

        # incremental mode
        stop_candidate = False
        for r in records:
            rid = str(r.get("id") or "")
            rdt = first_date(r, date_keys)

            if rid and rid in existing_ids:
                continue

            if cutoff and rdt and rdt <= cutoff:
                continue

            page_new.append(r)

        # Stop when this page is at/before cutoff and has no unseen records.
        if cutoff:
            _, page_max = min_max_dates(records, date_keys)
            if page_max and page_max <= cutoff:
                unseen = any(str(r.get("id") or "") not in existing_ids for r in records)
                if not unseen:
                    stop_candidate = True

        return page_new, stop_candidate

    # Build page order.
    if mode == "full":
        page_order = list(range(1, last_page + 1))
    else:
        page_order = list(range(last_page, 0, -1))

    # Consume pages.
    for page in page_order:
        if page == 1:
            recs = first_records
        else:
            page_params = list(params_base or []) + [("page", str(page))]
            res, last_request_ts = fetch_with_retries(
                session, url, timeout, rate_delay_ms, max_retries, last_request_ts, params=page_params
            )
            statuses.append({k: v for k, v in res.items() if k != "payload"})
            recs = to_records(res.get("payload"))

        fetched_pages += 1
        page_new, stop_now = process_page_records(recs)
        collected_new.extend(page_new)

        if stop_now:
            break

    status_summary = {
        "endpoint": endpoint_name,
        "mode": mode,
        "ok": all(bool(s.get("ok")) for s in statuses),
        "status": statuses[0].get("status") if statuses else None,
        "url": statuses[0].get("url") if statuses else url,
        "fetched_pages": fetched_pages,
        "last_page": last_page,
        "statuses": [s.get("status") for s in statuses],
    }

    return collected_new, status_summary, statuses, last_request_ts


def summarize_reservations(records: list[dict[str, Any]], days: int) -> dict[str, Any]:
    now = utcnow()
    horizon = now + timedelta(days=days)

    status_counter: Counter[str] = Counter()
    upcoming_checkins = 0
    upcoming_checkouts = 0

    for r in records:
        status = r.get("status")
        if status:
            status_counter[str(status)] += 1

        cin = first_date(r, ["check_in", "checkIn", "arrival_date", "start_date", "starts_at"])
        cout = first_date(r, ["check_out", "checkOut", "departure_date", "end_date", "ends_at"])

        if cin and now <= cin <= horizon:
            upcoming_checkins += 1
        if cout and now <= cout <= horizon:
            upcoming_checkouts += 1

    return {
        "count": len(records),
        "status_breakdown": dict(status_counter),
        "upcoming_checkins_next_days": upcoming_checkins,
        "upcoming_checkouts_next_days": upcoming_checkouts,
    }


def chunked(items: list[str], size: int) -> list[list[str]]:
    if size <= 0:
        size = 25
    return [items[i:i + size] for i in range(0, len(items), size)]


def sort_by_date(records: list[dict[str, Any]], keys: list[str]) -> list[dict[str, Any]]:
    def key_fn(r: dict[str, Any]) -> tuple[int, float]:
        d = first_date(r, keys)
        if d is None:
            return (0, 0.0)
        return (1, d.timestamp())

    return sorted(records, key=key_fn)


def main() -> None:
    args = parse_args()
    api_key = os.getenv("HOSPITABLE_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("HOSPITABLE_API_KEY missing in environment")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    store_dir = out_dir / STORE_DIRNAME
    store_dir.mkdir(parents=True, exist_ok=True)

    ts = utcnow().strftime("%Y%m%dT%H%M%SZ")
    run_dir = out_dir / ts
    run_dir.mkdir(parents=True, exist_ok=True)

    latest_dir = out_dir / "latest"
    latest_dir.mkdir(parents=True, exist_ok=True)

    state_path = out_dir / STATE_FILE
    state = load_json(state_path, {})
    initial_done = bool(state.get("initial_full_completed"))
    full_mode = args.force_full or (not initial_done)
    mode = "full" if full_mode else "incremental"

    session = requests.Session()
    session.headers.update(
        {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "User-Agent": "openclaw-hospitable-metrics/2.0",
        }
    )

    last_request_ts = 0.0

    # Existing store.
    existing_properties = to_records(load_json(store_dir / "properties_all.json", {"data": []}))
    existing_reservations = to_records(load_json(store_dir / "reservations_all.json", {"data": []}))
    existing_payouts = to_records(load_json(store_dir / "payouts_all.json", {"data": []}))
    existing_transactions = to_records(load_json(store_dir / "transactions_all.json", {"data": []}))

    existing_ids = {
        "properties": {str(r.get("id")) for r in existing_properties if r.get("id")},
        "reservations": {str(r.get("id")) for r in existing_reservations if r.get("id")},
        "payouts": {str(r.get("id")) for r in existing_payouts if r.get("id")},
        "transactions": {str(r.get("id")) for r in existing_transactions if r.get("id")},
    }

    cutoff = {
        "reservations": parse_dt(((state.get("endpoints") or {}).get("reservations") or {}).get("last_saved_date")),
        "payouts": parse_dt(((state.get("endpoints") or {}).get("payouts") or {}).get("last_saved_date")),
        "transactions": parse_dt(((state.get("endpoints") or {}).get("transactions") or {}).get("last_saved_date")),
    }

    endpoint_status: dict[str, Any] = {}

    # 1) Properties (always refresh full page set; usually small, needed for reservation filters)
    properties_fetched, properties_status, properties_page_statuses, last_request_ts = paginate_sync(
        session=session,
        endpoint_name="properties",
        endpoint_path="/properties",
        params_base=[],
        timeout=args.timeout,
        rate_delay_ms=args.rate_delay_ms,
        max_retries=args.max_retries,
        last_request_ts=last_request_ts,
        mode="full",
        cutoff=None,
        existing_ids=existing_ids["properties"],
        date_keys=["updated_at", "created_at"],
    )
    properties_new = [
        r for r in properties_fetched if str(r.get("id") or "") and str(r.get("id") or "") not in existing_ids["properties"]
    ]
    merged_properties = sort_by_date(dedupe_merge(existing_properties, properties_fetched), ["updated_at", "created_at"])
    endpoint_status["properties"] = properties_status
    save_json(run_dir / "properties_new.json", {"data": properties_new, "count": len(properties_new)})

    property_ids = [str(r.get("id")) for r in merged_properties if r.get("id")]

    # 2) Payouts & transactions
    merged_map: dict[str, list[dict[str, Any]]] = {
        "payouts": existing_payouts,
        "transactions": existing_transactions,
    }
    new_map: dict[str, list[dict[str, Any]]] = {
        "payouts": [],
        "transactions": [],
    }

    for ep in ["payouts", "transactions"]:
        new_records, status, page_statuses, last_request_ts = paginate_sync(
            session=session,
            endpoint_name=ep,
            endpoint_path=f"/{ep}",
            params_base=[],
            timeout=args.timeout,
            rate_delay_ms=args.rate_delay_ms,
            max_retries=args.max_retries,
            last_request_ts=last_request_ts,
            mode=mode,
            cutoff=cutoff[ep] if mode == "incremental" else None,
            existing_ids=existing_ids[ep],
            date_keys=DATE_KEYS[ep],
        )
        merged_map[ep] = sort_by_date(dedupe_merge(merged_map[ep], new_records), DATE_KEYS[ep])
        new_map[ep] = new_records
        endpoint_status[ep] = status
        save_json(run_dir / f"{ep}_new.json", {"data": new_records, "count": len(new_records), "mode": mode})

    # 3) Reservations (per property chunk, paginated)
    reservation_new_all: list[dict[str, Any]] = []
    reservation_chunk_statuses: list[dict[str, Any]] = []

    reservation_mode = mode
    reservation_cutoff = cutoff["reservations"] if mode == "incremental" else None

    if not property_ids:
        reservation_chunk_statuses.append(
            {
                "ok": False,
                "status": None,
                "note": "No property IDs available for required properties[] filter.",
            }
        )
    else:
        for idx, grp in enumerate(chunked(property_ids, args.reservation_prop_chunk), 1):
            params_base = [("properties[]", pid) for pid in grp]
            new_records, status, page_statuses, last_request_ts = paginate_sync(
                session=session,
                endpoint_name=f"reservations_chunk_{idx}",
                endpoint_path="/reservations",
                params_base=params_base,
                timeout=args.timeout,
                rate_delay_ms=args.rate_delay_ms,
                max_retries=args.max_retries,
                last_request_ts=last_request_ts,
                mode=reservation_mode,
                cutoff=reservation_cutoff,
                existing_ids=existing_ids["reservations"],
                date_keys=DATE_KEYS["reservations"],
            )
            reservation_new_all.extend(new_records)
            reservation_chunk_statuses.append(
                {
                    "chunk": idx,
                    "property_count": len(grp),
                    "fetched_pages": status.get("fetched_pages"),
                    "statuses": status.get("statuses"),
                    "ok": status.get("ok"),
                }
            )
            save_json(
                run_dir / f"reservations_chunk_{idx}_new.json",
                {
                    "data": new_records,
                    "count": len(new_records),
                    "mode": reservation_mode,
                    "property_ids": grp,
                },
            )

    merged_reservations = sort_by_date(
        dedupe_merge(existing_reservations, reservation_new_all), DATE_KEYS["reservations"]
    )

    endpoint_status["reservations"] = {
        "ok": all(bool(c.get("ok")) for c in reservation_chunk_statuses) if reservation_chunk_statuses else False,
        "status": 200 if reservation_chunk_statuses and all(bool(c.get("ok")) for c in reservation_chunk_statuses) else None,
        "url": f"{BASE_URL}/reservations",
        "mode": reservation_mode,
        "chunk_count": len(reservation_chunk_statuses),
        "chunk_statuses": reservation_chunk_statuses,
        "new_records": len(reservation_new_all),
    }

    save_json(run_dir / "reservations_new.json", {"data": reservation_new_all, "count": len(reservation_new_all), "mode": reservation_mode})

    # Persist merged stores.
    save_json(store_dir / "properties_all.json", {"data": merged_properties})
    save_json(store_dir / "reservations_all.json", {"data": merged_reservations})
    save_json(store_dir / "payouts_all.json", {"data": merged_map["payouts"]})
    save_json(store_dir / "transactions_all.json", {"data": merged_map["transactions"]})

    # Mirror latest for downstream agents.
    save_json(latest_dir / "properties.json", {"data": merged_properties})
    save_json(latest_dir / "reservations.json", {"data": merged_reservations})
    save_json(latest_dir / "payouts.json", {"data": merged_map["payouts"]})
    save_json(latest_dir / "transactions.json", {"data": merged_map["transactions"]})

    reservations_summary = summarize_reservations(merged_reservations, days=args.days)

    summary = {
        "generated_at": utcnow().isoformat(),
        "mode": mode,
        "window_days": args.days,
        "api_base": BASE_URL,
        "endpoint_status": endpoint_status,
        "metrics": {
            "properties_count": len(merged_properties),
            "reservations": reservations_summary,
            "payouts_count": len(merged_map["payouts"]),
            "transactions_count": len(merged_map["transactions"]),
            "new_records": {
                "properties": len(properties_new),
                "reservations": len(reservation_new_all),
                "payouts": len(new_map["payouts"]),
                "transactions": len(new_map["transactions"]),
            },
        },
        "snapshot_dir": str(run_dir),
        "store_dir": str(store_dir),
        "latest_dir": str(latest_dir),
    }

    save_json(run_dir / "summary.json", summary)
    save_json(latest_dir / "summary.json", summary)

    # Update sync state for incremental pulls.
    state_out = {
        "generated_at": summary["generated_at"],
        "initial_full_completed": True,
        "last_mode": mode,
        "endpoints": {
            "reservations": {
                "last_saved_date": dt_to_iso(max_date(merged_reservations, DATE_KEYS["reservations"])),
                "count": len(merged_reservations),
            },
            "payouts": {
                "last_saved_date": dt_to_iso(max_date(merged_map["payouts"], DATE_KEYS["payouts"])),
                "count": len(merged_map["payouts"]),
            },
            "transactions": {
                "last_saved_date": dt_to_iso(max_date(merged_map["transactions"], DATE_KEYS["transactions"])),
                "count": len(merged_map["transactions"]),
            },
            "properties": {
                "count": len(merged_properties),
            },
        },
    }
    save_json(state_path, state_out)

    # Human summary
    md_lines = [
        "# Hospitable Live Metrics Snapshot",
        "",
        f"Generated: {utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"Mode: **{mode}**",
        "",
        "## API Endpoint Status",
        "",
        "| Endpoint | HTTP | OK | Fetched pages |",
        "|---|---:|---:|---:|",
    ]
    for name in ["properties", "reservations", "payouts", "transactions"]:
        meta = endpoint_status.get(name, {})
        md_lines.append(
            f"| {name} | {meta.get('status', '—')} | {'yes' if meta.get('ok') else 'no'} | {meta.get('fetched_pages', meta.get('chunk_count', '—'))} |"
        )

    m = summary["metrics"]
    r = m["reservations"]
    md_lines += [
        "",
        "## Metrics",
        "",
        f"- Properties: **{m['properties_count']}** (new this run: {m['new_records']['properties']})",
        f"- Reservations: **{r['count']}** (new this run: {m['new_records']['reservations']})",
        f"- Upcoming check-ins ({args.days}d): **{r['upcoming_checkins_next_days']}**",
        f"- Upcoming check-outs ({args.days}d): **{r['upcoming_checkouts_next_days']}**",
        f"- Payouts: **{m['payouts_count']}** (new this run: {m['new_records']['payouts']})",
        f"- Transactions: **{m['transactions_count']}** (new this run: {m['new_records']['transactions']})",
        "",
        "## Reservation status breakdown",
        "",
    ]
    if r["status_breakdown"]:
        for k, v in sorted(r["status_breakdown"].items(), key=lambda x: (-x[1], x[0])):
            md_lines.append(f"- {k}: {v}")
    else:
        md_lines.append("- (none or unavailable)")

    (run_dir / "summary.md").write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    (latest_dir / "summary.md").write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    # Lightweight append-only health log for quick checks.
    health_log = out_dir / "health.log"
    status_bits = " ".join(
        f"{name}:{(meta.get('status') if isinstance(meta, dict) else 'na')}" for name, meta in endpoint_status.items()
    )
    overall_ok = all(bool((meta or {}).get("ok")) for meta in endpoint_status.values())
    health_line = (
        f"{summary['generated_at']} mode={mode} ok={'1' if overall_ok else '0'} "
        f"props={m['properties_count']} res={r['count']} payouts={m['payouts_count']} tx={m['transactions_count']} "
        f"new_props={m['new_records']['properties']} new_res={m['new_records']['reservations']} "
        f"new_payouts={m['new_records']['payouts']} new_tx={m['new_records']['transactions']} "
        f"statuses={status_bits}\n"
    )
    with health_log.open("a", encoding="utf-8") as f:
        f.write(health_line)

    print(
        json.dumps(
            {
                "ok": True,
                "mode": mode,
                "snapshot": str(run_dir),
                "latest": str(latest_dir),
                "store": str(store_dir),
                "state": str(state_path),
                "health_log": str(health_log),
                "metrics": summary["metrics"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
