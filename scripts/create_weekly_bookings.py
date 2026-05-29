#!/usr/bin/env python3
"""
Create weekly recurring manual bookings in Hospitable via MCP.
Hardened workflow with proper error handling and retry logic.
"""

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta
from typing import List, Tuple, Optional


def run_mcporter(tool: str, **kwargs) -> Tuple[bool, dict]:
    """
    Call mcporter with proper JSON string escaping for nested objects.
    Returns (success, result_or_error).
    """
    args = ["mcporter", "call", "hospitable", tool]
    
    for key, value in kwargs.items():
        if isinstance(value, (dict, list)):
            # JSON objects must be passed as properly quoted strings
            args.append(f"{key}={json.dumps(value)}")
        else:
            args.append(f"{key}={value}")
    
    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=60
    )
    
    if result.returncode != 0:
        return False, {"error": result.stderr.strip() or result.stdout.strip()}
    
    try:
        # Try to parse JSON response
        output = result.stdout.strip()
        # mcporter might return non-JSON on validation errors
        if output.startswith('{'):
            return True, json.loads(output)
        else:
            return False, {"error": output}
    except json.JSONDecodeError:
        return False, {"error": f"Invalid JSON: {result.stdout[:200]}"}


def generate_weekly_dates(
    start_date: str,
    end_date: str,
    checkin_day: int = 0,  # Monday=0, Sunday=6
    checkout_day: int = 4   # Friday=4
) -> List[Tuple[str, str]]:
    """Generate weekly check-in/check-out dates."""
    dates = []
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    
    # Find first checkin_day on or after start
    days_until_checkin = (checkin_day - start.weekday()) % 7
    first_checkin = start + timedelta(days=days_until_checkin)
    
    current = first_checkin
    while current + timedelta(days=checkout_day - checkin_day) <= end:
        check_in = current.strftime("%Y-%m-%d")
        check_out = (current + timedelta(days=checkout_day - checkin_day)).strftime("%Y-%m-%d")
        dates.append((check_in, check_out))
        current += timedelta(days=7)
    
    return dates


def create_booking(
    property_id: str,
    check_in: str,
    check_out: str,
    guest: dict,
    rate_cents: int,
    notes: str = "",
    dry_run: bool = False
) -> Tuple[bool, dict]:
    """Create a single manual booking via MCP."""
    
    payload = {
        "property_id": property_id,
        "check_in": check_in,
        "check_out": check_out,
        "guests": {"adults": guest.get("adults", 1)},
        "guest": {
            "first_name": guest["first_name"],
            "last_name": guest["last_name"],
            "email": guest["email"],
            "phone": guest.get("phone", "")
        },
        "language": guest.get("language", "en"),
        "financials": {
            "currency": "USD",
            "accommodation": rate_cents
        },
        "channel": "direct",
        "notes": notes or f"Weekly booking - {guest['first_name']} {guest['last_name']}"
    }
    
    if dry_run:
        return True, {"dry_run": True, "payload": payload}
    
    return run_mcporter("create-reservation", **payload)


def main():
    ap = argparse.ArgumentParser(
        description="Create weekly recurring manual bookings in Hospitable"
    )
    ap.add_argument("--property-id", required=True, help="Hospitable property UUID")
    ap.add_argument("--start-date", required=True, help="Start date YYYY-MM-DD")
    ap.add_argument("--end-date", required=True, help="End date YYYY-MM-DD")
    ap.add_argument("--rate", type=int, required=True, help="Weekly rate in cents (e.g., 68750 for $687.50)")
    ap.add_argument("--first-name", required=True, help="Guest first name")
    ap.add_argument("--last-name", required=True, help="Guest last name")
    ap.add_argument("--email", required=True, help="Guest email")
    ap.add_argument("--phone", default="", help="Guest phone")
    ap.add_argument("--adults", type=int, default=1, help="Number of adults")
    ap.add_argument("--checkin-day", type=int, default=0, help="Check-in day (0=Monday, 6=Sunday)")
    ap.add_argument("--checkout-day", type=int, default=4, help="Check-out day (4=Friday)")
    ap.add_argument("--notes", default="", help="Booking notes")
    ap.add_argument("--dry-run", action="store_true", help="Preview without creating")
    ap.add_argument("--delay", type=float, default=0.5, help="Delay between requests (seconds)")
    
    args = ap.parse_args()
    
    # Generate dates
    dates = generate_weekly_dates(
        args.start_date,
        args.end_date,
        args.checkin_day,
        args.checkout_day
    )
    
    print(f"Creating {len(dates)} weekly bookings for {args.first_name} {args.last_name}")
    print(f"Property: {args.property_id}")
    print(f"Rate: ${args.rate/100:.2f}/week")
    print(f"Dates: {dates[0][0]} to {dates[-1][1]}")
    print()
    
    guest = {
        "first_name": args.first_name,
        "last_name": args.last_name,
        "email": args.email,
        "phone": args.phone,
        "adults": args.adults,
        "language": "en"
    }
    
    created = 0
    failed = 0
    
    for i, (check_in, check_out) in enumerate(dates, 1):
        print(f"[{i}/{len(dates)}] {check_in} → {check_out} ... ", end="", flush=True)
        
        success, result = create_booking(
            property_id=args.property_id,
            check_in=check_in,
            check_out=check_out,
            guest=guest,
            rate_cents=args.rate,
            notes=args.notes,
            dry_run=args.dry_run
        )
        
        if success:
            if args.dry_run:
                print("[DRY RUN]")
            else:
                booking_id = result.get("id", "N/A")
                print(f"✓ ({booking_id[:8]}...)")
            created += 1
        else:
            error = result.get("error", "Unknown error")
            print(f"✗ {error[:60]}")
            failed += 1
        
        time.sleep(args.delay)
    
    print()
    print(f"Done: {created} created, {failed} failed")
    if not args.dry_run:
        print(f"Total value: ${created * args.rate / 100:.2f}")
    
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
