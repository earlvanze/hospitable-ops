#!/usr/bin/env python3
import argparse, json, os, sys, textwrap
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

SCRIPT_DIR = Path('/home/umbrel/.openclaw/workspace/skills/hospitable-ops/scripts')
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from hospitable_property_matcher import match_property

try:
    import requests
except Exception:
    print('Missing dependency: requests', file=sys.stderr)
    sys.exit(2)

API_BASE = os.environ.get('HOSPITABLE_API_BASE', 'https://api.hospitable.com')
DEFAULT_HEADERS = {
    'accept': 'application/json, text/plain, */*',
    'content-type': 'application/json',
    'origin': 'https://my.hospitable.com',
    'referer': 'https://my.hospitable.com/',
    'user-agent': os.environ.get('HOSPITABLE_USER_AGENT', 'Mozilla/5.0 OpenClaw manual booking sync'),
}

@dataclass
class BookingInput:
    property_id: int
    reservation_code: str
    channel: str
    check_in: str
    check_out: str
    adults: int
    children: int = 0
    infants: int = 0
    pets: int = 0
    currency: str = 'USD'
    stay_type: str = 'guest_stay'
    guest_language: str = 'en'
    guest_first_name: str = ''
    guest_last_name: str = ''
    guest_email: str = ''
    guest_phone: str = ''
    note: str = ''
    num_guests: int = 2
    fees: Optional[List[Dict[str, Any]]] = None


def load_session() -> requests.Session:
    cookie_header = os.environ.get('HOSPITABLE_COOKIE')
    bearer = os.environ.get('HOSPITABLE_BEARER')
    s = requests.Session()
    s.headers.update(DEFAULT_HEADERS)
    if cookie_header:
        s.headers['cookie'] = cookie_header
    if bearer:
        s.headers['authorization'] = f'Bearer {bearer}'
    if not cookie_header and not bearer:
        raise SystemExit('Set HOSPITABLE_COOKIE or HOSPITABLE_BEARER in the environment.')
    return s


def quote_payload(b: BookingInput) -> Dict[str, Any]:
    return {
        'propertyId': b.property_id,
        'checkIn': b.check_in,
        'checkOut': b.check_out,
        'fees': b.fees or [],
        'adults': b.adults,
        'children': b.children,
        'pets': b.pets,
        'reservationCode': b.reservation_code,
        'stayType': b.stay_type,
    }


def create_payload(b: BookingInput) -> Dict[str, Any]:
    return {
        'platform': 'manual',
        'reservationCode': b.reservation_code,
        'channel': b.channel,
        'guestLanguage': b.guest_language,
        'numGuests': b.num_guests,
        'adults': b.adults,
        'children': b.children,
        'infants': b.infants,
        'pets': b.pets,
        'currency': b.currency,
        'stayType': b.stay_type,
        'checkIn': b.check_in,
        'checkOut': b.check_out,
        'propertyId': b.property_id,
        'guestFirstName': b.guest_first_name,
        'guestLastName': b.guest_last_name,
        'guestEmail': b.guest_email,
        'guestPhone': b.guest_phone,
        'note': b.note,
        'fees': b.fees or [],
    }


def post_json(session: requests.Session, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    r = session.post(f'{API_BASE}{path}', json=payload, timeout=30)
    if not r.ok:
        raise RuntimeError(f'{path} -> HTTP {r.status_code}: {r.text[:1200]}')
    return r.json()


def get_json(session: requests.Session, path: str) -> Dict[str, Any]:
    r = session.get(f'{API_BASE}{path}', timeout=30)
    if not r.ok:
        raise RuntimeError(f'{path} -> HTTP {r.status_code}: {r.text[:1200]}')
    return r.json()


def properties_lookup(session: requests.Session, query: str = '') -> List[Dict[str, Any]]:
    data = post_json(session, '/v1/properties?pagination=false&transformer=simple', {'filters': []})
    rows = data.get('data', [])
    if not query:
        return rows

    match = match_property(query)
    resolved = match.get('resolved')
    if resolved is not None:
        scored = []
        for row in rows:
            pid = row.get('id') or row.get('property_id')
            row_copy = dict(row)
            if str(pid) == str(resolved):
                row_copy['_fuzzy_match'] = match['matches'][0]
                scored.insert(0, row_copy)
            elif query.lower() in json.dumps(row).lower():
                scored.append(row_copy)
        if scored:
            return scored

    q = query.lower()
    return [r for r in rows if q in json.dumps(r).lower()]


def duplicate_check(session: requests.Session, property_id: int, reservation_code: str) -> List[Dict[str, Any]]:
    data = get_json(session, f'/v1/reservations/?starts_or_ends_between=2025-01-01_2027-12-31&timezones=false&property_ids={property_id}&calendar_blockable=true&include_family_reservations=true')
    hits = []
    for row in data.get('data', []):
        if str(row.get('code', '')).strip() == reservation_code:
            hits.append(row)
    return hits


def main() -> None:
    ap = argparse.ArgumentParser(description='Create Hospitable manual bookings via captured internal API path.')
    sub = ap.add_subparsers(dest='cmd', required=True)

    p_lookup = sub.add_parser('lookup-property')
    p_lookup.add_argument('query', nargs='?', default='')

    p_quote = sub.add_parser('quote')
    p_quote.add_argument('json_file')

    p_create = sub.add_parser('create')
    p_create.add_argument('json_file')
    p_create.add_argument('--dry-run', action='store_true')
    p_create.add_argument('--skip-duplicate-check', action='store_true')

    args = ap.parse_args()
    session = load_session()

    if args.cmd == 'lookup-property':
        print(json.dumps(properties_lookup(session, args.query), indent=2))
        return

    raw = json.load(open(args.json_file))
    booking = BookingInput(**raw)

    if args.cmd == 'quote':
        print(json.dumps(post_json(session, '/v1/bookings/manual/quote', quote_payload(booking)), indent=2))
        return

    if not args.skip_duplicate_check:
        hits = duplicate_check(session, booking.property_id, booking.reservation_code)
        if hits:
            print(json.dumps({'duplicate': True, 'matches': hits}, indent=2))
            raise SystemExit(3)

    quote = post_json(session, '/v1/bookings/manual/quote', quote_payload(booking))
    if args.dry_run:
        print(json.dumps({'dry_run': True, 'quote': quote, 'create_payload': create_payload(booking)}, indent=2))
        return

    created = post_json(session, '/v1/bookings/manual', create_payload(booking))
    print(json.dumps({'quote': quote, 'created': created}, indent=2))


if __name__ == '__main__':
    main()
