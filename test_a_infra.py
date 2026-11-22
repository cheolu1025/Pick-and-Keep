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
os.environ["DB_PATH"] = TEST_DB  # set before importing db (dotenv will not override)

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
    db.delete_photo_record("keep/2026/11/aaa_test.jpg")
    assert len(db.get_photos("keep")) == 2
    try:
        db.save_photo_record("x.jpg", "trash/x.jpg", "trash", [], 1)
        raise AssertionError("invalid folder accepted")
    except ValueError:
        pass
    ok("update_photo_location / delete_photo_record / folder check")


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
        if db_only:
            print("\nDB tests passed (S3 skipped: --db-only)")
        else:
            test_aws()
            print("\nAll tests passed")
    finally:
        remove_test_db()


if __name__ == "__main__":
    main()
