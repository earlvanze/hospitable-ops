#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests

API_ROOT = "https://api.hospitable.com"
SECURE_ENV_PATH = Path("/home/umbrel/.openclaw/workspace/secure/evolve-hospitable-sync.env")


class HospitableSmartlocksError(RuntimeError):
    pass


class HospitableSmartlocksClient:
    def __init__(self, token: str | None = None, timeout: int = 20):
        self.token = token or resolve_hospitable_token()
        if not self.token:
            raise HospitableSmartlocksError(
                "No Hospitable bearer/API token found. Checked env and secure env file."
            )
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | list[Any] | None = None,
    ) -> Any:
        url = path if path.startswith("http") else f"{API_ROOT}{path}"
        resp = self.session.request(
            method=method.upper(),
            url=url,
            params=params,
            json=json_body,
            timeout=self.timeout,
        )
        if resp.status_code >= 400:
            raise HospitableSmartlocksError(
                f"{method.upper()} {resp.url} failed: {resp.status_code} {resp.text[:600]}"
            )
        if not resp.content:
            return None
        ctype = resp.headers.get("content-type", "")
        if "json" in ctype:
            return resp.json()
        return resp.text

    def list_devices(
        self,
        *,
        query: str = "",
        offset: int = 0,
        limit: int = 20,
        filters: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/v1/smartlocks/devices/smartlocks",
            params={"offset": offset, "query": query, "limit": limit},
            json_body={"filters": filters or []},
        )

    def list_codes(
        self,
        code_type: str,
        *,
        query: str = "",
        offset: int = 0,
        limit: int = 20,
        filters: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if code_type not in {"reservation", "manual"}:
            raise ValueError("code_type must be 'reservation' or 'manual'")
        return self._request(
            "POST",
            f"/v1/smartlocks/codes/{code_type}",
            params={"offset": offset, "query": query, "limit": limit},
            json_body={"filters": filters or []},
        )

    def list_properties(self) -> dict[str, Any]:
        return self._request("GET", "/v1/smartlocks/properties")

    def list_notifications(self) -> dict[str, Any]:
        return self._request("GET", "/v1/smartlocks/notifications")

    def create_manual_code(
        self,
        *,
        name: str,
        code: str,
        properties: list[int],
        starts_at: str,
        ends_at: str,
    ) -> dict[str, Any]:
        payload = {
            "name": name,
            "code": code,
            "properties": properties,
            "starts_at": starts_at,
            "ends_at": ends_at,
        }
        return self._request("POST", "/smartlocks/properties/code/manual", json_body=payload)

    def edit_code(
        self,
        *,
        property_id: int,
        reservation_id: str,
        name: str,
        code: str,
        properties: list[int],
        starts_at: str,
        ends_at: str,
    ) -> dict[str, Any]:
        payload = {
            "name": name,
            "code": code,
            "properties": properties,
            "starts_at": starts_at,
            "ends_at": ends_at,
        }
        return self._request(
            "PUT",
            f"/smartlocks/properties/{property_id}/code/{reservation_id}",
            json_body=payload,
        )


def resolve_hospitable_token() -> str:
    bearer = os.getenv("HOSPITABLE_BEARER", "").strip()
    if bearer:
        return bearer

    file_api_key = ""
    if SECURE_ENV_PATH.exists():
        for line in SECURE_ENV_PATH.read_text(encoding="utf-8").splitlines():
            if line.startswith("HOSPITABLE_BEARER="):
                return line.split("=", 1)[1].strip()
            if line.startswith("HOSPITABLE_API_KEY="):
                file_api_key = line.split("=", 1)[1].strip()

    env_api_key = os.getenv("HOSPITABLE_API_KEY", "").strip()
    return file_api_key or env_api_key


def pretty_print(data: Any) -> None:
    print(json.dumps(data, indent=2, sort_keys=False))


def payload_preview(method: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "execute": False,
        "method": method.upper(),
        "url": f"{API_ROOT}{path}",
        "payload": payload,
    }


def with_query(path: str, params: dict[str, Any]) -> str:
    filtered = {k: v for k, v in params.items() if v not in (None, "")}
    return f"{API_ROOT}{path}?{urlencode(filtered)}" if filtered else f"{API_ROOT}{path}"
