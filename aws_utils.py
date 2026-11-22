"""S3 helpers for Pick & Keep (owner: A).

No credentials in code: locally boto3 reads ~/.aws/credentials,
on EC2 it uses the LabInstanceProfile IAM role.
"""

import mimetypes
import os
import re
import uuid
from datetime import datetime, timezone

import boto3
from botocore.config import Config
from dotenv import load_dotenv

import db

load_dotenv()

_s3_client = None


# ---------- internal helpers ----------

def _bucket() -> str:
    name = os.getenv("S3_BUCKET_NAME")
    if not name:
        raise RuntimeError("S3_BUCKET_NAME is not set in .env")
    return name


def _s3():
    """Create the S3 client once and reuse it."""
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client(
            "s3",
            region_name=os.getenv("AWS_REGION", "us-east-1"),
            config=Config(signature_version="s3v4"),
        )
    return _s3_client


def _make_s3_key(folder: str, filename: str) -> str:
    """Build 'folder/YYYY/MM/<uuid12>_<ascii-name>.ext' (ascii part optional)."""
    stem, ext = os.path.splitext(os.path.basename(filename))
    ext = ext.lower() if re.fullmatch(r"\.[A-Za-z0-9]{1,5}", ext) else ""
    ascii_stem = re.sub(r"[^A-Za-z0-9_-]+", "_", stem).strip("_")[:50]
    uid = uuid.uuid4().hex[:12]
    name = f"{uid}_{ascii_stem}" if ascii_stem else uid
    now = datetime.now(timezone.utc)
    return f"{folder}/{now:%Y}/{now:%m}/{name}{ext}"


# ---------- interface (agreed with B) ----------

def upload_photo(file_path: str, folder: str) -> str:
    """Upload a local file to S3 under keep/ or hold/ and return its S3 key."""
    if folder not in db.FOLDERS:
        raise ValueError(f"folder must be one of {db.FOLDERS}, got {folder!r}")
    key = _make_s3_key(folder, file_path)
    content_type = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
    _s3().upload_file(file_path, _bucket(), key,
                      ExtraArgs={"ContentType": content_type})
    return key


def get_photo_url(s3_key: str, expires: int = 3600, *,
                  mark_viewed: bool = True) -> str:
    """Return a temporary (presigned) URL.

    mark_viewed=True records the view time in the DB (opening one photo).
    Use mark_viewed=False for list/thumbnail pages, so that simply opening the
    hold list does not reset the 60-day timer of every photo in it.
    """
    url = _s3().generate_presigned_url(
        "get_object",
        Params={"Bucket": _bucket(), "Key": s3_key},
        ExpiresIn=expires,
    )
    if mark_viewed:
        db.update_last_viewed(s3_key)
    return url


def delete_photo(s3_key: str) -> None:
    """Delete the S3 object. Call db.delete_photo_record() as well."""
    _s3().delete_object(Bucket=_bucket(), Key=s3_key)


def move_photo(s3_key: str, to_folder: str) -> str:
    """Move a photo between folders (e.g. hold -> keep) and return the new key.

    Order: copy -> update DB -> delete original. If the DB update fails the
    copy is removed, so S3 and DB never point to different places.
    """
    if to_folder not in db.FOLDERS:
        raise ValueError(f"folder must be one of {db.FOLDERS}, got {to_folder!r}")
    from_folder, _, rest = s3_key.partition("/")
    if from_folder == to_folder:
        return s3_key
    new_key = f"{to_folder}/{rest}"
    bucket = _bucket()

    _s3().copy_object(Bucket=bucket, Key=new_key,
                      CopySource={"Bucket": bucket, "Key": s3_key})
    try:
        db.update_photo_location(s3_key, new_key, to_folder)
    except Exception:
        _s3().delete_object(Bucket=bucket, Key=new_key)
        raise
    _s3().delete_object(Bucket=bucket, Key=s3_key)
    return new_key
