---
name: hospitable-ops
description: Operate Hospitable via API for reservations, calendar availability, smart locks, AI guest replies, and live metrics/snapshots. Use when asked to modify an existing reservation (guest fields, dates, fees, channel, notes), cancel reservations, block dates, unblock dates, reconcile overlap conflicts on Hospitable calendar, retrieve a guest smart-lock backup code, lock/unlock a specific door by fuzzy name, send an AI-assisted guest reply, or pull current Hospitable metrics and snapshots without browser automation.
---

# Hospitable Ops

Use API-first workflows for Hospitable reservations, calendar operations, smart locks, guest messaging, and metrics.

## Required auth

Depending on the task, load one or both of:
- `HOSPITABLE_COOKIE`
- `HOSPITABLE_BEARER`
- `HOSPITABLE_API_KEY` (required for Hospitable MCP)

Preferred local env source:
- `/home/umbrel/.openclaw/workspace/secure/evolve-hospitable-sync.env`

**Important — shell env propagation:** This env file contains special characters (JSON, base64, semicolons) that break `source` in plain bash. **Always** load it with:
```bash
bash -c 'set -a && source /home/umbrel/.openclaw/workspace/secure/evolve-hospitable-sync.env && set +a && python3 ...'
```
Or in a wrapper script:
```bash
set -a && source /home/umbrel/.openclaw/workspace/secure/evolve-hospitable-sync.env && set +a
python3 ...
```
Without `set -a`/`set +a`, env vars with `=`, `{`, `}`, or `%` in their values are silently dropped.

## Scripts

### Reservation + calendar
- `scripts/hospitable_ops.py` — reservations + calendar availability

Subcommands:
- `block-dates`
- `unblock-dates`
- `cancel-reservation`
- `update-manual-booking`
- `find-by-code`

### Smart locks
- `scripts/get_backup_code.py` — guest smart-lock backup code lookup
- `scripts/unlock_door.py` — lock/unlock smart locks by fuzzy door name
- `scripts/check_smartlock_health.py` — inspect lock connectivity and battery, optionally alert WhatsApp maintenance

### Smart-lock health checks

```bash
python3 scripts/check_smartlock_health.py
python3 scripts/check_smartlock_health.py --send-whatsapp
```

Behavior:
- scans all Hospitable smart-lock devices when a valid Hospitable app/device bearer is available
- flags disconnected locks
- flags locks with battery below 15%
- WhatsApp alert target is Repairs & Maintenance: `120363214019262017@g.us`
- current safe send path is the OpenClaw `message` tool, not shelling out to provider messaging
- if the endpoint returns 401, the script emits structured JSON with `ok:false` instead of a traceback

Scheduled run:
- 08:00 and 16:00 America/New_York via OpenClaw cron
- logs to `workspace/logs/smartlock_health_check.log`

### Guest AI reply
- `scripts/trigger_reply.py` — fetch reservation context and send an AI-style reply for a reservation/message workflow

### Live metrics and snapshots
- `scripts/pull_hospitable_metrics.py` — pull current properties, reservations, payouts, and transactions into auditable snapshot files

## Common commands

### Reservation + calendar

```bash
cd /home/umbrel/.openclaw/workspace/skills/hospitable-ops
bash -c 'set -a && source /home/umbrel/.openclaw/workspace/secure/evolve-hospitable-sync.env && set +a && python3 scripts/hospitable_ops.py block-dates --property-id 1976512 --start 2026-09-15 --end 2026-09-16'
cd /home/umbrel/.openclaw/workspace/skills/hospitable-ops
bash -c 'set -a && source /home/umbrel/.openclaw/workspace/secure/evolve-hospitable-sync.env && set +a && python3 scripts/hospitable_ops.py unblock-dates --property-id 1976512 --start 2026-09-15 --end 2026-09-16'
cd /home/umbrel/.openclaw/workspace/skills/hospitable-ops
bash -c 'set -a && source /home/umbrel/.openclaw/workspace/secure/evolve-hospitable-sync.env && set +a && python3 scripts/hospitable_ops.py find-by-code --property-id 1976512 --code X65129639'
cd /home/umbrel/.openclaw/workspace/skills/hospitable-ops
bash -c 'set -a && source /home/umbrel/.openclaw/workspace/secure/evolve-hospitable-sync.env && set +a && python3 scripts/hospitable_ops.py cancel-reservation --reservation-uuid <uuid>'
cd /home/umbrel/.openclaw/workspace/skills/hospitable-ops
bash -c 'set -a && source /home/umbrel/.openclaw/workspace/secure/evolve-hospitable-sync.env && set +a && python3 scripts/hospitable_ops.py update-manual-booking --booking-uuid <uuid> --json-file payload.json'
```

### Smart locks

```bash
cd /home/umbrel/.openclaw/workspace/skills/hospitable-ops
bash -c 'set -a && source /home/umbrel/.openclaw/workspace/secure/evolve-hospitable-sync.env && set +a && python3 scripts/get_backup_code.py "Annette Gustafson" "27 Front Door"'
cd /home/umbrel/.openclaw/workspace/skills/hospitable-ops
bash -c 'set -a && source /home/umbrel/.openclaw/workspace/secure/evolve-hospitable-sync.env && set +a && python3 scripts/unlock_door.py unlock "27 front"'
cd /home/umbrel/.openclaw/workspace/skills/hospitable-ops
bash -c 'set -a && source /home/umbrel/.openclaw/workspace/secure/evolve-hospitable-sync.env && set +a && python3 scripts/unlock_door.py unlock "27 front" --execute'
cd /home/umbrel/.openclaw/workspace/skills/hospitable-ops
bash -c 'set -a && source /home/umbrel/.openclaw/workspace/secure/evolve-hospitable-sync.env && set +a && python3 scripts/unlock_door.py lock "27 front" --execute'
```

