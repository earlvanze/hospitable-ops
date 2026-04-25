#!/usr/bin/env python3
import json
import re
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_ALIASES_PATH = Path('/home/umbrel/.openclaw/workspace/ops/hospitable_property_aliases.json')
STOPWORDS = {
    'the', 'at', 'on', 'in', 'of', 'and', 'a', 'an',
    'apt', 'apartment', 'unit', 'suite', 'ste', 'room',
    'building', 'bldg', 'house', 'home', 'listing', 'property'
}
STREET_EQUIV = {
    'avenue': 'ave',
    'av': 'ave',
    'street': 'st',
    'road': 'rd',
    'lane': 'ln',
    'drive': 'dr',
    'floor': 'fl',
    'flr': 'fl',
    'bedroom': 'bd',
    'bed': 'bd',
    'bathroom': 'ba',
    'bath': 'ba',
}


def load_properties(path: Path = DEFAULT_ALIASES_PATH) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    return data.get('properties', [])


def normalize(text: str) -> str:
    s = (text or '').lower().strip()
    s = s.replace('&', ' and ')
    s = re.sub(r'\b(\d+)\s*bed(room)?s?\b', r'\1 bd', s)
    s = re.sub(r'\b(\d+)\s*ba(th(room)?)?s?\b', r'\1 ba', s)
    s = re.sub(r'\b(\d+)\s*/\s*(\d+)\b', r'\1 \2', s)
    s = re.sub(r'[^a-z0-9.]+', ' ', s)
    parts = [STREET_EQUIV.get(token, token) for token in s.split()]
    return re.sub(r'\s+', ' ', ' '.join(parts)).strip()


def tokenize(text: str) -> List[str]:
    return [tok for tok in normalize(text).split() if tok not in STOPWORDS]


def extract_features(text: str) -> Dict[str, Any]:
    n = normalize(text)
    tokens = tokenize(text)
    raw = f' {n} '
    building = None
    unit = None
    beds = None
    baths = None
    floor = None

    m = re.search(r'\b(\d{2,4})\.(\d+)\b', n)
    if m:
        building = m.group(1)
        unit = m.group(2)

    if building is None:
        m = re.search(r'\b(\d{2,4})\s+(?:unit|apt|apartment|suite|ste|#)?\s*(\d+)\b', raw)
        if m:
            building = m.group(1)
            unit = m.group(2)

    if building is None or unit is None:
        m = re.search(r'\b(\d{2,4})\s+[a-z]+(?:\s+[a-z]+)?\s+(\d+)\b', n)
        if m:
            building = building or m.group(1)
            unit = unit or m.group(2)

    if unit is None:
        m = re.search(r'\b(?:unit|apt|apartment|suite|ste|#)\s*(\d+)\b', raw)
        if m:
            unit = m.group(1)

    if building is None:
        m = re.search(r'\b(\d{2,4})\b', n)
        if m:
            building = m.group(1)

    m = re.search(r'\b(\d+)\s*bd\b', n)
    if m:
        beds = m.group(1)
    m = re.search(r'\b(\d+)\s*ba\b', n)
    if m:
        baths = m.group(1)
    m = re.search(r'\bfl\s*(\d+)\b', n)
    if m:
        floor = m.group(1)

    return {
        'normalized': n,
        'compact': n.replace(' ', ''),
        'tokens': tokens,
        'token_counts': Counter(tokens),
        'building': building,
        'unit': unit,
        'beds': beds,
        'baths': baths,
        'floor': floor,
    }


