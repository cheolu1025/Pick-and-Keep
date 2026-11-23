"""Smoke test for A's code (db.py, aws_utils.py).

Usage (project root, venv active):
    python3 test_a_infra.py            # DB + S3 (Learner Lab must be running)
    python3 test_a_infra.py --db-only  # DB only, no AWS needed

Uses a separate test_a_infra.db, so the real photos.db is never touched.
S3 test objects are deleted at the end even if a step fails.
"""

import os
import sys
import tempfile
import urllib.request

TEST_DB = "test_a_infra.db"
# set before importing db (dotenv does not override existing values)
os.environ["DB_PATH"] = TEST_DB
os.environ["HOLD_DAYS"] = "60"
os.environ["HOLD_DEMO_MINUTES"] = ""

import db  # noqa: E402

LABELS = [{"name": "Food", "confidence": 97.2}, {"name": "Pizza", "confidence": 88.1}]


def ok(msg: str) -> None:
    print(f"[OK] {msg}")


def remove_test_db() -> None:
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(TEST_DB + suffix):
            os.remove(TEST_DB + suffix)


def test_db() -> None:
    db.init_db()
    db.init_db()  # second call must not fail
    ok("init_db (table + WAL)")

    pid = db.save_photo_record("점심_피자.jpg", "hold/2026/11/aaa_test.jpg", "hold",
                               LABELS, 1000, file_hash="hash-1")
    db.save_photo_record("cat.png", "keep/2026/11/bbb_cat.png", "keep", [], 500)
    db.save_photo_record("dog.png", "keep/2026/11/ccc_dog.png", "keep", [], 300)  # no hash x2
    hold = db.get_photos("hold")
    assert len(hold) == 1 and hold[0]["filename"] == "점심_피자.jpg"
    assert hold[0]["labels"] == LABELS and hold[0]["status"] == "done"
    assert len(db.get_photos("keep")) == 2
    ok("save_photo_record / get_photos (Korean filename, labels JSON, status=done)")

    assert db.get_saved_size() == 1800
    ok("get_saved_size")

    assert db.is_duplicate("hash-1")["id"] == pid
    assert db.is_duplicate("hash-unknown") is None
    try:
        db.save_photo_record("again.jpg", "hold/2026/11/ddd.jpg", "hold", [], 1, file_hash="hash-1")
        raise AssertionError("duplicate hash was saved")
    except db.sqlite3.IntegrityError:
        pass
    ok("is_duplicate + duplicate hash rejected")

    assert db.get_old_hold_photos(days=60) == []
    assert len(db.get_old_hold_photos(days=-1)) == 1
    before = hold[0]["last_viewed"]
    with db._connect() as conn:  # simulate an old photo (demo method)
        conn.execute("UPDATE photos SET last_viewed = '2026-01-01T00:00:00+00:00' WHERE id = ?", (pid,))
    assert len(db.get_old_hold_photos(days=60)) == 1
    db.update_last_viewed("hold/2026/11/aaa_test.jpg")
    assert db.get_old_hold_photos(days=60) == []
    assert db.get_photos("hold")[0]["last_viewed"] >= before
    ok("get_old_hold_photos / update_last_viewed (60-day extension)")

    db.update_photo_location("hold/2026/11/aaa_test.jpg", "keep/2026/11/aaa_test.jpg", "keep")
    assert len(db.get_photos("keep")) == 3 and db.get_photos("hold") == []
    moved = db.get_photo(pid)  # same id after a move, new key and folder
    assert moved["s3_key"] == "keep/2026/11/aaa_test.jpg" and moved["folder"] == "keep"
    assert moved["filename"] == "점심_피자.jpg" and db.get_photo(99999) is None
    db.delete_photo_record("keep/2026/11/aaa_test.jpg")
    assert len(db.get_photos("keep")) == 2
    try:
        db.save_photo_record("x.jpg", "trash/x.jpg", "trash", [], 1)
        raise AssertionError("invalid folder accepted")
    except ValueError:
        pass
    ok("update_photo_location / get_photo(id) after move / delete_photo_record / folder check")


def _set_last_viewed(s3_key: str, last_viewed: str, notified_at: str | None = None) -> None:
    with db._connect() as conn:
        conn.execute("UPDATE photos SET last_viewed = ?, notified_at = ? WHERE s3_key = ?",
                     (last_viewed, notified_at, s3_key))


