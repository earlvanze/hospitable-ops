#!/usr/bin/env python3
import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from hospitable_property_matcher import match_property

ALIASES_PATH = Path('/home/umbrel/.openclaw/workspace/ops/hospitable_property_aliases.json')
OPS_SCRIPT = '/home/umbrel/.openclaw/workspace/skills/hospitable-ops/scripts/hospitable_ops.py'
OPS_PYTHON = '/home/umbrel/.openclaw/workspace/.venv-sync/bin/python'


def norm(s: str) -> str:
    return re.sub(r'\s+', ' ', s.strip().lower())


def load_aliases():
    if not ALIASES_PATH.exists():
        return []
    data = json.loads(ALIASES_PATH.read_text())
    return data.get('properties', [])


def resolve_property_id(text: str):
    props = load_aliases()
    result = match_property(text, properties=props, min_score=110.0)
    pid = result.get('resolved')
    if pid is not None:
        best = (result.get('matches') or [{}])[0]
        return int(pid), best.get('matched_on') or 'fuzzy-match'
    return None, None


def parse_dates(text: str):
    iso = re.findall(r'\b(20\d{2}-\d{2}-\d{2})\b', text)
    if len(iso) >= 2:
        return {'kind': 'inclusive-range', 'start': iso[0], 'end': iso[1]}
    md = re.findall(r'\b(\d{1,2}/\d{1,2}(?:/\d{2,4})?)\b', text)
    if len(md) >= 2:
        def conv(x):
            if x.count('/') == 1:
                x = f"{x}/{datetime.utcnow().year}"
            dt = datetime.strptime(x, '%m/%d/%Y')
            return dt.strftime('%Y-%m-%d')
        return {'kind': 'inclusive-range', 'start': conv(md[0]), 'end': conv(md[1])}
    return None


def parse_relative_nights(text: str, base_date: datetime = None):
    t = norm(text)
    base = (base_date or datetime.now()).date()

    if 'tonight and tomorrow night' in t:
        return {'kind': 'nights', 'start': base.strftime('%Y-%m-%d'), 'nights': 2}
    if 'tomorrow night and the following night' in t or 'tomorrow night and next night' in t:
        return {'kind': 'nights', 'start': (base + timedelta(days=1)).strftime('%Y-%m-%d'), 'nights': 2}
    if 'tonight' in t:
        return {'kind': 'nights', 'start': base.strftime('%Y-%m-%d'), 'nights': 1}
    if 'tomorrow night' in t:
        return {'kind': 'nights', 'start': (base + timedelta(days=1)).strftime('%Y-%m-%d'), 'nights': 1}
    return None


def parse_reservation_code(text: str):
    m = re.search(r'(?:code|reservation)\s*[:#]?\s*([A-Za-z0-9]{6,})', text, re.I)
    if m:
        return m.group(1)
    cands = re.findall(r'\b[A-Za-z0-9]{6,}\b', text)
    if cands:
        return cands[-1]
    return None


def parse_email(text: str):
    m = re.search(r'([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})', text)
    return m.group(1) if m else None


def parse_guest_hint(text: str):
    patterns = [
        r'\bfind\s+(.+?)\s+in\s+',
        r'\bguest\s+(.+?)\s+in\s+',
        r'\bmove\s+guest\s+(.+?)\s+in\s+',
        r'\bmove\s+(.+?)\s+in\s+',
    ]
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if m:
            guess = re.sub(r'\s+', ' ', m.group(1)).strip(' ,.-')
            if guess:
                return guess
    return None


def parse_move_target_hint(text: str):
    m = re.search(r'\bto\s+(.+)$', text, re.I)
    if not m:
        return None
    guess = re.sub(r'\s+', ' ', m.group(1)).strip(' ,.-')
    return guess or None


def parse_move_source_hint(text: str):
    patterns = [
        r'\bmove\s+guest\s+in\s+(.+?)(?:\s+to\s+.+)?$',
        r'\bmove\s+in\s+(.+?)(?:\s+to\s+.+)?$',
    ]
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if m:
            guess = re.sub(r'\s+', ' ', m.group(1)).strip(' ,.-')
            if guess:
                return guess
    return None


