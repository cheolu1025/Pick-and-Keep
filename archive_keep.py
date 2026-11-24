"""Demo mode for the keep/ -> Glacier IR lifecycle (owner: A).

Real service: the S3 lifecycle rule (deploy/setup_lifecycle.py) moves keep/
photos to Glacier Instant Retrieval after 90 days. Lifecycle rules work in
whole days and are applied around midnight UTC, so they cannot be shown live.

Demo mode: with .env KEEP_DEMO_MINUTES=3, this script changes keep/ photos
older than 3 minutes to Glacier IR right away. It copies each object onto the
same key with a new storage class, which is the same end result as the
lifecycle rule. The key does not change, so the DB and B's code are untouched.

Usage (project root, venv active):
    python3 archive_keep.py            # does nothing unless KEEP_DEMO_MINUTES is set
    python3 archive_keep.py --dry-run  # list what would be changed

On EC2 this runs every minute from cron (deploy/crontab.txt), like check_hold.py.
"""

import sys
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

import aws_utils
import db

load_dotenv()

ARCHIVE_CLASS = "GLACIER_IR"
KST = timezone(timedelta(hours=9))


def demo_period() -> timedelta | None:
    """KEEP_DEMO_MINUTES as a timedelta, or None when demo mode is off."""
    minutes = db._positive_float("KEEP_DEMO_MINUTES")  # same parsing as HOLD_DEMO_MINUTES
    return timedelta(minutes=minutes) if minutes else None


def archive_old_keep(older_than: timedelta, prefix: str = "keep/",
                     dry_run: bool = False) -> list[str]:
    """Change keep/ objects older than `older_than` to Glacier IR.

    Age is the S3 LastModified time, i.e. when the photo entered keep/
    (a hold -> keep move counts from the move). Objects already in Glacier IR
    are skipped. Returns the changed (or, with dry_run, the matching) keys.
    prefix must stay inside keep/; tests pass one exact key.
    """
    if not prefix.startswith("keep/"):
        raise ValueError(f"only keep/ photos are archived, got prefix {prefix!r}")
    s3, bucket = aws_utils._s3(), aws_utils._bucket()
    cutoff = datetime.now(timezone.utc) - older_than
    changed = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            if obj.get("StorageClass", "STANDARD") != "STANDARD" or obj["LastModified"] > cutoff:
                continue
            if not dry_run:
                # same key, new storage class; metadata and ContentType are kept
                s3.copy_object(Bucket=bucket, Key=obj["Key"],
                               CopySource={"Bucket": bucket, "Key": obj["Key"]},
                               StorageClass=ARCHIVE_CLASS, MetadataDirective="COPY")
            changed.append(obj["Key"])
    return changed


def main() -> int:
    period = demo_period()
    if period is None:
        return 0  # demo mode off: the real lifecycle rule handles keep/ (no log spam)

    dry_run = "--dry-run" in sys.argv
    stamp = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST")
    keys = archive_old_keep(period, dry_run=dry_run)
    if keys:
        verb = "would move" if dry_run else "moved"
        print(f"{stamp} {verb} {len(keys)} keep photo(s) to {ARCHIVE_CLASS} (demo {period.total_seconds() / 60:g} min)")
        for k in keys:
            print(f"  {k}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
