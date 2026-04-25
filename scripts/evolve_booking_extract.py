#!/usr/bin/env python3
import argparse, json, os, sys
from typing import Any, Dict, Optional
try:
    import requests
except Exception:
    print('Missing dependency: requests', file=sys.stderr)
    sys.exit(2)

GRAPH='https://graph.prod.evolve.com/'
QUERY='''query Booking($id: ID, $internalId: ID, $excludeDamageTypes: Boolean, $excludeFailedAndCancelled: Boolean, $excludeGuestFees: Boolean, $excludeTypes: [BookingFinancialActivityType!], $goesTo: BookingFinancialActivityGoesTo) {\n  booking(id: $id, internalId: $internalId) {\n    __typename\n    id\n    internalId\n    code\n    displayStatus\n    status\n    channel\n    source\n    checkIn\n    checkOut\n    nights\n    numGuests\n    adults\n    children\n    infants\n    pets\n    guestName\n    guestEmail\n    guestPhone\n    listing { id internalId propertyName __typename }\n    financialActivities(excludeDamageTypes: $excludeDamageTypes, excludeFailedAndCancelled: $excludeFailedAndCancelled, excludeGuestFees: $excludeGuestFees, excludeTypes: $excludeTypes, goesTo: $goesTo) {\n      nodes {\n        id\n        type\n        goesTo\n        status\n        amount { amount currency __typename }\n        description\n        createdAt\n        __typename\n      }\n      __typename\n    }\n  }\n}'''

def session():
    s=requests.Session()
    s.headers.update({
        'content-type':'application/json',
        'accept':'application/json',
        'origin':'https://owner.evolve.com',
        'referer':'https://owner.evolve.com/',
        'user-agent':'Mozilla/5.0 OpenClaw Evolve extractor',
    })
    cookie=os.environ.get('EVOLVE_COOKIE')
    auth=os.environ.get('EVOLVE_AUTHORIZATION')
    if cookie: s.headers['cookie']=cookie
    if auth: s.headers['authorization']=auth
    if not cookie and not auth:
        raise SystemExit('Set EVOLVE_COOKIE or EVOLVE_AUTHORIZATION')
    return s

def fetch_booking(internal_id:str)->Dict[str,Any]:
    vars={
        'id':'',
        'internalId':internal_id,
        'excludeDamageTypes':True,
        'excludeFailedAndCancelled':True,
        'excludeGuestFees':True,
        'excludeTypes':['INSURANCE','REFUND','FUTURE_BOOKING_CREDIT'],
        'goesTo':'OWNER',
    }
    payload={'operationName':'Booking','variables':vars,'query':QUERY,'extensions':{'clientLibrary':{'name':'@apollo/client','version':'4.0.11'}}}
    r=session().post(GRAPH,json=payload,timeout=30)
    if not r.ok:
        raise SystemExit(f'HTTP {r.status_code}: {r.text[:1200]}')
    data=r.json()
    if data.get('errors'):
        raise SystemExit(json.dumps(data['errors'], indent=2))
    return data['data']['booking']

def normalize_to_hospitable(b:Dict[str,Any], property_id_map:Optional[Dict[str,int]]=None)->Dict[str,Any]:
    listing=b.get('listing') or {}
    prop_name=listing.get('propertyName','')
    property_id=(property_id_map or {}).get(prop_name)
    guest=(b.get('guestName') or '').strip()
    parts=guest.split()
    first=parts[0] if parts else ''
    last=' '.join(parts[1:]) if len(parts)>1 else ''
    channel=(b.get('channel') or 'manual').lower()
    if channel == 'vrbo':
        hosp_channel='vrbo'
    elif channel in ('airbnb','booking.com','evolve'):
        hosp_channel='evolve' if channel=='evolve' else channel
    else:
        hosp_channel=channel or 'manual'
    return {
        'property_name': prop_name,
        'property_id': property_id,
        'reservation_code': b.get('code') or b.get('internalId'),
        'channel': hosp_channel,
        'check_in': b.get('checkIn'),
        'check_out': b.get('checkOut'),
        'adults': b.get('adults') or 0,
        'children': b.get('children') or 0,
        'infants': b.get('infants') or 0,
        'pets': b.get('pets') or 0,
        'guest_first_name': first,
        'guest_last_name': last,
        'guest_email': b.get('guestEmail') or '',
        'guest_phone': b.get('guestPhone') or '',
        'num_guests': b.get('numGuests') or 2,
        'source_channel': b.get('channel'),
        'display_status': b.get('displayStatus'),
        'note': b.get('channel') or '',
        'financial_activities': b.get('financialActivities',{}).get('nodes',[]),
    }

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('internal_id')
    ap.add_argument('--map-json')
    ap.add_argument('--normalize', action='store_true')
    args=ap.parse_args()
    b=fetch_booking(args.internal_id)
    if not args.normalize:
        print(json.dumps(b, indent=2))
        return
    mp={}
    if args.map_json:
        mp=json.load(open(args.map_json))
    print(json.dumps(normalize_to_hospitable(b, mp), indent=2))

if __name__=='__main__':
    main()