def build_plan(text: str):
    t = norm(text)
    relative = parse_relative_nights(text)
    explicit = parse_dates(text)

    move_source = parse_move_source_hint(text) if 'move' in t else None
    property_lookup_text = move_source or text
    prop_id, prop_match = resolve_property_id(property_lookup_text)

    if 'unblock' in t or 'open calendar' in t:
        if not prop_id:
            return {'error': 'Need property + date window for unblock'}
        if relative:
            return {
                'action': 'unblock-dates',
                'args': ['--property-id', str(prop_id), '--start', relative['start'], '--nights', str(relative['nights'])],
                'property_match': prop_match,
            }
        if explicit:
            return {
                'action': 'unblock-dates',
                'args': ['--property-id', str(prop_id), '--start', explicit['start'], '--end', explicit['end']],
                'property_match': prop_match,
            }
        return {'error': 'Need property + date window for unblock'}

    if 'block' in t:
        if not prop_id:
            return {'error': 'Need property + date window for block'}
        if relative:
            return {
                'action': 'block-dates',
                'args': ['--property-id', str(prop_id), '--start', relative['start'], '--nights', str(relative['nights'])],
                'property_match': prop_match,
            }
        if explicit:
            return {
                'action': 'block-dates',
                'args': ['--property-id', str(prop_id), '--start', explicit['start'], '--end', explicit['end']],
                'property_match': prop_match,
            }
        return {'error': 'Need property + date window for block'}

    if 'cancel' in t and ('reservation' in t or 'booking' in t or 'code' in t):
        code = parse_reservation_code(text)
        if not (prop_id and code):
            return {'error': 'Need property + reservation code for cancel'}
        return {'action': 'cancel-by-code', 'args': ['--property-id', str(prop_id), '--code', code], 'property_match': prop_match}

    guest_hint = parse_guest_hint(text)
    if 'find' in t and guest_hint and prop_id:
        return {'action': 'find-by-guest', 'args': ['--property-id', str(prop_id), '--guest', guest_hint], 'property_match': prop_match}

    move_target = parse_move_target_hint(text)
    if ('move guest in' in t or re.search(r'\bmove\s+in\s+', t)) and prop_id:
        args = ['--source-property-id', str(prop_id)]
        if guest_hint and guest_hint.lower() != 'guest':
            args += ['--guest', guest_hint]
        if move_target:
            target_prop_id, _ = resolve_property_id(move_target)
            if target_prop_id:
                args += ['--target-property-id', str(target_prop_id)]
        return {'action': 'move-guest-plan', 'args': args, 'property_match': prop_match}

    if 'find' in t and 'first name' in t:
        m = re.search(r'first\s+name\s+([A-Za-z\'\-]+)', text, re.I)
        first = m.group(1) if m else None
        if not (prop_id and first):
            return {'error': 'Need property + guest first name for lookup'}
        return {'action': 'find-by-guest-first', 'args': ['--property-id', str(prop_id), '--first-name', first], 'property_match': prop_match}

    if 'update' in t and 'email' in t:
        code = parse_reservation_code(text)
        email = parse_email(text)
        if not (prop_id and code and email):
            return {'error': 'Need property + reservation code + email for update'}
        return {'action': 'update-email-by-code', 'args': ['--property-id', str(prop_id), '--code', code, '--email', email], 'property_match': prop_match}

    return {'error': 'Could not map request. Supported intents: block/unblock dates, cancel by code, update email by code, find guest in unit, move guest in unit, and move guest to a target unit.'}


