# Cloudflare R2 Object Listing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only, dependency-free Python utility that verifies a Cloudflare API token and prints every object key in every R2 bucket returned for an account.

**Architecture:** A single `cloudflare_r2_list.py` module owns the small Cloudflare REST client and command-line entry point. HTTP access is injected so `unittest` can exercise authentication, pagination, output, and failures without a live account.

**Tech Stack:** Python standard library (`json`, `os`, `sys`, `urllib`), `unittest`

## Global Constraints

- Read `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` from the process environment.
- Use only authenticated `GET` requests to Cloudflare API v4.
- Use only Python's standard library; add no package dependency.
- Never print, persist, or include the API token in an error.
- Print object keys only; do not download or display object contents.
- Follow every bucket and object cursor until pagination is exhausted.
- Do not upload, modify, or delete R2 objects.
- Follow the repository's existing `unittest` style.

---

### Task 1: Token verification and safe API envelope

**Files:**
- Create: `cloudflare_r2_list.py`
- Create: `tests/test_cloudflare_r2_list.py`

**Interfaces:**
- Produces: `CloudflareAPIError`
- Produces: `api_get(path: str, token: str, *, params: Mapping[str, str] | None = None, jurisdiction: str | None = None, opener: Callable[..., Any] = request.urlopen) -> dict[str, Any]`
- Produces: `verify_token(token: str, *, opener: Callable[..., Any] = request.urlopen) -> None`

- [ ] **Step 1: Write the failing token-verification tests**

Create `tests/test_cloudflare_r2_list.py` with a reusable in-memory response and
recording opener:

```python
from __future__ import annotations

import io
import json
import unittest
from urllib.error import HTTPError

from cloudflare_r2_list import CloudflareAPIError, verify_token


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.body = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


class RecordingOpener:
    def __init__(self, *outcomes: object) -> None:
        self.outcomes = list(outcomes)
        self.requests = []

    def __call__(self, request, timeout: int = 30):
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return FakeResponse(outcome)


class CloudflareConnectionTests(unittest.TestCase):
    def test_verify_token_uses_bearer_auth_and_accepts_active_token(self) -> None:
        opener = RecordingOpener(
            {"success": True, "result": {"status": "active"}}
        )

        verify_token("secret-token", opener=opener)

        self.assertEqual(
            opener.requests[0].get_header("Authorization"),
            "Bearer secret-token",
        )
        self.assertTrue(
            opener.requests[0].full_url.endswith("/user/tokens/verify")
        )

    def test_verify_token_rejects_inactive_token(self) -> None:
        opener = RecordingOpener(
            {"success": True, "result": {"status": "disabled"}}
        )

        with self.assertRaisesRegex(CloudflareAPIError, "disabled"):
            verify_token("secret-token", opener=opener)

    def test_http_failure_redacts_token(self) -> None:
        token = "secret-token"
        body = io.BytesIO(
            json.dumps(
                {
                    "success": False,
                    "errors": [{"message": f"rejected {token}"}],
                }
            ).encode("utf-8")
        )
        failure = HTTPError(
            "https://api.cloudflare.com/client/v4/user/tokens/verify",
            403,
            "Forbidden",
            {},
            body,
        )
        opener = RecordingOpener(failure)

        with self.assertRaises(CloudflareAPIError) as caught:
            verify_token(token, opener=opener)

        self.assertNotIn(token, str(caught.exception))
        self.assertIn("[redacted]", str(caught.exception))
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_cloudflare_r2_list.py" -v
```

Expected: FAIL because `cloudflare_r2_list` does not exist.

- [ ] **Step 3: Implement the minimal safe API client**

Create `cloudflare_r2_list.py` with the following API envelope:

