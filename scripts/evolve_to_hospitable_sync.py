#!/usr/bin/env python3
import argparse, csv, datetime as dt, json, os, re, sys
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import requests
except Exception:
    print('Missing dependency: requests', file=sys.stderr)
    sys.exit(2)

WORKSPACE = Path('/home/umbrel/.openclaw/workspace')
STATE_DIR = WORKSPACE / 'state'
LOG_DIR = WORKSPACE / 'logs'
OPS_DIR = WORKSPACE / 'ops'
STATE_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

EVOLVE_GRAPH = 'https://graph.prod.evolve.com/'
HOSP_API = os.environ.get('HOSPITABLE_API_BASE', 'https://api.hospitable.com')
PLACEHOLDER_DOMAIN = os.environ.get('HOSPITABLE_PLACEHOLDER_DOMAIN', 'missing-guest-email.local')
TODAY = dt.datetime.utcnow().strftime('%Y-%m-%d')

BOOKINGS_QUERY = '''query Bookings($accountId: ID, $orderBy: BookingsOrderByInput, $offset: Int, $limit: Int, $first: Int, $excludeCancelledBlocks: Boolean, $listingIds: [String!], $listingStatuses: [String!], $checkInAfter: LocalDate, $checkInBefore: LocalDate, $checkOutAfter: LocalDate, $checkOutBefore: LocalDate, $displayStatuses: [BookingDisplayStatus!], $stayAfter: LocalDate, $stayBefore: LocalDate, $types: [BookingType!], $dataSources: [BookingDataSource!], $hasClaim: Boolean, $hasIncidentReport: Boolean) {\n  bookings(accountId: $accountId, orderBy: $orderBy, offset: $offset, limit: $limit, first: $first, excludeCancelledBlocks: $excludeCancelledBlocks, listingIds: $listingIds, listingStatuses: $listingStatuses, checkInAfter: $checkInAfter, checkInBefore: $checkInBefore, checkOutAfter: $checkOutAfter, checkOutBefore: $checkOutBefore, displayStatuses: $displayStatuses, stayAfter: $stayAfter, stayBefore: $stayBefore, types: $types, dataSources: $dataSources, hasClaim: $hasClaim, hasIncidentReport: $hasIncidentReport) {\n    nodes { id internalId displayStatus status referralSource listing { propertyName id internalId __typename } __typename }\n    pageInfo { totalCount hasNextPage __typename }\n    __typename\n  }\n}'''

BOOKING_QUERY = '''query Booking($id: ID, $internalId: String) {
  booking(id: $id, internalId: $internalId) {
    __typename
    id
    internalId
    status
    displayStatus
    referralSource
    type
    checkInDate
    checkOutDate
    numberOfNights
    numberOfAdults
    numberOfChildren
    numberOfInfants
    petsRequested
    traveler { id name shortName email phone phoneRaw __typename }
    listing { id internalId propertyName address { street city state postalCode __typename } __typename }
    fees { amount { amount __typename } description __typename }
    rates { amount { amount __typename } quantity __typename }
    lineItems { nodes { id description taxRemitter type totalAmount { amount currency __typename } totalBaseAmount { amount currency __typename } refundedAmount { amount currency __typename } feeCategory __typename } __typename }
    ownerPayout { amount currency __typename }
    evolveManagementFee { amount currency __typename }
    amountDueToOwner { amount __typename }
    ownerTaxResponsibility { amount currency __typename }
    transactions { nodes { id amount { amount currency __typename } dueDate status createdAt __typename } __typename }
  }
}'''


def refresh_evolve_bearer_from_clerk(session_id: str, cookie: str) -> str:
    url = f"https://clerk.evolve.com/v1/client/sessions/{session_id}/tokens?__clerk_api_version=2025-11-10&_clerk_js_version=5.125.5"
    headers = {
        'accept': '*/*',
        'content-type': 'application/x-www-form-urlencoded',
        'origin': 'https://owner.evolve.com',
        'referer': 'https://owner.evolve.com/',
        'user-agent': 'Mozilla/5.0 OpenClaw Evolve-Hospitable Sync',
        'cookie': cookie,
    }
    r = requests.post(url, headers=headers, data='organization_id=', timeout=30)
    if not r.ok:
        raise RuntimeError(f'Clerk token refresh HTTP {r.status_code}: {r.text[:1200]}')
    data = r.json()
    token = data.get('jwt') or data.get('token') or data.get('session_token')
    if not token:
        for k, v in data.items():
            if isinstance(v, str) and v.count('.') >= 2:
                token = v
                break
    if not token:
        raise RuntimeError(f'Could not extract Clerk token from response: {data}')
    return token


