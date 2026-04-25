#!/usr/bin/env python3
import argparse
import json
import sys

sys.path.insert(0, '/home/umbrel/.openclaw/workspace/skills/hospitable-ops/scripts')
from hospitable_ops import block_unblock  # noqa: E402
from hospitable_property_matcher import resolve_property  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description='Block or unblock Hospitable calendar dates via API.')
    ap.add_argument('property', help='Hospitable property id or fuzzy unit/property query')
    ap.add_argument('start', help='YYYY-MM-DD first night to modify')
    ap.add_argument('end', nargs='?', help='YYYY-MM-DD inclusive last night to modify')
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--block', action='store_true', help='Set available=false')
    g.add_argument('--unblock', action='store_true', help='Set available=true')
    ap.add_argument('--checkout-date', help='YYYY-MM-DD checkout date (exclusive upper bound)')
    ap.add_argument('--nights', type=int, help='Number of nights starting at start date')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--no-verify', action='store_true', help='Skip post-write verification')
    args = ap.parse_args()

    provided = [args.end is not None, args.checkout_date is not None, args.nights is not None]
    if sum(provided) != 1:
        raise SystemExit('Provide exactly one of: positional end, --checkout-date, --nights')

    available = True if args.unblock else False
    try:
        property_id = int(args.property)
        property_resolution = {'resolved': property_id, 'high_confidence': True, 'matches': [{'property_id': property_id, 'matched_on': 'property_id', 'score': 999.0, 'reasons': ['explicit_property_id']}]}
    except ValueError:
        property_id, property_resolution = resolve_property(args.property)
        if property_id is None:
            raise SystemExit(f'Could not confidently resolve property from query: {args.property!r}')

    out = block_unblock(
        property_id,
        args.start,
        end=args.end,
        checkout_date=args.checkout_date,
        nights=args.nights,
        available=available,
        dry_run=args.dry_run,
        verify=not args.no_verify,
    )
    out['property_resolution'] = property_resolution
    print(json.dumps(out, indent=2))


if __name__ == '__main__':
    main()