```python
from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any
from urllib import parse, request
from urllib.error import HTTPError, URLError

API_ROOT = "https://api.cloudflare.com/client/v4"


class CloudflareAPIError(RuntimeError):
    pass


def _redact(message: str, token: str) -> str:
    return message.replace(token, "[redacted]") if token else message


def _cloudflare_error(payload: object) -> str:
    if isinstance(payload, dict):
        errors = payload.get("errors")
        if isinstance(errors, list):
            messages = [
                item.get("message", "")
                for item in errors
                if isinstance(item, dict) and item.get("message")
            ]
            if messages:
                return "; ".join(messages)
    return "Cloudflare API request failed"


def api_get(
    path: str,
    token: str,
    *,
    params: Mapping[str, str] | None = None,
    jurisdiction: str | None = None,
    opener: Callable[..., Any] = request.urlopen,
) -> dict[str, Any]:
    query = parse.urlencode(params or {})
    url = f"{API_ROOT}{path}" + (f"?{query}" if query else "")
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
    }
    if jurisdiction:
        headers["cf-r2-jurisdiction"] = jurisdiction
    api_request = request.Request(url, headers=headers, method="GET")

    try:
        with opener(api_request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = {}
        raise CloudflareAPIError(
            _redact(_cloudflare_error(payload), token)
        ) from None
    except (URLError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CloudflareAPIError(_redact(str(exc), token)) from None

    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise CloudflareAPIError(
            _redact(_cloudflare_error(payload), token)
        )
    return payload


def verify_token(
    token: str,
    *,
    opener: Callable[..., Any] = request.urlopen,
) -> None:
    payload = api_get("/user/tokens/verify", token, opener=opener)
    result = payload.get("result")
    status = result.get("status") if isinstance(result, dict) else None
    if status != "active":
        raise CloudflareAPIError(
            f"Cloudflare API token is {status or 'invalid'}"
        )
```

- [ ] **Step 4: Run the focused test and verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_cloudflare_r2_list.py" -v
```

Expected: all three tests PASS with no network request.

- [ ] **Step 5: Commit Task 1**

```powershell
git add cloudflare_r2_list.py tests/test_cloudflare_r2_list.py
git commit -m "feat: verify Cloudflare API token"
```

### Task 2: Paginated R2 bucket and object discovery

**Files:**
- Modify: `cloudflare_r2_list.py`
- Modify: `tests/test_cloudflare_r2_list.py`

**Interfaces:**
- Consumes: `api_get(...) -> dict[str, Any]`
- Produces: `list_buckets(account_id: str, token: str, *, opener: Callable[..., Any] = request.urlopen) -> list[dict[str, Any]]`
- Produces: `list_object_keys(account_id: str, bucket_name: str, token: str, *, jurisdiction: str | None = None, opener: Callable[..., Any] = request.urlopen) -> list[str]`

- [ ] **Step 1: Write failing pagination tests**

Extend the test import:

```python
from cloudflare_r2_list import (
    CloudflareAPIError,
    list_buckets,
    list_object_keys,
    verify_token,
)
```

Add these methods to `CloudflareConnectionTests`:

```python
def test_list_buckets_follows_cursor_pagination(self) -> None:
    opener = RecordingOpener(
        {
            "success": True,
            "result": {
                "buckets": [
                    {"name": "first-bucket", "jurisdiction": "default"}
                ]
            },
            "result_info": {"cursor": "next page"},
        },
        {
            "success": True,
            "result": {
                "buckets": [
                    {"name": "second-bucket", "jurisdiction": "eu"}
                ]
            },
            "result_info": {},
        },
    )

    buckets = list_buckets("account-id", "token", opener=opener)

    self.assertEqual(
        [bucket["name"] for bucket in buckets],
        ["first-bucket", "second-bucket"],
    )
    self.assertIn("cursor=next+page", opener.requests[1].full_url)

def test_list_object_keys_preserves_paths_and_follows_cursor(self) -> None:
    opener = RecordingOpener(
        {
            "success": True,
            "result": [{"key": "folder/first.csv"}],
            "result_info": {"cursor": "more"},
        },
        {
            "success": True,
            "result": [{"key": "second.parquet"}],
            "result_info": {},
        },
    )

    keys = list_object_keys(
        "account-id",
        "bucket with spaces",
        "token",
        jurisdiction="eu",
        opener=opener,
    )

    self.assertEqual(keys, ["folder/first.csv", "second.parquet"])
    self.assertIn("bucket%20with%20spaces", opener.requests[0].full_url)
    self.assertEqual(
        opener.requests[0].get_header("Cf-r2-jurisdiction"),
        "eu",
    )
    self.assertIn("cursor=more", opener.requests[1].full_url)

def test_list_object_keys_rejects_an_invalid_response_shape(self) -> None:
    opener = RecordingOpener(
        {"success": True, "result": {"objects": []}, "result_info": {}}
    )

    with self.assertRaisesRegex(
        CloudflareAPIError,
        "Invalid R2 object-list response",
    ):
        list_object_keys(
            "account-id",
            "bucket",
            "token",
            opener=opener,
        )
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_cloudflare_r2_list.py" -v
```

Expected: FAIL because `list_buckets` and `list_object_keys` are undefined.

- [ ] **Step 3: Implement cursor pagination**

Append these functions to `cloudflare_r2_list.py`:

```python
def _cursor(payload: Mapping[str, Any]) -> str | None:
    result_info = payload.get("result_info")
    if not isinstance(result_info, dict):
        return None
    cursor = result_info.get("cursor")
    return cursor if isinstance(cursor, str) and cursor else None