def evolve_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        'content-type': 'application/json',
        'accept': 'application/json',
        'origin': 'https://owner.evolve.com',
        'referer': 'https://owner.evolve.com/',
        'user-agent': 'Mozilla/5.0 OpenClaw Evolve-Hospitable Sync',
    })
    cookie = os.environ.get('EVOLVE_COOKIE')
    auth = os.environ.get('EVOLVE_AUTHORIZATION')
    clerk_cookie = os.environ.get('EVOLVE_CLERK_COOKIE')
    clerk_session_id = os.environ.get('EVOLVE_CLERK_SESSION_ID')
    if cookie:
        s.headers['cookie'] = cookie
    if auth:
        s.headers['authorization'] = auth if auth.lower().startswith('bearer ') else f'Bearer {auth}'
    elif clerk_cookie and clerk_session_id:
        s.headers['authorization'] = f'Bearer {refresh_evolve_bearer_from_clerk(clerk_session_id, clerk_cookie)}'
    if not cookie and not auth and not (clerk_cookie and clerk_session_id):
        raise SystemExit('Missing EVOLVE auth: provide EVOLVE_AUTHORIZATION, EVOLVE_COOKIE, or EVOLVE_CLERK_COOKIE+EVOLVE_CLERK_SESSION_ID')
    return s


def hosp_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        'accept': 'application/json, text/plain, */*',
        'content-type': 'application/json',
        'origin': 'https://my.hospitable.com',
        'referer': 'https://my.hospitable.com/',
        'user-agent': 'Mozilla/5.0 OpenClaw Evolve-Hospitable Sync',
    })
    cookie = os.environ.get('HOSPITABLE_COOKIE')
    bearer = os.environ.get('HOSPITABLE_BEARER')
    if cookie:
        s.headers['cookie'] = cookie
    if bearer:
        s.headers['authorization'] = f'Bearer {bearer}'
    if not cookie and not bearer:
        raise SystemExit('Missing HOSPITABLE_COOKIE / HOSPITABLE_BEARER')
    return s


def gql(session: requests.Session, op: str, query: str, variables: Dict[str, Any]) -> Dict[str, Any]:
    payload = {
        'operationName': op,
        'variables': variables,
        'query': query,
        'extensions': {'clientLibrary': {'name': '@apollo/client', 'version': '4.0.11'}},
    }
    r = session.post(EVOLVE_GRAPH, json=payload, timeout=30)
    if not r.ok:
        raise RuntimeError(f'Evolve {op} HTTP {r.status_code}: {r.text[:1200]}')
    data = r.json()
    if data.get('errors'):
        raise RuntimeError(f'Evolve {op} errors: {json.dumps(data["errors"])}')
    return data['data']


def hosp_post(session: requests.Session, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    r = session.post(f'{HOSP_API}{path}', json=payload, timeout=30)
    if not r.ok:
        raise RuntimeError(f'Hospitable {path} HTTP {r.status_code}: {r.text[:1200]}')
    return r.json()


def hosp_get(session: requests.Session, path: str) -> Dict[str, Any]:
    r = session.get(f'{HOSP_API}{path}', timeout=30)
    if not r.ok:
        raise RuntimeError(f'Hospitable {path} HTTP {r.status_code}: {r.text[:1200]}')
    return r.json()


def load_json(path: Path, default: Any) -> Any:
    return json.loads(path.read_text()) if path.exists() else default


def save_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True))


