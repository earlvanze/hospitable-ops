#!/usr/bin/env python3
import argparse
import json
import os
import sys
from pathlib import Path
from datetime import date, datetime, timedelta

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from hospitable_property_matcher import resolve_property, load_properties, extract_features

try:
    import requests
except Exception:
    print('Missing dependency: requests', file=sys.stderr)
    sys.exit(2)

API_BASE = os.environ.get('HOSPITABLE_API_BASE', 'https://api.hospitable.com')


def _session():
    s = requests.Session()
    s.headers.update({
        'accept': 'application/json, text/plain, */*',
        'content-type': 'application/json',
        'origin': 'https://my.hospitable.com',
        'referer': 'https://my.hospitable.com/',
        'user-agent': 'Mozilla/5.0 OpenClaw Hospitable Ops',
    })
    cookie = os.environ.get('HOSPITABLE_COOKIE')
    bearer = os.environ.get('HOSPITABLE_BEARER')
    if cookie:
        s.headers['cookie'] = cookie
    if bearer:
        s.headers['authorization'] = bearer if bearer.lower().startswith('bearer ') else f'Bearer {bearer}'
    if not cookie and not bearer:
        raise SystemExit('Missing HOSPITABLE_COOKIE or HOSPITABLE_BEARER')
    return s


def _parse_date(s: str) -> date:
    return datetime.strptime(s, '%Y-%m-%d').date()


def _format_date(d: date) -> str:
    return d.strftime('%Y-%m-%d')

def _add_property_selector_args(parser):
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--property-id', type=int, help='Exact Hospitable property id')
    group.add_argument('--property', help='Property/unit query, e.g. "88.3" or "88 Madison 3"')


def _add_prefixed_property_selector_args(parser, prefix, required=True, help_label='Property/unit query'):
    group = parser.add_mutually_exclusive_group(required=required)
    dashed = prefix.replace('_', '-')
    group.add_argument(f'--{dashed}-property-id', dest=f'{prefix}_property_id', type=int, help=f'Exact Hospitable {prefix.replace("_", " ")} property id')
    group.add_argument(f'--{dashed}-property', dest=f'{prefix}_property', help=help_label)


def _resolve_property_arg(args):
    if getattr(args, 'property_id', None) is not None:
        return int(args.property_id), {'resolved': int(args.property_id), 'high_confidence': True, 'matches': [{'property_id': int(args.property_id), 'matched_on': 'property_id', 'score': 999.0, 'reasons': ['explicit_property_id']}]}
    query = getattr(args, 'property', None)
    pid, match = resolve_property(query)
    if pid is None:
        top = []
        for m in (match.get('matches') or [])[:3]:
            top.append(f"{m['property_id']}:{m['name']} ({m['score']})")
        suffix = f" Top matches: {'; '.join(top)}" if top else ''
        raise SystemExit(f'Could not confidently resolve property from query: {query!r}.{suffix}')
    return int(pid), match


def _resolve_property_value(property_id=None, property_query=None, label='property'):
    if property_id is not None:
        return int(property_id), {'resolved': int(property_id), 'high_confidence': True, 'matches': [{'property_id': int(property_id), 'matched_on': f'{label}_id', 'score': 999.0, 'reasons': [f'explicit_{label}_id']}]}
    pid, match = resolve_property(property_query)
    if pid is None:
        top = []
        for m in (match.get('matches') or [])[:3]:
            top.append(f"{m['property_id']}:{m['name']} ({m['score']})")
        suffix = f" Top matches: {'; '.join(top)}" if top else ''
        raise SystemExit(f'Could not confidently resolve {label} from query: {property_query!r}.{suffix}')
    return int(pid), match


def _enumerate_dates(start: str, end: str):
    start_d = _parse_date(start)
    end_d = _parse_date(end)
    if end_d < start_d:
        raise ValueError(f'end date {end} is before start date {start}')
    out = []
    cur = start_d
    while cur <= end_d:
        out.append(_format_date(cur))
        cur += timedelta(days=1)
    return out