def list_buckets(
    account_id: str,
    token: str,
    *,
    opener: Callable[..., Any] = request.urlopen,
) -> list[dict[str, Any]]:
    buckets: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        params = {"per_page": "1000"}
        if cursor:
            params["cursor"] = cursor
        payload = api_get(
            f"/accounts/{parse.quote(account_id, safe='')}/r2/buckets",
            token,
            params=params,
            opener=opener,
        )
        result = payload.get("result")
        page = result.get("buckets") if isinstance(result, dict) else None
        if not isinstance(page, list) or not all(
            isinstance(item, dict) for item in page
        ):
            raise CloudflareAPIError("Invalid R2 bucket-list response")
        buckets.extend(page)
        cursor = _cursor(payload)
        if not cursor:
            return buckets


def list_object_keys(
    account_id: str,
    bucket_name: str,
    token: str,
    *,
    jurisdiction: str | None = None,
    opener: Callable[..., Any] = request.urlopen,
) -> list[str]:
    keys: list[str] = []
    cursor: str | None = None
    path = (
        f"/accounts/{parse.quote(account_id, safe='')}/r2/buckets/"
        f"{parse.quote(bucket_name, safe='')}/objects"
    )
    while True:
        params = {"per_page": "1000"}
        if cursor:
            params["cursor"] = cursor
        payload = api_get(
            path,
            token,
            params=params,
            jurisdiction=jurisdiction,
            opener=opener,
        )
        page = payload.get("result")
        if not isinstance(page, list):
            raise CloudflareAPIError("Invalid R2 object-list response")
        for item in page:
            key = item.get("key") if isinstance(item, dict) else None
            if not isinstance(key, str):
                raise CloudflareAPIError("Invalid R2 object-list response")
            keys.append(key)
        cursor = _cursor(payload)
        if not cursor:
            return keys
```

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_cloudflare_r2_list.py" -v
```

Expected: all six tests PASS.

- [ ] **Step 5: Commit Task 2**

```powershell
git add cloudflare_r2_list.py tests/test_cloudflare_r2_list.py
git commit -m "feat: list Cloudflare R2 object keys"
```

### Task 3: Environment-driven command-line output

**Files:**
- Modify: `cloudflare_r2_list.py`
- Modify: `tests/test_cloudflare_r2_list.py`

**Interfaces:**
- Consumes: `verify_token`, `list_buckets`, and `list_object_keys`
- Produces: `load_config(environ: Mapping[str, str]) -> tuple[str, str]`
- Produces: `main(*, environ: Mapping[str, str] | None = None, stdout: TextIO = sys.stdout, stderr: TextIO = sys.stderr, opener: Callable[..., Any] = request.urlopen) -> int`

- [ ] **Step 1: Write failing CLI tests**

Add imports to the test module:

```python
from unittest.mock import ANY, patch

from cloudflare_r2_list import (
    CloudflareAPIError,
    list_buckets,
    list_object_keys,
    main,
    verify_token,
)
```

Add a new test class:

```python
class CloudflareCLITests(unittest.TestCase):
    def test_missing_environment_configuration_is_reported(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()

        exit_code = main(environ={}, stdout=stdout, stderr=stderr)

        self.assertEqual(exit_code, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("CLOUDFLARE_API_TOKEN", stderr.getvalue())
        self.assertIn("CLOUDFLARE_ACCOUNT_ID", stderr.getvalue())

    def test_main_prints_object_keys_and_marks_empty_buckets(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        environ = {
            "CLOUDFLARE_API_TOKEN": "token",
            "CLOUDFLARE_ACCOUNT_ID": "account-id",
        }
        buckets = [
            {"name": "data", "jurisdiction": "default"},
            {"name": "empty", "jurisdiction": "eu"},
        ]

        with (
            patch("cloudflare_r2_list.verify_token") as verify,
            patch(
                "cloudflare_r2_list.list_buckets",
                return_value=buckets,
            ),
            patch(
                "cloudflare_r2_list.list_object_keys",
                side_effect=[["node/a.csv", "node/b.csv"], []],
            ) as list_keys,
        ):
            exit_code = main(
                environ=environ,
                stdout=stdout,
                stderr=stderr,
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            stdout.getvalue().splitlines(),
            ["[data]", "node/a.csv", "node/b.csv", "[empty]", "(empty)"],
        )
        self.assertEqual(stderr.getvalue(), "")
        verify.assert_called_once_with("token", opener=ANY)
        self.assertEqual(list_keys.call_args_list[1].kwargs["jurisdiction"], "eu")

    def test_main_reports_api_error_without_traceback(self) -> None:
        stderr = io.StringIO()
        environ = {
            "CLOUDFLARE_API_TOKEN": "token",
            "CLOUDFLARE_ACCOUNT_ID": "account-id",
        }

        with patch(
            "cloudflare_r2_list.verify_token",
            side_effect=CloudflareAPIError("permission denied"),
        ):
            exit_code = main(
                environ=environ,
                stdout=io.StringIO(),
                stderr=stderr,
            )

        self.assertEqual(exit_code, 1)
        self.assertEqual(stderr.getvalue(), "Error: permission denied\n")
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_cloudflare_r2_list.py" -v
```

