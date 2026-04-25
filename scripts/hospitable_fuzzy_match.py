#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

MODULE_DIR = Path('/home/umbrel/.openclaw/workspace/skills/hospitable-ops/scripts')
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))

from hospitable_property_matcher import DEFAULT_ALIASES_PATH, load_properties, match_property  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description='Fuzzy-match Hospitable property/unit names.')
    ap.add_argument('query', help='Unit/property query, e.g. "88 Madison 3"')
    ap.add_argument('--aliases', default=str(DEFAULT_ALIASES_PATH))
    ap.add_argument('--limit', type=int, default=5)
    ap.add_argument('--min-score', type=float, default=110.0)
    args = ap.parse_args()

    props = load_properties(Path(args.aliases))
    result = match_property(args.query, properties=props, min_score=args.min_score)
    result['best_match'] = result['matches'][0] if result.get('matches') else None
    if args.limit:
        result['matches'] = (result.get('matches') or [])[:args.limit]
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
