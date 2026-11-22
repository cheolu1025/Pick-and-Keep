"""SQLite storage for Pick & Keep photo records (owner: A).

B calls these functions only; the table structure is internal to this module.
All timestamps are UTC ISO 8601 strings, so periods can be compared as strings.
"""

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

load_dotenv()

FOLDERS = ("keep", "hold")

# status: kept for compatibility, always 'done'. SQS progress is tracked by B
# in tmp/<batch_id>/<photo_id>.result.json, not in this DB (agreed 2026-11-22).
_SCHEMA = """
CREATE TABLE IF NOT EXISTS photos (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    filename     TEXT NOT NULL,
    s3_key       TEXT NOT NULL UNIQUE,
    folder       TEXT NOT NULL CHECK (folder IN ('keep', 'hold')),
    labels       TEXT,
    size         INTEGER NOT NULL,
    uploaded_at  TEXT NOT NULL,
    last_viewed  TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'done'
        CHECK (status IN ('pending', 'done', 'failed')),
    file_hash    TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_photos_file_hash ON photos (file_hash);
"""


# ---------- internal helpers ----------

def _db_path() -> str:
    return os.getenv("DB_PATH", "photos.db")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def _connect():
    """Open a connection, commit on success, roll back on error, always close."""
    conn = sqlite3.connect(_db_path(), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["labels"] = json.loads(d["labels"]) if d["labels"] else []
    return d


def _check_folder(folder: str) -> None:
    if folder not in FOLDERS:
        raise ValueError(f"folder must be one of {FOLDERS}, got {folder!r}")


# ---------- interface (agreed with B) ----------

def init_db() -> None:
    """Create the photos table and indexes if they do not exist."""
    with _connect() as conn:
        conn.executescript(_SCHEMA)


def save_photo_record(filename: str, s3_key: str, folder: str,
                      labels: list[dict], size: int,
                      file_hash: str | None = None) -> int:
    """Insert a photo record and return its id.

    file_hash is optional (SHA-256 of the original upload bytes). If the same
    hash already exists, sqlite3.IntegrityError is raised, so call
    is_duplicate() first.
    """
    _check_folder(folder)
    now = _now()
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO photos (filename, s3_key, folder, labels, size,"
            " uploaded_at, last_viewed, file_hash)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (filename, s3_key, folder, json.dumps(labels, ensure_ascii=False),
             size, now, now, file_hash),
        )
        return cur.lastrowid


def get_photos(folder: str) -> list[dict]:
    """Return all photos in a folder, newest first. labels is a list of dicts."""
    _check_folder(folder)
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM photos WHERE folder = ? ORDER BY uploaded_at DESC, id DESC",
            (folder,),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def update_last_viewed(s3_key: str) -> None:
    """Set last_viewed to now. Also used for the '60-day extension' button."""
    with _connect() as conn:
        conn.execute("UPDATE photos SET last_viewed = ? WHERE s3_key = ?",
                     (_now(), s3_key))


def get_saved_size() -> int:
    """Total bytes of all saved photos (keep + hold) = space the user can free."""
    with _connect() as conn:
        row = conn.execute("SELECT COALESCE(SUM(size), 0) FROM photos").fetchone()
    return int(row[0])


def get_old_hold_photos(days: int = 60) -> list[dict]:
    """Hold photos whose last_viewed is older than `days` days."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM photos WHERE folder = 'hold' AND last_viewed < ?"
            " ORDER BY last_viewed",
            (cutoff,),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def delete_photo_record(s3_key: str) -> None:
    """Delete a photo record. Call together with aws_utils.delete_photo()."""
    with _connect() as conn:
        conn.execute("DELETE FROM photos WHERE s3_key = ?", (s3_key,))


def is_duplicate(file_hash: str) -> dict | None:
    """Return the existing photo with this hash, or None if it is new."""
    with _connect() as conn:
        row = conn.execute("SELECT * FROM photos WHERE file_hash = ?",
                           (file_hash,)).fetchone()
    return _row_to_dict(row) if row else None


# ---------- used inside A's code ----------

def update_photo_location(old_s3_key: str, new_s3_key: str, new_folder: str) -> None:
    """Change folder and s3_key after an S3 move (used by aws_utils.move_photo)."""
    _check_folder(new_folder)
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE photos SET s3_key = ?, folder = ? WHERE s3_key = ?",
            (new_s3_key, new_folder, old_s3_key),
        )
        if cur.rowcount == 0:
            raise LookupError(f"no photo record for s3_key {old_s3_key!r}")
