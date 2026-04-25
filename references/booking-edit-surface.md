# Hospitable booking edit surface (discovery notes)

Last updated: 2026-03-31

## Confirmed manual booking route

- `PUT /v1/bookings/manual/{booking_uuid}`
- This remains the confirmed writable route for manual bookings.

## Non-manual reservation findings

### Live reservation feed signals

From `GET /v1/reservations/...`:
- Airbnb / HomeAway / Booking / most direct reservations generally surfaced with `supports.editing = false`
- Some non-manual iCal reservations (`platform/channel = ics.hostshare.co`) surfaced with `supports.editing = true`
- At least one direct booking surfaced with `editable = true` even while `supports.editing = false`

### iCal-imported reservations

For a non-manual iCal reservation with `supports.editing = true`:
- `GET /v1/bookings/manual/{uuid}` works
- `OPTIONS /v1/bookings/manual/{uuid}` advertises `GET,HEAD,PUT,DELETE`
- malformed `PUT /v1/bookings/manual/{uuid}` returns schema validation errors
- normalized same-value `PUT /v1/bookings/manual/{uuid}` returns permission error (not 404)

Interpretation:
- the manual booking route family is wired to some non-manual iCal reservations
- successful edit is not yet validated
- current blocker appears to be permission / exact accepted payload / business-rule enforcement

### Direct bookings

Confirmed route family:
- `GET /v1/bookings/direct/{uuid}` returns a full booking payload for at least some direct reservations
- `OPTIONS /v1/bookings/direct/{uuid}` advertises `GET,HEAD,PUT,DELETE`
- `OPTIONS /v1/bookings/direct/quote` advertises `GET,HEAD,POST,PUT,DELETE`
- `POST /v1/bookings/direct/quote` works and returns normalized fees / repricing output

Observed behavior:
- Same-value `PUT /v1/bookings/direct/{uuid}` using raw booking fee payload => `500 Server Error`
- Same-value `PUT /v1/bookings/direct/{uuid}` using quote-normalized fees => `500 Server Error`
- Same-value `PUT /v1/bookings/direct/{uuid}` using the full GET object shape plus quote-derived `fees` and `total` => `500 Server Error`
- `POST /v1/bookings/direct/quote` returns only computed `fees` + `total`; it does not expose an obvious quote token or update envelope
- Before/after GET hash unchanged in all same-value probes (no state mutation observed)

Interpretation:
- direct bookings have a real edit route family plus a real quote/reprice route
- accepted PUT payload shape is still not fully known, or the backend has an internal bug on this path

### Platform quote routes discovered

Safe probes confirmed working quote endpoints exist for:
- `POST /v1/bookings/airbnb/quote`
- `POST /v1/bookings/homeaway/quote`
- `POST /v1/bookings/vrbo/quote`
- `POST /v1/bookings/booking/quote`
- `POST /v1/bookings/ota/quote`
- `POST /v1/bookings/channel/quote`
- `POST /v1/bookings/direct/quote`

Confirmed with real payloads:
- Airbnb quote works with resolved `propertyId` + dates + guests + reservation code
- HomeAway quote works with resolved `propertyId` + dates + guests + reservation code
- Booking quote works with resolved `propertyId` + dates + guests + reservation code
- `ota/quote` and `channel/quote` also work for the same OTA booking payloads

Important caveat:
- existence of quote routes does **not** yet prove successful edit/update routes for those platforms
- matching per-booking route families were probed and returned `404` for OTA/channel reservations:
  - `/v1/bookings/airbnb/{uuid}`
  - `/v1/bookings/homeaway/{uuid}`
  - `/v1/bookings/vrbo/{uuid}`
  - `/v1/bookings/booking/{uuid}`
  - `/v1/bookings/ota/{uuid}`
  - `/v1/bookings/channel/{uuid}`
- no validated writable route has been confirmed for Airbnb / HomeAway / VRBO / Booking channel reservations

## Current practical conclusion

- Manual bookings: confirmed editable
- Non-manual iCal bookings: route family exists; successful write not yet validated
- Direct bookings: edit + quote route families exist; quote works; PUT still returns 500 with no confirmed accepted payload yet
- Airbnb / HomeAway / VRBO / Booking: quote routes exist; no validated booking update route yet

## Recommended next probe order

1. Compare browser/UI network traffic for a real direct-booking edit to capture exact PUT body
2. Test whether direct booking updates require a quote token / computed fields from `/v1/bookings/direct/quote`
3. Test whether platform quote endpoints accept payloads and reveal platform-specific validation schemas
4. Look for hidden `/v1/bookings/{platform}/{uuid}` or alternate non-idempotent update routes surfaced only from UI traffic