def run_json(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return json.loads(p.stdout)


def execute(plan, apply=False):
    if 'error' in plan:
        return plan
    action = plan['action']
    if action in ('block-dates', 'unblock-dates'):
        cmd = [OPS_PYTHON, OPS_SCRIPT, action] + plan['args']
        if not apply:
            cmd.append('--dry-run')
        out = run_json(cmd)
        return {'plan': plan, 'executed': True, 'result': out}

    if action == 'find-by-guest-first':
        cmd = [OPS_PYTHON, OPS_SCRIPT, 'find-by-guest-first'] + plan['args']
        out = run_json(cmd)
        return {'plan': plan, 'executed': True, 'result': out}

    if action == 'find-by-guest':
        cmd = [OPS_PYTHON, OPS_SCRIPT, 'find-by-guest'] + plan['args']
        out = run_json(cmd)
        return {'plan': plan, 'executed': True, 'result': out}

    if action == 'find-current-stay':
        cmd = [OPS_PYTHON, OPS_SCRIPT, 'find-current-stay'] + plan['args']
        out = run_json(cmd)
        return {'plan': plan, 'executed': True, 'result': out}

    if action == 'move-guest-plan':
        cmd = [OPS_PYTHON, OPS_SCRIPT, 'move-guest-plan'] + plan['args']
        out = run_json(cmd)
        return {'plan': plan, 'executed': True, 'result': out}

    if action == 'cancel-by-code':
        prop_id = plan['args'][1]
        code = plan['args'][3]
        found = run_json([OPS_PYTHON, OPS_SCRIPT, 'find-by-code', '--property-id', prop_id, '--code', code])
        if found.get('count', 0) == 0:
            return {'plan': plan, 'executed': False, 'error': 'No reservation found by code'}
        uuid = found['matches'][0]['uuid']
        cmd = [OPS_PYTHON, OPS_SCRIPT, 'cancel-reservation', '--reservation-uuid', uuid]
        if not apply:
            cmd.append('--dry-run')
        out = run_json(cmd)
        return {'plan': plan, 'reservation_uuid': uuid, 'executed': True, 'result': out}

    if action == 'update-email-by-code':
        prop_id = plan['args'][1]
        code = plan['args'][3]
        email = plan['args'][5]
        found = run_json([OPS_PYTHON, OPS_SCRIPT, 'find-by-code', '--property-id', prop_id, '--code', code])
        if found.get('count', 0) == 0:
            return {'plan': plan, 'executed': False, 'error': 'No reservation found by code'}
        match = found['matches'][0]
        booking_uuid = match['uuid']
        payload = {
            'id': booking_uuid,
            'reservationCode': match.get('code'),
            'platform': 'manual',
            'channel': match.get('channel') or 'manual',
            'propertyId': int(prop_id),
            'checkInDate': str(match.get('checkin', ''))[:10],
            'checkOutDate': str(match.get('checkout', ''))[:10],
            'guestName': match.get('guest') or '',
            'guestFirstName': (match.get('guest') or '').split(' ')[0] if match.get('guest') else '',
            'guestLastName': ' '.join((match.get('guest') or '').split(' ')[1:]) if match.get('guest') else '',
            'guestEmail': email,
            'guestPhone': match.get('phone') or '',
            'fees': []
        }
        tmp = Path('/tmp/hospitable_update_email_payload.json')
        tmp.write_text(json.dumps(payload))
        cmd = [OPS_PYTHON, OPS_SCRIPT, 'update-manual-booking', '--booking-uuid', booking_uuid, '--json-file', str(tmp)]
        if not apply:
            cmd.append('--dry-run')
        out = run_json(cmd)
        return {'plan': plan, 'booking_uuid': booking_uuid, 'executed': True, 'result': out}

    return {'plan': plan, 'executed': False, 'error': 'Unsupported action'}


def main():
    ap = argparse.ArgumentParser(description='Map natural-language Hospitable requests to deterministic API ops')
    ap.add_argument('request', help='Natural-language request text')
    ap.add_argument('--apply', action='store_true', help='Execute write action (default dry-run)')
    args = ap.parse_args()
    plan = build_plan(args.request)
    out = execute(plan, apply=args.apply)
    print(json.dumps(out, indent=2))


if __name__ == '__main__':
    main()
