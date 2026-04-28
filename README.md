# Hospitable Ops

API-first operations toolkit for Hospitable reservations, calendar availability, smart locks, AI guest replies, and live metrics snapshots.

This repo is designed for automation agents and operators who need to manage Hospitable workflows without browser automation.

## What this repo does

- Manage reservation/calendar operations
  - block dates
  - unblock dates
  - cancel reservations
  - update manual bookings
  - find reservations by code
- Operate smart locks
  - retrieve guest backup codes
  - lock/unlock doors by fuzzy property or door name
  - check lock health, connectivity, and battery
- Pull live Hospitable metrics
  - properties
  - reservations
  - payouts
  - transactions
- Trigger AI-assisted guest replies
- Use Hospitable’s MCP server through `mcporter`

## Repository layout

```text
SKILL.md                          Agent/operator runbook
references/                       Endpoint notes and implementation references
scripts/hospitable_ops.py          Reservation and calendar operations
scripts/hospitable_mcp_client.py   Hospitable MCP wrapper
scripts/pull_hospitable_metrics.py Metrics snapshot puller
scripts/check_smartlock_health.py  Smart-lock health checker
scripts/get_backup_code.py         Guest lock-code lookup
scripts/unlock_door.py             Lock/unlock helper
scripts/trigger_reply.py           Guest reply helper
```

## Authentication

Most workflows require one or more Hospitable credentials in the environment:

- `HOSPITABLE_COOKIE`
- `HOSPITABLE_BEARER`
- `HOSPITABLE_API_KEY`

Do **not** commit credentials. Load them from your local secret store or env file.

For the original OpenClaw workspace, the expected local env file is:

```bash
/home/umbrel/.openclaw/workspace/secure/evolve-hospitable-sync.env
```

Because some values contain special characters, load it with exported env propagation:

```bash
set -a
source /home/umbrel/.openclaw/workspace/secure/evolve-hospitable-sync.env
set +a
```

## Common commands

### Block dates

```bash
python3 scripts/hospitable_ops.py block-dates \
  --property-id 1976512 \
  --start 2026-09-15 \
  --end 2026-09-16
```

### Unblock dates

```bash
python3 scripts/hospitable_ops.py unblock-dates \
  --property-id 1976512 \
  --start 2026-09-15 \
  --end 2026-09-16
```

### Find a reservation by code

```bash
python3 scripts/hospitable_ops.py find-by-code \
  --property-id 1976512 \
  --code X65129639
```

### Check smart-lock health

```bash
python3 scripts/check_smartlock_health.py
```

Send maintenance alert when needed:

```bash
python3 scripts/check_smartlock_health.py --send-whatsapp
```

### Unlock a door

Dry-run / preview:

```bash
python3 scripts/unlock_door.py unlock "27 front"
```

Execute:

```bash
python3 scripts/unlock_door.py unlock "27 front" --execute
```

### Pull live metrics snapshots

```bash
python3 scripts/pull_hospitable_metrics.py
python3 scripts/pull_hospitable_metrics.py --days 14 --rate-delay-ms 400
```

## Hospitable MCP

Hospitable MCP endpoint:

```text
https://mcp.hospitable.com/mcp
```

Authenticate once:

```bash
mcporter auth hospitable
```

List tools:

```bash
mcporter list hospitable --schema
```

Use the bundled wrapper:

```bash
python3 scripts/hospitable_mcp_client.py list
python3 scripts/hospitable_mcp_client.py test
python3 scripts/hospitable_mcp_client.py call --tool get_reservation --args '{"reservation_id":"ABC123"}'
```

## Safety rules

- Resolve property, reservation, guest, or lock identity before writes.
- Use dry-run/preview mode before destructive or irreversible operations.
- For smart-lock actions, confirm the matched device name.
- Preserve raw endpoint payloads for metrics/audit workflows.
- Preview guest messages before sending unless explicitly instructed otherwise.

## References

- `SKILL.md`
- `references/ai-reply-api.md`
- `references/booking-edit-surface.md`
- `references/metrics-endpoints.md`