def resolve_date_window(start: str, end: str = None, checkout_date: str = None, nights: int = None):
    provided = [end is not None, checkout_date is not None, nights is not None]
    if sum(provided) != 1:
        raise ValueError('Provide exactly one of: end, checkout_date, nights')

    start_d = _parse_date(start)
    if nights is not None:
        if int(nights) < 1:
            raise ValueError('nights must be >= 1')
        end_d = start_d + timedelta(days=int(nights) - 1)
    elif checkout_date is not None:
        checkout_d = _parse_date(checkout_date)
        if checkout_d <= start_d:
            raise ValueError('checkout_date must be after start date')
        end_d = checkout_d - timedelta(days=1)
    else:
        end_d = _parse_date(end)
        if end_d < start_d:
            raise ValueError('end date must be on/after start date')

    requested_nights = _enumerate_dates(_format_date(start_d), _format_date(end_d))
    return {
        'start': _format_date(start_d),
        'end': _format_date(end_d),
        'checkout_date': _format_date(end_d + timedelta(days=1)),
        'nights_count': len(requested_nights),
        'requested_nights': requested_nights,
    }


def _post(path, payload):
    r = _session().post(f'{API_BASE}{path}', json=payload, timeout=30)
    if not r.ok:
        raise RuntimeError(f'{path} HTTP {r.status_code}: {r.text[:1200]}')
    return r.json()


def _put(path, payload):
    r = _session().put(f'{API_BASE}{path}', json=payload, timeout=30)
    if not r.ok:
        raise RuntimeError(f'{path} HTTP {r.status_code}: {r.text[:1200]}')
    return r.json()


def _get(path):
    r = _session().get(f'{API_BASE}{path}', timeout=30)
    if not r.ok:
        raise RuntimeError(f'{path} HTTP {r.status_code}: {r.text[:1200]}')
    return r.json()


def get_pricing_and_availability(property_id, start, end):
    window = resolve_date_window(start=start, end=end)
    path = (
        f'/v1/properties/{property_id}/prices-and-availability'
        f'?start={window["start"]}&end={window["end"]}'
        '&flags[events_only]=true'
        '&flags[with_restrictions]=true'
        '&flags[with_calendar_rulesets]=true'
    )
    data = _get(path)
    return data.get('data', {})


def verify_availability(property_id, start, end, expected_available):
    window = resolve_date_window(start=start, end=end)
    data = get_pricing_and_availability(property_id, window['start'], window['end'])
    day_status = []
    verified_nights = []
    mismatches = []
    for night in window['requested_nights']:
        day = data.get(night, {})
        sources = day.get('sources') or []
        simplified_sources = []
        for src in sources:
            simplified_sources.append({
                'type': src.get('type'),
                'source': src.get('source'),
                'source_type': src.get('source_type'),
                'date': src.get('date'),
            })
        row = {
            'date': night,
            'available': day.get('available'),
            'sources': simplified_sources,
        }
        day_status.append(row)
        if day.get('available') == bool(expected_available):
            verified_nights.append(night)
        else:
            mismatches.append(row)
    return {
        'ok': len(mismatches) == 0,
        'expected_available': bool(expected_available),
        'requested_nights': window['requested_nights'],
        'verified_nights': verified_nights,
        'mismatches': mismatches,
        'day_status': day_status,
        'nights_count': window['nights_count'],
    }


def block_unblock(property_id, start, end=None, available=False, dry_run=False, checkout_date=None, nights=None, verify=True):
    window = resolve_date_window(start=start, end=end, checkout_date=checkout_date, nights=nights)
    path = f'/v1/properties/{property_id}/calendar?flags[events_only]=true'
    payload = {'start': window['start'], 'end': window['end'], 'available': bool(available)}

    base = {
        'property_id': int(property_id),
        'path': path,
        'payload': payload,
        'requested_nights': window['requested_nights'],
        'nights_count': window['nights_count'],
        'checkout_date': window['checkout_date'],
        'verify_requested': bool(verify and not dry_run),
    }

    if dry_run:
        return {'dry_run': True, **base}

    raw = _post(path, payload)
    out = {'write_success': bool(raw.get('success', True)), 'raw': raw, **base}
    if verify:
        verification = verify_availability(property_id, window['start'], window['end'], expected_available=available)
        out['verification'] = verification
        out['success'] = out['write_success'] and verification['ok']
    else:
        out['success'] = out['write_success']
    return out


def find_by_code(property_id, code):
    path = f'/v1/reservations/?starts_or_ends_between=2025-01-01_2027-12-31&timezones=false&property_ids={property_id}&calendar_blockable=true&include_family_reservations=true'
    data = _get(path)
    rows = [r for r in data.get('data', []) if str(r.get('code', '')).strip() == str(code).strip()]
    return {'matches': rows, 'count': len(rows)}