def test_hold_alert() -> None:
    import sqlite3
    from datetime import timedelta

    import check_hold

    # hold period: normal / demo mode / invalid value
    assert db.hold_period() == timedelta(days=60) and db.hold_period_label() == "60일"
    os.environ["HOLD_DEMO_MINUTES"] = "2"
    assert db.hold_period() == timedelta(minutes=2) and db.hold_period_label() == "2분"
    os.environ["HOLD_DEMO_MINUTES"] = "abc"
    assert db.hold_period() == timedelta(days=60)
    os.environ["HOLD_DEMO_MINUTES"] = ""
    ok("hold_period / hold_period_label (60일, demo 2분, invalid -> 60일)")

    key = "hold/2026/11/alert_test.jpg"
    db.save_photo_record("보류_테스트.jpg", key, "hold", [], 2_500_000)
    assert db.get_old_hold_photos() == [] and db.get_photos_to_notify() == []
    os.environ["HOLD_DEMO_MINUTES"] = "2"
    _set_last_viewed(key, db._hold_cutoff(0.01))  # ~14 minutes ago
    assert len(db.get_old_hold_photos()) == 1  # banner follows demo mode
    assert len(db.get_old_hold_photos(60)) == 0  # explicit days still works
    os.environ["HOLD_DEMO_MINUTES"] = ""
    ok("get_old_hold_photos() follows demo mode, explicit days unchanged")

    _set_last_viewed(key, "2026-01-01T00:00:00+00:00")
    assert len(db.get_photos_to_notify()) == 1
    db.mark_notified([key])
    assert db.get_photos_to_notify() == []  # reported only once
    _set_last_viewed(key, "2026-01-03T00:00:00+00:00", notified_at="2026-01-02T00:00:00+00:00")
    assert len(db.get_photos_to_notify()) == 1  # extended, then old again -> report again
    db.mark_notified([])  # no-op
    ok("get_photos_to_notify / mark_notified (no repeated email, re-alert after extension)")

    photos = [{"filename": f"사진{i}.jpg", "size": 1_048_576, "last_viewed": "2026-01-01T00:00:00+00:00"}
              for i in range(12)]
    subject, body = check_hold.build_message(photos, "http://1.2.3.4")
    assert subject.isascii() and len(subject) < 100 and "12 photo(s)" in subject
    assert "사진0.jpg" in body and "외 2장" in body and "12.0 MB" in body and "http://1.2.3.4" in body
    assert "2026-01-01 09:00" in body  # shown in KST
    ok("check_hold.build_message (ASCII subject, Korean body, KST, max 10 listed)")

    db.delete_photo_record(key)

    # an old DB file without file_hash / notified_at gets the new columns
    os.environ["DB_PATH"] = "test_a_infra_old.db"
    try:
        conn = sqlite3.connect("test_a_infra_old.db")
        conn.execute("CREATE TABLE photos (id INTEGER PRIMARY KEY AUTOINCREMENT, filename TEXT NOT NULL,"
                     " s3_key TEXT NOT NULL UNIQUE, folder TEXT NOT NULL, labels TEXT, size INTEGER NOT NULL,"
                     " uploaded_at TEXT NOT NULL, last_viewed TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'done')")
        conn.commit()
        conn.close()
        db.init_db()
        with db._connect() as c:
            cols = {r["name"] for r in c.execute("PRAGMA table_info(photos)")}
        assert {"file_hash", "notified_at"} <= cols
    finally:
        os.environ["DB_PATH"] = TEST_DB
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists("test_a_infra_old.db" + suffix):
                os.remove("test_a_infra_old.db" + suffix)
    ok("init_db adds new columns to an old DB file")


def test_aws() -> None:
    import ssl

    import aws_utils
    from botocore.httpsession import get_cert_path
    from PIL import Image

    s3 = aws_utils._s3()
    bucket = aws_utils._bucket()
    created_keys = []

    def exists(key: str) -> bool:
        try:
            s3.head_object(Bucket=bucket, Key=key)
            return True
        except s3.exceptions.ClientError:
            return False

    with tempfile.TemporaryDirectory() as tmp:
        img_path = os.path.join(tmp, "red_test 사진.jpg")
        Image.new("RGB", (64, 64), (255, 0, 0)).save(img_path, "JPEG")
        with open(img_path, "rb") as f:
            original = f.read()

        try:
            key = aws_utils.upload_photo(img_path, "hold")
            created_keys.append(key)
            assert key.startswith("hold/") and key.endswith("_red_test.jpg") and exists(key)
            ok(f"upload_photo -> {key}")

            db.save_photo_record("red_test 사진.jpg", key, "hold", [], len(original))
            with db._connect() as conn:
                conn.execute("UPDATE photos SET last_viewed = '2026-01-01T00:00:00+00:00' WHERE s3_key = ?", (key,))
            aws_utils.get_photo_url(key, expires=300, mark_viewed=False)  # list page
            assert db.get_photos("hold")[0]["last_viewed"] == "2026-01-01T00:00:00+00:00"
            ok("get_photo_url(mark_viewed=False) keeps last_viewed")

            url = aws_utils.get_photo_url(key, expires=300)  # opening one photo
            # python.org macOS builds ship without CA certs; reuse botocore's bundle
            ctx = ssl.create_default_context(cafile=get_cert_path(True))
            with urllib.request.urlopen(url, timeout=10, context=ctx) as resp:
                assert resp.read() == original
            assert db.get_photos("hold")[0]["last_viewed"] > "2026-01-01T00:00:00+00:00"
            ok("get_photo_url (download matches, last_viewed updated)")

            new_key = aws_utils.move_photo(key, "keep")
            created_keys.append(new_key)
            assert new_key.startswith("keep/") and exists(new_key) and not exists(key)
            assert any(p["s3_key"] == new_key for p in db.get_photos("keep"))
            ok(f"move_photo hold -> keep ({new_key})")

            aws_utils.delete_photo(new_key)
            db.delete_photo_record(new_key)
            assert not exists(new_key)
            ok("delete_photo + delete_photo_record")
        finally:
            for k in created_keys:
                s3.delete_object(Bucket=bucket, Key=k)  # no-op if already deleted


def main() -> None:
    db_only = "--db-only" in sys.argv
    remove_test_db()
    try:
        test_db()
        test_hold_alert()
        if db_only:
            print("\nDB tests passed (S3 skipped: --db-only)")
        else:
            test_aws()
            print("\nAll tests passed")
    finally:
        remove_test_db()


if __name__ == "__main__":
    main()