def list_bookings(account_id: str, listing_ids: List[str], stay_after: str, stay_before: str) -> List[Dict[str, Any]]:
    vars = {
        'accountId': account_id,
        'dataSources': ['SALESFORCE', 'PINNACLE'],
        'displayStatuses': ['BOOKED', 'PENDING'],
        'excludeCancelledBlocks': True,
        'first': 1000,
        'limit': 1000,
        'listingIds': listing_ids,
        'listingStatuses': ['Active', 'Active For Rent', 'Building', 'Entered - Incomplete', 'Entered - Pending', 'Inactive', 'On-Hold', 'Removed From Network', 'Resigned'],
        'orderBy': {'direction': 'ASC', 'field': 'CHECK_IN'},
        'stayAfter': stay_after,
        'stayBefore': stay_before,
    }
    return gql(evolve_session(), 'Bookings', BOOKINGS_QUERY, vars)['bookings']['nodes']


def get_booking(internal_id: str) -> Dict[str, Any]:
    vars = {
        'id': '',
        'internalId': internal_id,
    }
    return gql(evolve_session(), 'Booking', BOOKING_QUERY, vars)['booking']


def money(v: Optional[Dict[str, Any]]) -> float:
    if not v:
        return 0.0
    amt = v.get('amount')
    return float(amt or 0.0)


def split_name(name: str) -> (str, str):
    parts = (name or '').strip().split()
    if not parts:
        return '', ''
    return parts[0], ' '.join(parts[1:]) if len(parts) > 1 else ''


def normalize_channel(referral: Optional[str]) -> str:
    x = (referral or '').strip().upper()
    if x == 'VRBO':
        return 'evolve'
    if x == 'AIRBNB':
        return 'airbnb'
    if x in ('BOOKING_COM', 'BOOKING.COM'):
        return 'booking.com'
    if x == 'EVOLVE':
        return 'evolve'
    return 'manual'


def placeholder_email(code: str) -> str:
    safe = re.sub(r'[^A-Za-z0-9._-]+', '-', code or 'unknown')
    return f'{safe}@{PLACEHOLDER_DOMAIN}'


def extract_fee_rows(booking: Dict[str, Any]) -> Dict[str, float]:
    acc = 0.0
    cleaning = 0.0
    host_tax = 0.0
    pet_fee = 0.0
    extra_guest_fee = 0.0
    community_fee = 0.0
    resort_fee = 0.0
    management_fee = 0.0
    linen_fee = 0.0
    for li in (booking.get('lineItems') or {}).get('nodes', []):
        desc = (li.get('description') or '').lower()
        base = money(li.get('totalBaseAmount') or li.get('totalAmount'))
        typ = (li.get('type') or '').upper()
        if typ == 'RATE' or 'rate' in desc:
            acc += base
        elif 'discount' in desc:
            acc += base
        elif 'cleaning' in desc:
            cleaning += base
        elif 'pet fee' in desc:
            pet_fee += base
        elif typ == 'TAX':
            host_tax += base
    management_fee = money(booking.get('evolveManagementFee'))
    return {
        'accomodation': round(acc, 2),
        'cleaning_fee': round(cleaning, 2),
        'linen_fee': round(linen_fee, 2),
        'management_fee': 0.0,
        'community_fee': round(community_fee, 2),
        'resort_fee': round(resort_fee, 2),
        'pet_fee': round(pet_fee, 2),
        'extra_guest_fee': round(extra_guest_fee, 2),
        'host_tax': round(host_tax, 2),
        '_evolve_management_fee_reference': round(management_fee, 2),
    }