def _reservations_for_property(property_id, starts_or_ends_between='2025-01-01_2027-12-31'):
    path = f'/v1/reservations/?starts_or_ends_between={starts_or_ends_between}&timezones=false&property_ids={property_id}&calendar_blockable=true&include_family_reservations=true'
    data = _get(path)
    return data.get('data', [])


def _guest_match_score(query: str, guest_name: str):
    q = (query or '').strip().lower()
    g = (guest_name or '').strip().lower()
    if not q or not g:
        return 0
    if q == g:
        return 300
    q_parts = [x for x in q.split() if x]
    g_parts = [x for x in g.split() if x]
    if q_parts and all(part in g_parts for part in q_parts):
        return 200 + len(q_parts) * 10
    if g.startswith(q):
        return 170
    if q in g:
        return 140
    if q_parts and g_parts and q_parts[0] == g_parts[0]:
        return 120
    return 0


def find_by_guest_first(property_id, first_name):
    rows = []
    target = (first_name or '').strip().lower()
    for r in _reservations_for_property(property_id):
        guest = (r.get('guest') or '').strip()
        first = guest.split(' ')[0].lower() if guest else ''
        if first and first == target:
            rows.append(r)
    return {'matches': rows, 'count': len(rows), 'query_first_name': first_name}


def find_by_guest(property_id, guest_query):
    scored = []
    for r in _reservations_for_property(property_id):
        guest = (r.get('guest') or '').strip()
        score = _guest_match_score(guest_query, guest)
        if score > 0:
            item = dict(r)
            item['_guest_match_score'] = score
            scored.append(item)
    scored.sort(key=lambda r: (-r.get('_guest_match_score', 0), r.get('checkin', '')))
    return {'matches': scored, 'count': len(scored), 'query_guest': guest_query}


def find_current_stay(property_id, on_date=None):
    target = _parse_date(on_date) if on_date else datetime.now().date()
    rows = []
    for r in _reservations_for_property(property_id):
        status = (r.get('status') or '').lower()
        if status not in ('accepted', 'modified', 'pending', 'pre accepted'):
            continue
        checkin = r.get('checkin')
        checkout = r.get('checkout')
        if not checkin or not checkout:
            continue
        start = datetime.fromisoformat(checkin).date()
        end_exclusive = datetime.fromisoformat(checkout).date()
        if start <= target < end_exclusive:
            rows.append(r)
    rows.sort(key=lambda r: r.get('checkin', ''))
    return {'matches': rows, 'count': len(rows), 'on_date': _format_date(target)}


def _property_record(property_id, properties=None):
    props = properties if properties is not None else load_properties()
    for p in props:
        if int(p.get('property_id')) == int(property_id):
            return p
    return {'property_id': int(property_id), 'name': str(property_id), 'aliases': []}


GENERIC_RELATION_TOKENS = {'bd', 'ba', 'ave', 'st', 'rd', 'ln', 'dr', 'fl'}


def _relation_tokens(prop):
    vals = [prop.get('name', '')] + list(prop.get('aliases', []) or [])
    tokens = set()
    for v in vals:
        for tok in extract_features(v).get('tokens', []):
            if tok.isdigit() or '.' in tok or tok in GENERIC_RELATION_TOKENS:
                continue
            tokens.add(tok)
    return tokens


def _candidate_relation(source_prop, candidate_prop):
    sf = extract_features(source_prop.get('name', ''))
    cf = extract_features(candidate_prop.get('name', ''))
    score = 0
    reasons = []
    if sf.get('building') and cf.get('building'):
        if sf['building'] == cf['building']:
            score += 120
            reasons.append('same_building')
        else:
            try:
                dist = abs(int(sf['building']) - int(cf['building']))
                if dist <= 6:
                    score += max(0, 30 - dist * 4)
                    reasons.append('nearby_building')
            except Exception:
                pass
    shared_tokens = sorted(_relation_tokens(source_prop) & _relation_tokens(candidate_prop))
    if shared_tokens:
        score += 60
        reasons.append('shared_alias_tokens:' + ','.join(shared_tokens[:4]))
    if sf.get('beds') and cf.get('beds'):
        if sf['beds'] == cf['beds']:
            score += 18
            reasons.append('same_beds')
        elif int(cf['beds']) >= int(sf['beds']):
            score += 10
            reasons.append('target_not_smaller_beds')
    if sf.get('baths') and cf.get('baths') and int(cf['baths']) >= int(sf['baths']):
        score += 8
        reasons.append('target_not_smaller_baths')
    if sf.get('unit') and cf.get('unit') and sf['unit'] != cf['unit']:
        score += 5
    return score, reasons


