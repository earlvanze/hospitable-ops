# Hospitable Public API endpoints (metrics-focused)

Base URL: `https://public.api.hospitable.com/v2`

Metrics-relevant endpoints currently working with PAT auth:

- `GET /properties`
- `GET /payouts`
- `GET /transactions`
- `GET /reservations` (**requires `properties[]` query array**)

Notes:
- Authentication uses a Personal Access Token from Hospitable.
- In this workspace, token source is `HOSPITABLE_API_KEY` from environment.
- For reservations, requests without `properties[]` return 400 (`The properties field is required`).
- API schema/pagination details can vary by account/version. The live script stores raw responses for traceability.