def jaccard(a: List[str], b: List[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def score_features(query: Dict[str, Any], cand: Dict[str, Any]) -> Tuple[float, List[str]]:
    score = 0.0
    reasons: List[str] = []

    if query['normalized'] == cand['normalized']:
        score += 140
        reasons.append('exact_normalized')
    elif query['compact'] == cand['compact']:
        score += 120
        reasons.append('exact_compact')

    seq = SequenceMatcher(None, query['normalized'], cand['normalized']).ratio()
    if seq:
        score += seq * 35
        if seq >= 0.9:
            reasons.append('high_string_similarity')

    jac = jaccard(query['tokens'], cand['tokens'])
    score += jac * 35
    if jac >= 0.6:
        reasons.append('high_token_overlap')

    if query['building'] and cand['building']:
        if query['building'] == cand['building']:
            score += 35
            reasons.append('same_building')
        else:
            score -= 45
            reasons.append('building_mismatch')

    if query['unit'] and cand['unit']:
        if query['unit'] == cand['unit']:
            score += 55
            reasons.append('same_unit')
        else:
            score -= 55
            reasons.append('unit_mismatch')

    if query['beds'] and cand['beds']:
        if query['beds'] == cand['beds']:
            score += 18
            reasons.append('same_beds')
        else:
            score -= 12
            reasons.append('beds_mismatch')

    if query['baths'] and cand['baths']:
        if query['baths'] == cand['baths']:
            score += 12
            reasons.append('same_baths')
        else:
            score -= 8
            reasons.append('baths_mismatch')

    if query['floor'] and cand['floor']:
        if query['floor'] == cand['floor']:
            score += 14
            reasons.append('same_floor')
        else:
            score -= 10
            reasons.append('floor_mismatch')

    return round(score, 2), reasons


def expand_candidate_texts(prop: Dict[str, Any]) -> List[str]:
    vals = [prop.get('name', '')] + list(prop.get('aliases', []) or [])
    name_features = extract_features(prop.get('name', ''))
    building = name_features.get('building')
    unit = name_features.get('unit')
    if building and unit:
        vals.extend([f'{building}.{unit}', f'{building} {unit}', f'{building} unit {unit}'])
        for alias in list(prop.get('aliases', []) or []):
            alias_n = normalize(alias)
            if building in alias_n and unit not in alias_n:
                vals.extend([f'{alias} {unit}', f'unit {unit} {alias}'])

    out = []
    seen = set()
    for v in vals:
        if not v:
            continue
        nv = normalize(v)
        if nv and nv not in seen:
            seen.add(nv)
            out.append(v)
    return out


def rank_properties(query: str, properties: Optional[List[Dict[str, Any]]] = None, limit: int = 5) -> List[Dict[str, Any]]:
    props = properties if properties is not None else load_properties()
    qf = extract_features(query)
    results: List[Dict[str, Any]] = []
    for prop in props:
        best_score = float('-inf')
        best_text = ''
        best_reasons: List[str] = []
        for text in expand_candidate_texts(prop):
            cf = extract_features(text)
            score, reasons = score_features(qf, cf)
            if score > best_score:
                best_score = score
                best_text = text
                best_reasons = reasons
        results.append({
            'property_id': int(prop['property_id']),
            'name': prop.get('name', ''),
            'score': best_score,
            'matched_on': best_text,
            'reasons': best_reasons,
        })
    results.sort(key=lambda r: (-r['score'], r['name']))
    return results[:limit]


def match_property(query: str, properties: Optional[List[Dict[str, Any]]] = None, min_score: float = 110.0) -> Dict[str, Any]:
    q = (query or '').strip()
    if not q:
        return {'query': query, 'resolved': None, 'high_confidence': False, 'matches': []}

    if re.fullmatch(r'\d{5,}', q):
        pid = int(q)
        props = properties if properties is not None else load_properties()
        match = next((p for p in props if int(p['property_id']) == pid), None)
        return {
            'query': query,
            'resolved': pid,
            'high_confidence': True,
            'matches': ([{'property_id': pid, 'name': match.get('name', ''), 'score': 999.0, 'matched_on': 'numeric-id', 'reasons': ['numeric_id']}]
                        if match else [{'property_id': pid, 'name': '', 'score': 999.0, 'matched_on': 'numeric-id', 'reasons': ['numeric_id']}]),
        }

    matches = rank_properties(q, properties=properties)
    best = matches[0] if matches else None
    return {
        'query': query,
        'resolved': int(best['property_id']) if best and best['score'] >= min_score else None,
        'high_confidence': bool(best and best['score'] >= min_score),
        'matches': matches,
    }


def resolve_property(query: str, properties: Optional[List[Dict[str, Any]]] = None, min_score: float = 110.0) -> Tuple[Optional[int], Dict[str, Any]]:
    result = match_property(query, properties=properties, min_score=min_score)
    return result.get('resolved'), result
