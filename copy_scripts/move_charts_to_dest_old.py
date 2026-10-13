# Python script to copy from source s3 url to destination s3 url:
#!/usr/bin/env python3
"""
Parallel S3-to-S3 copy using boto3's managed copy (handles multipart for >5 GB).
Reads a CSV with "file_source","file_dest" columns.

Usage:
    python parallel_s3_copy.py <input.csv> [--workers 50] [--dry-run]

Outputs:
    s3_copy_<timestamp>.log   — full run log
    s3_copy_failed_<timestamp>.csv — rows that failed, ready for rerun
"""

import csv
import sys
import argparse
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import urlparse

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

# ── Timestamped log file so reruns never overwrite previous output ────────────
_RUN_TS = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(f"s3_copy_{_RUN_TS}.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

# ── Retry config: up to 10 adaptive retries handles S3 SlowDown/throttling ───
_BOTO_CONFIG = Config(
    retries={"max_attempts": 10, "mode": "adaptive"},
)

_thread_local = threading.local()


def _get_resource():
    """Return a thread-local S3 resource. boto3.resource.copy() handles multipart."""
    if not hasattr(_thread_local, "s3_resource"):
        _thread_local.s3_resource = boto3.resource("s3", config=_BOTO_CONFIG)
    return _thread_local.s3_resource


def parse_s3_url(url: str) -> tuple[str, str]:
    """Return (bucket, key) from an s3:// URL."""
    parsed = urlparse(url)
    if parsed.scheme != "s3":
        raise ValueError(f"Not an S3 URL: {url!r}")
    bucket = parsed.netloc
    key = parsed.path.lstrip("/")
    if not bucket or not key:
        raise ValueError(f"Malformed S3 URL (empty bucket or key): {url!r}")
    return bucket, key


def copy_one(row: dict, dry_run: bool) -> tuple[bool, str, dict]:
    """
    Copy a single file. Returns (success, message, original_row).
    Uses boto3 managed copy which transparently uses multipart for objects >5 GB.
    """
    src = row["file_source"].strip()
    dst = row["file_dest"].strip()
    try:
        src_bucket, src_key = parse_s3_url(src)
        dst_bucket, dst_key = parse_s3_url(dst)

        if dry_run:
            return True, f"DRY-RUN: {src} -> {dst}", row

        copy_source = {"Bucket": src_bucket, "Key": src_key}
        _get_resource().meta.client.copy(
            copy_source,
            dst_bucket,
            dst_key,
            ExtraArgs={"MetadataDirective": "COPY"},
        )
        return True, f"OK: {src} -> {dst}", row
    except (ClientError, ValueError) as e:
        return False, f"FAIL: {src} -> {dst} | {e}", row


def validate_csv_columns(rows: list[dict]) -> None:
    """Abort early if required columns are missing."""
    required = {"file_source", "file_dest"}
    if not rows:
        log.error("CSV is empty — nothing to do.")
        sys.exit(1)
    actual = set(rows[0].keys())
    missing = required - actual
    if missing:
        log.error("CSV is missing required columns: %s. Found: %s", missing, actual)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Parallel S3-to-S3 copy")
    parser.add_argument("csv_file", help="CSV file with file_source,file_dest columns")
    parser.add_argument(
        "--workers",
        type=int,
        default=50,
        help="Number of parallel threads (default: 50)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be copied without doing it",
    )
    args = parser.parse_args()

    with open(args.csv_file, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    # Normalize header keys and values (strip surrounding quotes/whitespace)
    rows = [
        {k.strip().strip('"'): v.strip().strip('"') for k, v in row.items()}
        for row in rows
        if any(v.strip() for v in row.values())  # skip blank lines
    ]

    validate_csv_columns(rows)

    total = len(rows)
    log.info("Loaded %d rows from %s", total, args.csv_file)
    log.info(
        "Starting copy with %d workers (dry_run=%s, run_id=%s)",
        args.workers,
        args.dry_run,
        _RUN_TS,
    )

    success_count = 0
    fail_count = 0
    failed_rows: list[dict] = []

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(copy_one, row, args.dry_run): row for row in rows
        }

        for i, future in enumerate(as_completed(futures), 1):
            try:
                ok, msg, original_row = future.result()
            except Exception as e:
                ok, msg = False, f"FAIL: unexpected error | {e}"
                original_row = futures[future]

            if ok:
                success_count += 1
                log.debug(msg)
            else:
                fail_count += 1
                failed_rows.append(original_row)
                log.error(msg)

            if i % 500 == 0 or i == total:
                log.info(
                    "Progress: %d/%d  (ok=%d, fail=%d)",
                    i,
                    total,
                    success_count,
                    fail_count,
                )

    log.info(
        "Done. Success: %d | Failed: %d | Total: %d", success_count, fail_count, total
    )

    # Write failed rows to a CSV so they can be passed directly to a rerun
    if failed_rows:
        failure_csv = f"s3_copy_failed_{_RUN_TS}.csv"
        with open(failure_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["file_source", "file_dest"])
            writer.writeheader()
            writer.writerows(
                {"file_source": r["file_source"], "file_dest": r["file_dest"]}
                for r in failed_rows
            )
        log.warning(
            "%d failed entries written to %s — rerun with that file to retry.",
            fail_count,
            failure_csv,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()