Behavior:
- backup-code lookup filters by guest name and lock/property name
- door actions use fuzzy case-insensitive substring matching
- if multiple door matches are found, the script prompts for selection
- working smart-lock action routes are:
  - `POST /smartlocks/devices/{id}/lock`
  - `POST /smartlocks/devices/{id}/unlock`

### Smart-lock health checks

```bash
cd /home/umbrel/.openclaw/workspace/skills/hospitable-ops
python3 scripts/check_smartlock_health.py
python3 scripts/check_smartlock_health.py --send-whatsapp
```

Behavior:
- scans all Hospitable smart-lock devices when a valid Hospitable app/device bearer is available
- flags disconnected locks
- flags locks with battery below 15%
- WhatsApp alert target is Repairs & Maintenance: `120363214019262017@g.us`
- current safe send path is the OpenClaw `message` tool, not shelling out to provider messaging
- if the endpoint returns 401, the script emits structured JSON with `ok:false` instead of a traceback

Scheduled run:
- 08:00 and 16:00 America/New_York via OpenClaw cron
- logs to `workspace/logs/smartlock_health_check.log`

### Guest AI reply

```bash
cd /home/umbrel/.openclaw/workspace/skills/hospitable-ops
bash -c 'set -a && source /home/umbrel/.openclaw/workspace/secure/evolve-hospitable-sync.env && set +a && python3 scripts/trigger_reply.py <hospitable_api_key> <reservation_id>'
cd /home/umbrel/.openclaw/workspace/skills/hospitable-ops
bash -c 'set -a && source /home/umbrel/.openclaw/workspace/secure/evolve-hospitable-sync.env && set +a && python3 scripts/trigger_reply.py <hospitable_api_key> <reservation_id> --send'
```

Use for reservation-linked guest reply flows. Dry run is default; `--send` performs the write.

### Live metrics and snapshots

```bash
cd /home/umbrel/.openclaw/workspace/skills/hospitable-ops
python3 scripts/pull_hospitable_metrics.py
python3 scripts/pull_hospitable_metrics.py --days 14 --rate-delay-ms 400
```

Behavior:
- first run does a full backfill
- later runs do incremental sync based on saved state
- writes summary, raw endpoint payloads, latest merged snapshot files, and health log entries

## Safety rules

- Resolve property, reservation, guest, or lock identity first.
- For destructive actions, verify target identity before apply.
- Use dry-run or preview mode before the first write when uncertain.
- For smart-lock actions, report the matched device name back to the user.
- For metrics pulls, preserve raw endpoint payloads for auditability.
- For guest replies, preview first unless the user clearly wants the message sent.

## References

- `references/ai-reply-api.md`
- `references/metrics-endpoints.md`
- `references/booking-edit-surface.md`

## Payload note for update-manual-booking

Supply a full payload shape compatible with:
- `PUT /v1/bookings/manual/{booking_uuid}`

At minimum include fields that changed plus required booking identifiers expected by the endpoint.

## MCP Server Integration

Hospitable now provides an official MCP server at `https://mcp.hospitable.com/mcp`.

### Quick Start

```bash
# Authenticate once (stores credentials in mcporter config)
mcporter auth hospitable

# List available tools
bash -c 'set -a && source /home/umbrel/.openclaw/workspace/secure/evolve-hospitable-sync.env && set +a && mcporter --config /home/umbrel/.openclaw/workspace/config/mcporter.json list hospitable --schema'

# Call a tool
bash -c 'set -a && source /home/umbrel/.openclaw/workspace/secure/evolve-hospitable-sync.env && set +a && mcporter --config /home/umbrel/.openclaw/workspace/config/mcporter.json call hospitable.get-reservation uuid=ABC123'
```

### Python Client

Use the bundled MCP client for programmatic access:

```bash
# List tools
python3 scripts/hospitable_mcp_client.py list

# Call a tool
python3 scripts/hospitable_mcp_client.py call --tool get_reservation --args "{\"reservation_id\": \"ABC123\"}"

# Test connection
python3 scripts/hospitable_mcp_client.py test
```

### Available MCP Tools

After authentication, run `bash -c 'set -a && source /home/umbrel/.openclaw/workspace/secure/evolve-hospitable-sync.env && set +a && mcporter --config /home/umbrel/.openclaw/workspace/config/mcporter.json list hospitable --schema'` to see the full tool list.

Common tools:
- `get-reservation` — Fetch reservation details
- `get-reservations` — Search/list reservations
- `get-property-calendar` / `update-property-calendar` — Read/update calendar availability
- `send-reservation-message` — Send guest message
- `get-properties` — List all properties

### Auth Note

Hospitable MCP auth is configured in `/home/umbrel/.openclaw/workspace/config/mcporter.json` with OAuth (`auth: "oauth"`) and `mcp:use` scope. OAuth credentials are stored by mcporter in `~/.mcporter/credentials.json`. MCP auth is healthy as of 2026-05-07, but the official Hospitable MCP server currently exposes no smart-lock device health tools. Smart-lock web endpoints separately require a valid app/device session bearer; the long-lived API key and MCP OAuth access token are not accepted on those routes.