Expected: FAIL because `main` does not exist.

- [ ] **Step 3: Implement configuration, output, and entry point**

Add `os`, `sys`, and `TextIO` imports and the CLI implementation to
`cloudflare_r2_list.py`:

```python
import os
import sys
from typing import Any, TextIO


def load_config(environ: Mapping[str, str]) -> tuple[str, str]:
    token = environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    account_id = environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    missing = [
        name
        for name, value in (
            ("CLOUDFLARE_API_TOKEN", token),
            ("CLOUDFLARE_ACCOUNT_ID", account_id),
        )
        if not value
    ]
    if missing:
        raise CloudflareAPIError(
            "Missing environment variable(s): " + ", ".join(missing)
        )
    return token, account_id


def main(
    *,
    environ: Mapping[str, str] | None = None,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
    opener: Callable[..., Any] = request.urlopen,
) -> int:
    try:
        token, account_id = load_config(os.environ if environ is None else environ)
        verify_token(token, opener=opener)
        buckets = list_buckets(account_id, token, opener=opener)
        if not buckets:
            print("No R2 buckets found.", file=stdout)
            return 0

        for bucket in buckets:
            name = bucket.get("name")
            if not isinstance(name, str) or not name:
                raise CloudflareAPIError("Invalid R2 bucket-list response")
            jurisdiction = bucket.get("jurisdiction")
            if not isinstance(jurisdiction, str):
                jurisdiction = None
            print(f"[{name}]", file=stdout)
            keys = list_object_keys(
                account_id,
                name,
                token,
                jurisdiction=jurisdiction,
                opener=opener,
            )
            if keys:
                for key in keys:
                    print(key, file=stdout)
            else:
                print("(empty)", file=stdout)
        return 0
    except CloudflareAPIError as exc:
        print(f"Error: {exc}", file=stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
```

Add this module docstring above the future import:

```python
"""Verify a Cloudflare token and list R2 object names.

PowerShell setup:
    $env:CLOUDFLARE_API_TOKEN = "<token>"
    $env:CLOUDFLARE_ACCOUNT_ID = "<account-id>"
    python cloudflare_r2_list.py
"""
```

- [ ] **Step 4: Run focused and repository tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_cloudflare_r2_list.py" -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m py_compile cloudflare_r2_list.py tests/test_cloudflare_r2_list.py
```

Expected: all new tests PASS, all existing tests PASS, and compilation exits
successfully without warnings.

- [ ] **Step 5: Review the final diff for read-only behavior and token safety**

Run:

```powershell
git diff --check
git diff -- cloudflare_r2_list.py tests/test_cloudflare_r2_list.py
rg -n "POST|PUT|PATCH|DELETE|print\\(.*token|CLOUDFLARE_API_TOKEN\\s*=" cloudflare_r2_list.py tests/test_cloudflare_r2_list.py
```

Expected: no whitespace errors; the diff contains only the connection/listing
utility and tests; the scan finds no mutating HTTP method, token print, or
hard-coded token assignment.

- [ ] **Step 6: Commit Task 3**

```powershell
git add cloudflare_r2_list.py tests/test_cloudflare_r2_list.py
git commit -m "feat: add Cloudflare R2 listing command"
```

## Final verification

Run:

```powershell
git status --short --branch
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m py_compile cloudflare_r2_list.py tests/test_cloudflare_r2_list.py
```

Expected: the working tree is clean, the branch is ahead only by the intentional
design/plan/implementation commits, all tests pass, and both new Python files
compile.
