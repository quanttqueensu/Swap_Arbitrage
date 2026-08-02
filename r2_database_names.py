from __future__ import annotations

import csv
import os
import sys
from pathlib import Path, PurePosixPath

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv


OUTPUT_FILE = Path(__file__).with_name("r2_objects.csv")

DATABASE_SUFFIXES = (
    ".sqlite3",
    ".sqlite",
    ".duckdb",
    ".accdb",
    ".backup",
    ".dump",
    ".db",
    ".mdb",
    ".sql",
    ".bak",
)

ARCHIVE_SUFFIXES = (
    ".tar.gz",
    ".tar.bz2",
    ".tar.xz",
    ".tar.zst",
    ".gz",
    ".bz2",
    ".xz",
    ".zst",
    ".zip",
    ".7z",
    ".tar",
)


def require_environment_variable(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"{name} is missing")
    return value


def database_name_from_key(key: str) -> str:
    filename = PurePosixPath(key).name

    while True:
        lowercase = filename.casefold()
        suffix = next(
            (item for item in ARCHIVE_SUFFIXES if lowercase.endswith(item)),
            None,
        )
        if suffix is None:
            break
        filename = filename[: -len(suffix)]

    lowercase = filename.casefold()
    suffix = next(
        (item for item in DATABASE_SUFFIXES if lowercase.endswith(item)),
        None,
    )
    if suffix is None:
        return ""

    return filename[: -len(suffix)]


def get_bucket_names(r2, configured_bucket: str) -> list[str]:
    if configured_bucket:
        return [configured_bucket]

    response = r2.list_buckets()
    return sorted(
        bucket["Name"]
        for bucket in response.get("Buckets", [])
        if bucket.get("Name")
    )


def export_objects(r2, buckets: list[str]) -> int:
    total = 0

    with OUTPUT_FILE.open("w", newline="", encoding="utf-8-sig") as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=(
                "bucket",
                "object_key",
                "filename",
                "folder",
                "database_name",
                "size_bytes",
                "last_modified",
                "etag",
                "storage_class",
            ),
        )
        writer.writeheader()

        paginator = r2.get_paginator("list_objects_v2")

        for bucket in buckets:
            bucket_total = 0

            for page in paginator.paginate(Bucket=bucket):
                for item in page.get("Contents", []):
                    key = item.get("Key", "")
                    if not key or key.endswith("/"):
                        continue

                    path = PurePosixPath(key)
                    last_modified = item.get("LastModified")

                    writer.writerow(
                        {
                            "bucket": bucket,
                            "object_key": key,
                            "filename": path.name,
                            "folder": (
                                "" if str(path.parent) == "." else str(path.parent)
                            ),
                            "database_name": database_name_from_key(key),
                            "size_bytes": item.get("Size", ""),
                            "last_modified": (
                                last_modified.isoformat()
                                if hasattr(last_modified, "isoformat")
                                else last_modified or ""
                            ),
                            "etag": str(item.get("ETag", "")).strip('"'),
                            "storage_class": item.get("StorageClass", ""),
                        }
                    )

                    bucket_total += 1
                    total += 1

            print(f"{bucket}: exported {bucket_total} objects")

    return total


def main() -> int:
    load_dotenv(Path(__file__).with_name(".env"), override=False)

    try:
        account_id = require_environment_variable("R2_ACCOUNT_ID")
        access_key_id = require_environment_variable("R2_ACCESS_KEY_ID")
        secret_access_key = require_environment_variable(
            "R2_SECRET_ACCESS_KEY"
        )

        endpoint = os.getenv("R2_ENDPOINT", "").strip()
        if not endpoint:
            endpoint = f"https://{account_id}.r2.cloudflarestorage.com"

        r2 = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name="auto",
        )

        configured_bucket = os.getenv("R2_BUCKET", "").strip()
        buckets = get_bucket_names(r2, configured_bucket)

        if not buckets:
            print("No accessible R2 buckets found.")
            return 0

        total = export_objects(r2, buckets)
        print(f"\nExported {total} objects to:")
        print(OUTPUT_FILE)
        return 0

    except ValueError as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 2
    except ClientError as error:
        details = error.response.get("Error", {})
        code = details.get("Code", "ClientError")
        message = details.get("Message", str(error))
        print(f"R2 error ({code}): {message}", file=sys.stderr)
        return 1
    except BotoCoreError as error:
        print(f"R2 connection error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