def canonical_from_evolve(booking: Dict[str, Any], property_map: Dict[str, int]) -> Dict[str, Any]:
    traveler = booking.get('traveler') or {}
    first, last = split_name(traveler.get('name') or '')
    code = booking.get('id') or booking.get('internalId')
    email = traveler.get('email') or placeholder_email(code)
    fee_rows = extract_fee_rows(booking)
    fees = [
        {'type': 'accomodation', 'amount': fee_rows['accomodation'], 'label': 'Accommodation'},
        {'type': 'cleaning_fee', 'amount': fee_rows['cleaning_fee'], 'label': 'Cleaning Fee'},
        {'type': 'linen_fee', 'amount': fee_rows['linen_fee'], 'label': 'Linen Fee'},
        {'type': 'management_fee', 'amount': fee_rows['management_fee'], 'label': 'Management Fee'},
        {'type': 'community_fee', 'amount': fee_rows['community_fee'], 'label': 'Community Fee'},
        {'type': 'resort_fee', 'amount': fee_rows['resort_fee'], 'label': 'Resort Fee'},
        {'type': 'pet_fee', 'amount': fee_rows['pet_fee'], 'label': 'Pet Fee'},
        {'type': 'extra_guest_fee', 'amount': fee_rows['extra_guest_fee'], 'label': 'Extra Guest Fee'},
        {'type': 'host_tax', 'amount': fee_rows['host_tax'], 'label': 'Pass-through taxes'},
    ]
    prop_name = (booking.get('listing') or {}).get('propertyName')
    return {
        'source_internal_id': booking.get('internalId'),
        'source_code': code,
        'status': booking.get('status'),
        'display_status': booking.get('displayStatus'),
        'property_name': prop_name,
        'property_id': property_map.get(prop_name),
        'reservation_code': code,
        'channel': normalize_channel(booking.get('referralSource')),
        'guest_language': 'en',
        'num_guests': max(2, int(booking.get('numberOfAdults') or 0) + int(booking.get('numberOfChildren') or 0)),
        'adults': int(booking.get('numberOfAdults') or 0),
        'children': int(booking.get('numberOfChildren') or 0),
        'infants': int(booking.get('numberOfInfants') or 0),
        'pets': int(booking.get('petsRequested') or 0),
        'currency': 'USD',
        'stay_type': 'guest_stay',
        'check_in': booking.get('checkInDate'),
        'check_out': booking.get('checkOutDate'),
        'guest_first_name': first,
        'guest_last_name': last,
        'guest_email': email,
        'guest_phone': traveler.get('phoneRaw') or traveler.get('phone') or '',
        'note': booking.get('referralSource') or '',
        'fees': fees,
        'owner_payout': money(booking.get('ownerPayout')),
        'evolve_management_fee_reference': fee_rows['_evolve_management_fee_reference'],
        'missing_email': not bool(traveler.get('email')),
        'traveler_name': traveler.get('name') or '',
        'referral_source': booking.get('referralSource'),
    }


def duplicate_check(hs: requests.Session, property_id: int, reservation_code: str) -> List[Dict[str, Any]]:
    data = hosp_get(hs, f'/v1/reservations/?starts_or_ends_between=2025-01-01_2027-12-31&timezones=false&property_ids={property_id}&calendar_blockable=true&include_family_reservations=true')
    return [r for r in data.get('data', []) if str(r.get('code', '')).strip() == str(reservation_code).strip()]


def sync_one(hs: requests.Session, canon: Dict[str, Any], dry_run: bool=False) -> Dict[str, Any]:
    if not canon.get('property_id'):
        return {'status': 'error', 'reason': f"missing property_id for {canon.get('property_name')}"}
    dupes = duplicate_check(hs, canon['property_id'], canon['reservation_code'])
    if dupes:
        return {'status': 'skipped_duplicate', 'matches': dupes}
    quote_payload = {
        'propertyId': canon['property_id'],
        'checkIn': canon['check_in'],
        'checkOut': canon['check_out'],
        'fees': canon['fees'],
        'adults': canon['adults'],
        'children': canon['children'],
        'pets': canon['pets'],
        'reservationCode': canon['reservation_code'],
        'stayType': canon['stay_type'],
    }
    try:
        quote = hosp_post(hs, '/v1/bookings/manual/quote', quote_payload)
    except Exception as e:
        return {'status': 'error', 'reason': str(e)}
    create_payload = {
        'platform': 'manual',
        'reservationCode': canon['reservation_code'],
        'channel': canon['channel'],
        'guestLanguage': canon['guest_language'],
        'numGuests': canon['num_guests'],
        'adults': canon['adults'],
        'children': canon['children'],
        'infants': canon['infants'],
        'pets': canon['pets'],
        'currency': canon['currency'],
        'stayType': canon['stay_type'],
        'checkIn': canon['check_in'],
        'checkOut': canon['check_out'],
        'propertyId': canon['property_id'],
        'guestFirstName': canon['guest_first_name'],
        'guestLastName': canon['guest_last_name'],
        'guestEmail': canon['guest_email'],
        'guestPhone': canon['guest_phone'],
        'note': canon['note'],
        'fees': canon['fees'],
    }
    if dry_run:
        return {'status': 'dry_run', 'quote': quote, 'create_payload': create_payload}
    try:
        created = hosp_post(hs, '/v1/bookings/manual', create_payload)
        return {'status': 'created', 'quote': quote, 'created': created}
    except Exception as e:
        msg = str(e)
        if 'overlaps those dates' in msg:
            if os.environ.get('AUTO_BLOCK_ON_OVERLAP','0') == '1':
                try:
                    set_property_calendar_availability(hs, canon['property_id'], canon['check_in'], canon['check_out'], available=False)
                    return {'status': 'blocked_overlap_conflict', 'reason': msg}
                except Exception as be:
                    return {'status': 'overlap_conflict_block_error', 'reason': msg, 'block_error': str(be)}
            return {'status': 'skipped_overlap_conflict', 'reason': msg}
        return {'status': 'error', 'reason': msg}


