"""저장 연결부 (B 담당)

화면에서 고른 사진을 실제로 저장하는 곳. A의 함수를 여기서만 부른다.
  - A의 aws_utils.upload_photo, db.save_photo_record가 준비돼 있으면 → S3 + DB (mode "aws")
  - 아직 없거나 USE_LOCAL_STORAGE=true 이면 → tmp/_local_store/ 에 임시 저장 (mode "local")
그래서 A 함수가 올라오기 전에도 화면과 저장 흐름을 끝까지 테스트할 수 있다.
"""
import inspect
import json
import os
import shutil
from datetime import datetime, timezone

TMP_DIR = os.environ.get("TMP_DIR", "tmp")
LOCAL_STORE = os.path.join(TMP_DIR, "_local_store")


def _a_functions():
    """A의 함수 모듈. 아직 비어 있으면 None"""
    try:
        import aws_utils
        import db
    except Exception:
        return None
    if hasattr(aws_utils, "upload_photo") and hasattr(db, "save_photo_record"):
        return aws_utils, db
    return None


def mode() -> str:
    if os.environ.get("USE_LOCAL_STORAGE", "false").lower() == "true":
        return "local"
    return "aws" if _a_functions() else "local"


def save_photo(r: dict, folder: str) -> dict:
    """사진 한 장 저장. r은 result.json의 한 항목, folder는 "keep" | "hold"
    반환: {"status": "saved" | "duplicate", "key": 저장 위치}"""
    if mode() == "aws":
        return _save_aws(r, folder)
    return _save_local(r, folder)


# ---------- A 함수로 저장 (S3 + DB) ----------
def _save_aws(r: dict, folder: str) -> dict:
    aws_utils, db = _a_functions()

    # 이미 보관함에 있는 사진이면 건너뜀 (A의 is_duplicate가 있을 때만)
    if r.get("hash") and hasattr(db, "is_duplicate") and db.is_duplicate(r["hash"]):
        return {"status": "duplicate", "key": None}

    s3_key = aws_utils.upload_photo(r["path"], folder)
    try:
        kwargs = {}
        # A가 file_hash 선택 인자를 추가한 버전이면 같이 넘김
        if "file_hash" in inspect.signature(db.save_photo_record).parameters:
            kwargs["file_hash"] = r.get("hash")
        db.save_photo_record(r["original_name"], s3_key, folder,
                             r.get("labels", []), r["size"], **kwargs)
    except Exception:
        # DB 저장이 실패하면 S3에 올린 사진도 지워서 '주인 없는 파일'이 남지 않게
        if hasattr(aws_utils, "delete_photo"):
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