def _remaining_move_window(reservation_row, on_date=None):
    checkin = datetime.fromisoformat(reservation_row['checkin']).date()
    checkout_date = datetime.fromisoformat(reservation_row['checkout']).date()
    target_date = _parse_date(on_date) if on_date else datetime.now().date()
    move_start = max(checkin, target_date)
    if move_start >= checkout_date:
        raise ValueError('Move date is on/after checkout date; no remaining nights to move')
    return resolve_date_window(start=_format_date(move_start), checkout_date=_format_date(checkout_date))


def _reservation_summary(row):
    return {
        'uuid': row.get('uuid'),
        'code': row.get('code'),
        'guest': row.get('guest'),
        'status': row.get('status'),
        'platform': row.get('platform'),
        'channel': row.get('channel'),
        'checkin': row.get('checkin'),
        'checkout': row.get('checkout'),
        'phone': row.get('phone'),
        'email': row.get('email'),
        'listing': row.get('listing'),
        'supports': row.get('supports'),
    }


def _candidate_availability_row(candidate_prop, start, end):
    verification = verify_availability(int(candidate_prop['property_id']), start, end, expected_available=True)
    relation_score, relation_reasons = _candidate_relation(candidate_prop['_source_property'], candidate_prop)
    return {
        'property_id': int(candidate_prop['property_id']),
        'name': candidate_prop.get('name'),
        'relation_score': relation_score,
        'relation_reasons': relation_reasons,
        'verification': verification,
        'available': verification.get('ok', False),
    }


def move_guest_plan(source_property_id, on_date=None, guest=None, reservation_code=None, target_property_id=None, candidate_limit=8):
    properties = load_properties()
    source_prop = _property_record(source_property_id, properties=properties)

    if reservation_code:
        found = find_by_code(source_property_id, reservation_code)
        if found.get('count', 0) == 0:
            raise ValueError(f'No reservation found in source property for code {reservation_code}')
        reservation = found['matches'][0]
        resolution_mode = 'reservation_code'
    elif guest:
        found = find_by_guest(source_property_id, guest)
        if found.get('count', 0) == 0:
            raise ValueError(f'No reservation found in source property for guest query {guest}')
        reservation = found['matches'][0]
        resolution_mode = 'guest_query'
    else:
        found = find_current_stay(source_property_id, on_date=on_date)
        if found.get('count', 0) == 0:
            raise ValueError('No active stay found in source property for requested date')
        reservation = found['matches'][0]
        resolution_mode = 'current_stay'

    window = _remaining_move_window(reservation, on_date=on_date)

    if target_property_id is not None:
        candidate_props = [_property_record(target_property_id, properties=properties)]
    else:
        scored = []
        for prop in properties:
            pid = int(prop.get('property_id'))
            if pid == int(source_property_id):
                continue
            score, reasons = _candidate_relation(source_prop, prop)
            prop_copy = dict(prop)
            prop_copy['_pre_score'] = score
            prop_copy['_pre_reasons'] = reasons
            prop_copy['_source_property'] = source_prop
            scored.append(prop_copy)
        scored.sort(key=lambda p: (-p.get('_pre_score', 0), p.get('name', '')))
        candidate_props = scored[:max(1, int(candidate_limit))]

    normalized_candidates = []
    for prop in candidate_props:
        prop = dict(prop)
        prop['_source_property'] = source_prop
        normalized_candidates.append(prop)

    candidates = [_candidate_availability_row(prop, window['start'], window['end']) for prop in normalized_candidates]
    candidates.sort(key=lambda c: (not c.get('available', False), -c.get('relation_score', 0), c.get('name', '')))

    target_candidate = candidates[0] if (target_property_id is not None and candidates) else None

    return {
        'mode': resolution_mode,
        'do_not_block_destination': True,
        'source_property': {
            'property_id': int(source_property_id),
            'name': source_prop.get('name'),
        },
        'reservation': _reservation_summary(reservation),
        'move_window': window,
        'requested_on_date': _format_date(_parse_date(on_date)) if on_date else _format_date(datetime.now().date()),
        'target_candidate': target_candidate,
        'candidate_targets': candidates,
        'alteration_scaffold': {
            'ready_for_har_learning': True,
            'status': 'awaiting_reservation_alteration_har',
            'do_not_block_destination': True,
            'reservation_uuid': reservation.get('uuid'),
            'reservation_code': reservation.get('code'),
            'source_property_id': int(source_property_id),
            'source_property_name': source_prop.get('name'),
            'target_property_id': target_candidate.get('property_id') if target_candidate else (int(target_property_id) if target_property_id is not None else None),
            'remaining_start': window['start'],
            'remaining_end': window['end'],
            'checkout_date': window['checkout_date'],
            'sequence': [
                'resolve active reservation in source unit',
                'verify target availability only (do not block destination)',
                'alter reservation onto target unit/listing',
                'after successful move, optionally block source unit',
                'write internal note and send updated guest access details',
            ],
            'note_template': f"Guest {reservation.get('guest') or ''} moved from {source_prop.get('name')} starting {window['start']} through checkout {window['checkout_date']}. Destination unit: <target>. Reason: <reason>. Do not pre-block destination before alteration.",
        },
    }


