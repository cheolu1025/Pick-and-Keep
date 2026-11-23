"""저장 연결부 (B 담당)

화면에서 고른 사진을 실제로 저장하는 곳. A의 함수(aws_utils, db)를 여기서만 부른다.
  - .env에 S3_BUCKET_NAME이 있으면 → S3 + DB (mode "aws")
  - 비어 있거나 USE_LOCAL_STORAGE=true 이면 → tmp/_local_store/ 에 임시 저장 (mode "local")
버킷이 없는 컴퓨터에서도 화면과 저장 흐름을 끝까지 테스트할 수 있게 하려는 것.
"""
import json
import os
import shutil
from datetime import datetime, timezone

TMP_DIR = os.environ.get("TMP_DIR", "tmp")
LOCAL_STORE = os.path.join(TMP_DIR, "_local_store")


def mode() -> str:
    if os.environ.get("USE_LOCAL_STORAGE", "false").lower() == "true":
        return "local"
    return "aws" if os.environ.get("S3_BUCKET_NAME", "").strip() else "local"


def save_photo(r: dict, folder: str) -> dict:
    """사진 한 장 저장. r은 result.json의 한 항목, folder는 "keep" | "hold"
    반환: {"status": "saved" | "duplicate", "key": 저장 위치}"""
    if mode() == "aws":
        return _save_aws(r, folder)
    return _save_local(r, folder)


# ---------- A 함수로 저장 (S3 + DB) ----------
def _save_aws(r: dict, folder: str) -> dict:
    import sqlite3

    import aws_utils
    import db

    # 1) 이미 보관함에 있는 사진이면 올리지 않음 (같은 해시 = 같은 사진)
    if db.is_duplicate(r["hash"]):
        return {"status": "duplicate", "key": None}

    # 2) S3에 업로드: r["path"]는 원본 해상도 (HEIC면 원본 해상도 그대로 JPG 변환본)
    s3_key = aws_utils.upload_photo(r["path"], folder)

    # 3) DB에 기록. 실패하면 S3에 올린 것도 지워서 '주인 없는 파일'이 남지 않게
    try:
        db.save_photo_record(r["original_name"], s3_key, folder,
                             r.get("labels", []), r["size"], file_hash=r["hash"])
    except sqlite3.IntegrityError:
        # 1)과 3) 사이에 같은 사진이 먼저 저장된 경우 (두 탭에서 동시에 저장 등)
        aws_utils.delete_photo(s3_key)
        return {"status": "duplicate", "key": None}
    except Exception:
        aws_utils.delete_photo(s3_key)
        raise
    return {"status": "saved", "key": s3_key}


# ---------- 임시 저장 (A 함수가 오기 전) ----------
def _load_index() -> list:
    path = os.path.join(LOCAL_STORE, "index.json")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fp:
        return json.load(fp)


def _save_local(r: dict, folder: str) -> dict:
    os.makedirs(os.path.join(LOCAL_STORE, folder), exist_ok=True)
    index = _load_index()
    if r.get("hash") and any(item.get("hash") == r["hash"] for item in index):
        return {"status": "duplicate", "key": None}

    key = f"{folder}/{r['photo_id']}{os.path.splitext(r['path'])[1]}"
    shutil.copy(r["path"], os.path.join(LOCAL_STORE, key))
    index.append({"key": key, "folder": folder, "filename": r["original_name"],
                  "size": r["size"], "hash": r.get("hash"), "labels": r.get("labels", []),
                  "saved_at": datetime.now(timezone.utc).isoformat()})
    with open(os.path.join(LOCAL_STORE, "index.json"), "w", encoding="utf-8") as fp:
        json.dump(index, fp, ensure_ascii=False, indent=2)
    return {"status": "saved", "key": key}


# ---------- 첫 화면 오른쪽 필름: 최근 평생 보관한 사진 ----------
def recent_keep_photos(limit: int = 6) -> list[dict]:
    """최근 평생 보관함 사진 [{"url", "name"}]. 실패해도 첫 화면은 떠야 하므로 []를 돌려준다.
    목록용이라 mark_viewed=False (A 요청: 목록만 봐도 '열람'으로 기록되면 안 됨)"""
    try:
        if mode() == "aws":
            import aws_utils
            import db
            return [{"url": aws_utils.get_photo_url(p["s3_key"], mark_viewed=False),
                     "name": p["filename"]}
                    for p in db.get_photos("keep")[:limit]]
        items = [i for i in _load_index() if i["folder"] == "keep"][-limit:][::-1]
        return [{"url": f"/local/{i['key']}", "name": i["filename"]} for i in items]
    except Exception:
        return []