def write_missing_email_report(rows: List[Dict[str, Any]]) -> Optional[Path]:
    if not rows:
        return None
    path = LOG_DIR / f'evolve_missing_guest_emails_{TODAY}.csv'
    with path.open('w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=['source_code','traveler_name','guest_phone','property_name','check_in','check_out','guest_email','placeholder_email','referral_source'])
        w.writeheader()
        for r in rows:
            w.writerow({
                'source_code': r['reservation_code'],
                'traveler_name': r['traveler_name'],
                'guest_phone': r['guest_phone'],
                'property_name': r['property_name'],
                'check_in': r['check_in'],
                'check_out': r['check_out'],
                'guest_email': '',
                'placeholder_email': r['guest_email'],
                'referral_source': r['referral_source'],
            })
    return path


def set_property_calendar_availability(hs: requests.Session, property_id: int, start: str, end: str, available: bool) -> Dict[str, Any]:
    return hosp_post(hs, f'/v1/properties/{property_id}/calendar?flags[events_only]=true', {
        'start': start,
        'end': end,
        'available': bool(available),
    })



def find_hospitable_by_code(hs: requests.Session, property_id: int, reservation_code: str) -> List[Dict[str, Any]]:
    data = hosp_get(hs, f'/v1/reservations/?starts_or_ends_between=2025-01-01_2027-12-31&timezones=false&property_ids={property_id}&calendar_blockable=true&include_family_reservations=true')
    return [r for r in data.get('data', []) if str(r.get('code', '')).strip() == str(reservation_code).strip()]


def cancel_hospitable_reservation(hs: requests.Session, reservation_uuid: str, initiator: str = 'guest') -> Dict[str, Any]:
    # optional quote endpoint called by UI first
    _ = hosp_post(hs, f'/v1/reservations/{reservation_uuid}/cancel-quote', {'initiator': initiator})
    return hosp_put(hs, f'/v1/reservations/{reservation_uuid}/cancel', {'initiated_by': initiator})


def hosp_put(session: requests.Session, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    r = session.put(f'{HOSP_API}{path}', json=payload, timeout=30)
    if not r.ok:
        raise RuntimeError(f'Hospitable {path} HTTP {r.status_code}: {r.text[:1200]}')
    return r.json()



def main():
    ap = argparse.ArgumentParser(description='Daily Evolve -> Hospitable sync')
    ap.add_argument('--account-id', default=os.environ.get('EVOLVE_ACCOUNT_ID', '0014P000041tawnQAA'))
    ap.add_argument('--listing-id', action='append', default=[])
    ap.add_argument('--days-back', type=int, default=1)
    ap.add_argument('--days-forward', type=int, default=365)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--sync-cancelled', action='store_true', default=(os.environ.get('SYNC_CANCELLED_IN_HOSPITABLE','0')=='1'))
    ap.add_argument('--auto-block-on-overlap', action='store_true', default=(os.environ.get('AUTO_BLOCK_ON_OVERLAP','0')=='1'))
    ap.add_argument('--auto-unblock-on-cancel', action='store_true', default=(os.environ.get('AUTO_UNBLOCK_ON_CANCEL','1')=='1'))
    args = ap.parse_args()

    if args.auto_block_on_overlap:
        os.environ['AUTO_BLOCK_ON_OVERLAP'] = '1'
    else:
        os.environ.pop('AUTO_BLOCK_ON_OVERLAP', None)

    property_map = load_json(OPS_DIR / 'evolve_hospitable_property_map.json', {})
    sync_state_path = STATE_DIR / 'evolve_hospitable_sync_state.json'
    sync_state = load_json(sync_state_path, {'processed': {}})
    today = dt.date.today()
    stay_after = (today - dt.timedelta(days=args.days_back)).isoformat()
    stay_before = (today + dt.timedelta(days=args.days_forward)).isoformat()
    listing_ids = args.listing_id or os.environ.get('EVOLVE_LISTING_IDS', '478624').split(',')
    bookings = list_bookings(args.account_id, [x for x in listing_ids if x], stay_after, stay_before)
    hs = hosp_session()
    results = []
    missing_email = []
    for item in bookings:
        internal_id = item['internalId']
        if sync_state['processed'].get(internal_id) == item.get('displayStatus'):
            continue
        booking = get_booking(internal_id)
        if (booking.get('displayStatus') or '').upper() == 'CANCELLED':
            prop_name = (booking.get('listing') or {}).get('propertyName')
            prop_id = property_map.get(prop_name)
            code = booking.get('id') or booking.get('internalId')
            if args.sync_cancelled and prop_id and code:
                try:
                    matches = find_hospitable_by_code(hs, prop_id, code)
                    if matches:
                        if args.dry_run:
                            results.append({'internal_id': internal_id, 'status': 'dry_run_cancel', 'matches': [m.get('uuid') for m in matches], 'auto_unblock': bool(args.auto_unblock_on_cancel)})
                        else:
                            out = []
                            for m in matches:
                                out.append(cancel_hospitable_reservation(hs, m['uuid']))
                            if args.auto_unblock_on_cancel:
                                try:
                                    set_property_calendar_availability(hs, prop_id, booking.get('checkInDate'), booking.get('checkOutDate'), available=True)
                                except Exception as ue:
                                    results.append({'internal_id': internal_id, 'status': 'cancelled_in_hospitable_unblock_error', 'count': len(out), 'reason': str(ue)})
                                    out = None
                            if out is not None:
                                results.append({'internal_id': internal_id, 'status': 'cancelled_in_hospitable', 'count': len(out), 'auto_unblock': bool(args.auto_unblock_on_cancel)})
                    else:
                        results.append({'internal_id': internal_id, 'status': 'cancel_not_found_in_hospitable'})
                except Exception as e:
                    results.append({'internal_id': internal_id, 'status': 'cancel_error', 'reason': str(e)})
            else:
                results.append({'internal_id': internal_id, 'status': 'skipped_cancelled'})
            sync_state['processed'][internal_id] = 'CANCELLED'
            continue
        canon = canonical_from_evolve(booking, property_map)
        res = sync_one(hs, canon, dry_run=args.dry_run)
        results.append({'internal_id': internal_id, 'reservation_code': canon['reservation_code'], 'result': res, 'missing_email': canon['missing_email']})
        if res.get('status') in ('created', 'skipped_duplicate'):
            sync_state['processed'][internal_id] = booking.get('displayStatus')
        if canon['missing_email']:
            missing_email.append(canon)
    save_json(sync_state_path, sync_state)
    report = {
        'ran_at_utc': dt.datetime.utcnow().isoformat() + 'Z',
        'stay_after': stay_after,
        'stay_before': stay_before,
        'bookings_seen': len(bookings),
        'results': results,
    }
    report_path = LOG_DIR / f'evolve_hospitable_sync_{TODAY}.json'
    save_json(report_path, report)
    missing_path = write_missing_email_report(missing_email)
    print(json.dumps({'report': str(report_path), 'missing_email_report': str(missing_path) if missing_path else None, 'results': results}, indent=2))

if __name__ == '__main__':
    main()
