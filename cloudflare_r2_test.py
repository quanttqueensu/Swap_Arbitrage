"""Test a Cloudflare token and list every R2 object name.

PowerShell:
    $env:CLOUDFLARE_API_TOKEN = "your-token"
    $env:CLOUDFLARE_ACCOUNT_ID = "your-account-id"
    python cloudflare_r2_test.py
"""

import json
import os
import sys
from urllib import parse, request
from urllib.error import HTTPError, URLError


API_ROOT = "https://api.cloudflare.com/client/v4"
TOKEN = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
ACCOUNT_ID = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()


def get(path, params=None, jurisdiction=None):
    query = parse.urlencode(params or {})
    url = f"{API_ROOT}{path}" + (f"?{query}" if query else "")
    headers = {"Authorization": f"Bearer {TOKEN}", "Accept": "application/json"}
    if jurisdiction:
        headers["cf-r2-jurisdiction"] = jurisdiction

    try:
        with request.urlopen(
            request.Request(url, headers=headers, method="GET"),
            timeout=30,
        ) as response:
            payload = json.load(response)
    except HTTPError as error:
        try:
            payload = json.load(error)
            messages = [
                item.get("message", "")
                for item in payload.get("errors", [])
                if isinstance(item, dict)
            ]
            detail = "; ".join(filter(None, messages))
        except (UnicodeDecodeError, json.JSONDecodeError):
            detail = ""
        raise RuntimeError(detail or f"Cloudflare returned HTTP {error.code}") from None
    except (URLError, TimeoutError) as error:
        raise RuntimeError(f"Connection failed: {error}") from None

    if payload.get("success") is not True:
        messages = [
            item.get("message", "")
            for item in payload.get("errors", [])
            if isinstance(item, dict)
        ]
        raise RuntimeError("; ".join(filter(None, messages)) or "Cloudflare API failed")
    return payload


def list_buckets():
    buckets = []
    cursor = None
    while True:
        params = {"per_page": "1000"}
        if cursor:
            params["cursor"] = cursor
        payload = get(f"/accounts/{parse.quote(ACCOUNT_ID)}/r2/buckets", params)
        buckets.extend(payload.get("result", {}).get("buckets", []))
        cursor = payload.get("result_info", {}).get("cursor")
        if not cursor:
            return buckets


def list_objects(bucket):
    keys = []
    cursor = None
    bucket_name = bucket["name"]
    while True:
        params = {"per_page": "1000"}
        if cursor:
            params["cursor"] = cursor
        payload = get(
            f"/accounts/{parse.quote(ACCOUNT_ID)}/r2/buckets/"
            f"{parse.quote(bucket_name)}/objects",
            params,
            bucket.get("jurisdiction"),
        )
        keys.extend(item["key"] for item in payload.get("result", []))
        cursor = payload.get("result_info", {}).get("cursor")
        if not cursor:
            return keys


def main():
    missing = [
        name
        for name, value in (
            ("CLOUDFLARE_API_TOKEN", TOKEN),
            ("CLOUDFLARE_ACCOUNT_ID", ACCOUNT_ID),
        )
        if not value
    ]
    if missing:
        raise RuntimeError("Missing environment variable(s): " + ", ".join(missing))

    status = get("/user/tokens/verify").get("result", {}).get("status")
    if status != "active":
        raise RuntimeError(f"Token status is {status or 'unknown'}")

    print("Connection successful.")
    buckets = list_buckets()
    if not buckets:
        print("No R2 buckets found.")
        return

    for bucket in buckets:
        print(f"\n[{bucket['name']}]")
        keys = list_objects(bucket)
        if keys:
            print(*keys, sep="\n")
        else:
            print("(empty)")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, KeyError, TypeError) as error:
        message = str(error)
        if TOKEN:
            message = message.replace(TOKEN, "[redacted]")
        print(f"Error: {message}", file=sys.stderr)
        raise SystemExit(1)
