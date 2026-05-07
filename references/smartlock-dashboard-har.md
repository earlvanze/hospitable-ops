# Smart-lock dashboard routes from HAR

Source HAR: `~/Downloads/har/devices_my.hospitable.com.har` (user referred to `devices_hospitable.com.har`; local file had `my` in the name).

These are **Hospitable web/app routes**, not official Hospitable MCP tools. They require a logged-in app/device `HOSPITABLE_BEARER`; the long-lived PAT API key and MCP OAuth token are not accepted on these routes.

## Read routes

- `GET /smartlocks/connections` -> connected smart-lock provider accounts.
- `GET /v1/settings/smartlocks` -> lock-code automation settings and notification settings.
- `POST /v1/smartlocks/devices/smartlocks?offset=&query=&limit=` with body `{"filters":[]}` -> smart-lock devices.
- `POST /v1/smartlocks/devices/thermostats?offset=&query=&limit=` with body `{"filters":[]}` -> thermostats.
- `POST /v1/smartlocks/codes/reservation?offset=&query=&limit=` with body `{"filters":[]}` -> reservation-linked lock codes.
- `POST /v1/smartlocks/codes/manual?offset=&query=&limit=` with body `{"filters":[]}` -> manually-created codes.
- `GET /v1/smartlocks/notifications?cursor=` -> smart-lock notifications.
- `GET /smartlocks/filters/devices` -> filter definitions for devices.
- `GET /smartlocks/filters/codes` -> filter definitions for codes.

## Write routes

- `PUT /v1/smartlocks/notifications/{notification_id}` with body `{}` -> mark a smart-lock notification read.

Treat this as an external write. Use dry-run first unless the user explicitly asks to mark notifications read.

## Helper script

Use:

```bash
cd /home/umbrel/.openclaw/workspace/skills/hospitable-ops
python3 scripts/hospitable_smartlock_dashboard.py devices --summary
python3 scripts/hospitable_smartlock_dashboard.py reservation-codes --summary --max-pages 3
python3 scripts/hospitable_smartlock_dashboard.py notifications --summary --max-pages 2
python3 scripts/hospitable_smartlock_dashboard.py settings
python3 scripts/hospitable_smartlock_dashboard.py mark-notification-read <notification_id>       # dry-run
python3 scripts/hospitable_smartlock_dashboard.py mark-notification-read <notification_id> --execute
```

Default output redacts secret-looking keys (`code`, `pin`, `backup`, `token`, etc.). Use `--show-secrets` only when the user explicitly needs the actual code/PIN value and the channel is safe.