def cancel_reservation(reservation_uuid, initiator='guest', dry_run=False):
    quote_path = f'/v1/reservations/{reservation_uuid}/cancel-quote'
    cancel_path = f'/v1/reservations/{reservation_uuid}/cancel'
    if dry_run:
        return {
            'dry_run': True,
            'quote_path': quote_path,
            'quote_payload': {'initiator': initiator},
            'cancel_path': cancel_path,
            'cancel_payload': {'initiated_by': initiator},
        }
    quote = _post(quote_path, {'initiator': initiator})
    cancel = _put(cancel_path, {'initiated_by': initiator})
    return {'quote': quote, 'cancel': cancel}


def update_manual(booking_uuid, payload, dry_run=False):
    path = f'/v1/bookings/manual/{booking_uuid}'
    if dry_run:
        return {'dry_run': True, 'path': path, 'payload': payload}
    return _put(path, payload)


def _add_date_window_args(parser):
    parser.add_argument('--start', required=True, help='YYYY-MM-DD first night to modify')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--end', help='YYYY-MM-DD inclusive last night to modify')
    group.add_argument('--checkout-date', help='YYYY-MM-DD checkout date (exclusive upper bound)')
    group.add_argument('--nights', type=int, help='Number of nights starting at --start')


def main():
    ap = argparse.ArgumentParser(description='Hospitable Ops API utility')
    sub = ap.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('block-dates')
    _add_property_selector_args(p)
    _add_date_window_args(p)
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--no-verify', action='store_true', help='Skip post-write availability verification')

    p2 = sub.add_parser('unblock-dates')
    _add_property_selector_args(p2)
    _add_date_window_args(p2)
    p2.add_argument('--dry-run', action='store_true')
    p2.add_argument('--no-verify', action='store_true', help='Skip post-write availability verification')

    p2b = sub.add_parser('verify-dates')
    _add_property_selector_args(p2b)
    _add_date_window_args(p2b)
    p2b.add_argument('--available', choices=['true', 'false'], required=True, help='Expected availability state for every requested night')

    p3 = sub.add_parser('find-by-code')
    _add_property_selector_args(p3)
    p3.add_argument('--code', required=True)

    p3b = sub.add_parser('find-by-guest-first')
    _add_property_selector_args(p3b)
    p3b.add_argument('--first-name', required=True)

    p3c = sub.add_parser('find-by-guest')
    _add_property_selector_args(p3c)
    p3c.add_argument('--guest', required=True)

    p3d = sub.add_parser('find-current-stay')
    _add_property_selector_args(p3d)
    p3d.add_argument('--on-date', help='YYYY-MM-DD date to test occupancy for; defaults to today')

    p3e = sub.add_parser('move-guest-plan')
    _add_prefixed_property_selector_args(p3e, 'source', required=True, help_label='Source property/unit query, e.g. 88.3')
    _add_prefixed_property_selector_args(p3e, 'target', required=False, help_label='Optional target property/unit query')
    p3e.add_argument('--on-date', help='YYYY-MM-DD move date; defaults to today')
    p3e.add_argument('--guest', help='Optional guest query to disambiguate reservation')
    p3e.add_argument('--reservation-code', help='Optional reservation code to force reservation selection')
    p3e.add_argument('--candidate-limit', type=int, default=8, help='Number of nearby candidate properties to verify when no explicit target is supplied')

    p4 = sub.add_parser('cancel-reservation')
    p4.add_argument('--reservation-uuid', required=True)
    p4.add_argument('--initiator', default='guest')
    p4.add_argument('--dry-run', action='store_true')

    p5 = sub.add_parser('update-manual-booking')
    p5.add_argument('--booking-uuid', required=True)
    p5.add_argument('--json-file', required=True)
    p5.add_argument('--dry-run', action='store_true')

    args = ap.parse_args()

    if args.cmd in ('block-dates', 'unblock-dates', 'verify-dates', 'find-by-code', 'find-by-guest-first', 'find-by-guest', 'find-current-stay'):
        resolved_property_id, property_resolution = _resolve_property_arg(args)

    if args.cmd == 'block-dates':
        out = block_unblock(
            resolved_property_id,
            args.start,
            end=args.end,
            checkout_date=args.checkout_date,
            nights=args.nights,
            available=False,
            dry_run=args.dry_run,
            verify=not args.no_verify,
        )
        out['property_resolution'] = property_resolution
    elif args.cmd == 'unblock-dates':
        out = block_unblock(
            resolved_property_id,
            args.start,
            end=args.end,
            checkout_date=args.checkout_date,
            nights=args.nights,
            available=True,
            dry_run=args.dry_run,
            verify=not args.no_verify,
        )
        out['property_resolution'] = property_resolution
    elif args.cmd == 'verify-dates':
        window = resolve_date_window(args.start, end=args.end, checkout_date=args.checkout_date, nights=args.nights)
        out = {
            'property_id': resolved_property_id,
            'property_resolution': property_resolution,
            'requested_nights': window['requested_nights'],
            'nights_count': window['nights_count'],
            'checkout_date': window['checkout_date'],
            'verification': verify_availability(
                resolved_property_id,
                window['start'],
                window['end'],
                expected_available=(args.available == 'true'),
            ),
        }
    elif args.cmd == 'find-by-code':
        out = find_by_code(resolved_property_id, args.code)
        out['property_id'] = resolved_property_id
        out['property_resolution'] = property_resolution
    elif args.cmd == 'find-by-guest-first':
        out = find_by_guest_first(resolved_property_id, args.first_name)
        out['property_id'] = resolved_property_id
        out['property_resolution'] = property_resolution
    elif args.cmd == 'find-by-guest':
        out = find_by_guest(resolved_property_id, args.guest)
        out['property_id'] = resolved_property_id
        out['property_resolution'] = property_resolution
    elif args.cmd == 'find-current-stay':
        out = find_current_stay(resolved_property_id, on_date=args.on_date)
        out['property_id'] = resolved_property_id
        out['property_resolution'] = property_resolution
    elif args.cmd == 'move-guest-plan':
        source_property_id, source_resolution = _resolve_property_value(getattr(args, 'source_property_id', None), getattr(args, 'source_property', None), label='source property')
        target_resolution = None
        target_property_id = None
        if getattr(args, 'target_property_id', None) is not None or getattr(args, 'target_property', None):
            target_property_id, target_resolution = _resolve_property_value(getattr(args, 'target_property_id', None), getattr(args, 'target_property', None), label='target property')
        out = move_guest_plan(
            source_property_id=source_property_id,
            on_date=args.on_date,
            guest=args.guest,
            reservation_code=args.reservation_code,
            target_property_id=target_property_id,
            candidate_limit=args.candidate_limit,
        )
        out['source_property_resolution'] = source_resolution
        if target_resolution is not None:
            out['target_property_resolution'] = target_resolution
    elif args.cmd == 'cancel-reservation':
        out = cancel_reservation(args.reservation_uuid, initiator=args.initiator, dry_run=args.dry_run)
    elif args.cmd == 'update-manual-booking':
        payload = json.load(open(args.json_file))
        out = update_manual(args.booking_uuid, payload, dry_run=args.dry_run)
    else:
        raise SystemExit('Unknown command')

    print(json.dumps(out, indent=2))


if __name__ == '__main__':
    main()
