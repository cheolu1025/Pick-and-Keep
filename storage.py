"""저장 연결부 (B 담당)

화면에서 고른 사진을 실제로 저장하는 곳. A의 함수(aws_utils, db)를 여기서만 부른다.
  - 기본: S3 + DB (mode "aws")
  - .env에 USE_LOCAL_STORAGE=true 라고 직접 켰을 때만 → tmp/_local_store/ 에 저장 (mode "local")
    버킷이 없는 컴퓨터에서 화면만 테스트할 때 쓴다.
서버에서 .env가 잘못돼 S3_BUCKET_NAME이 비면 로컬에 조용히 저장하지 않고,
앱 시작 때 경고를 찍고 저장 화면에 오류를 보여준다 (A 요청).
"""
import inspect
import json
import os
import shutil
from datetime import datetime, timezone

TMP_DIR = os.environ.get("TMP_DIR", "tmp")
LOCAL_STORE = os.path.join(TMP_DIR, "_local_store")


def mode() -> str:
    if os.environ.get("USE_LOCAL_STORAGE", "false").strip().lower() == "true":
        return "local"
    return "aws"


def problem() -> str | None:
    """저장할 수 없는 설정이면 사람이 읽을 안내 문장, 괜찮으면 None"""
    if mode() == "aws" and not os.environ.get("S3_BUCKET_NAME", "").strip():
        return (".env에 S3_BUCKET_NAME이 비어 있어서 사진을 저장할 수 없어요. "
                "버킷 이름을 넣거나, 이 컴퓨터에서 화면만 테스트하려면 USE_LOCAL_STORAGE=true를 넣어 주세요.")
    return None


def _accepts_category() -> bool:
    """A의 save_photo_record가 category 인자를 받는지 (A가 올리기 전/후 모두 동작하게)"""
    import db
    return "category" in inspect.signature(db.save_photo_record).parameters


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
    #    category: 화면에서 최종으로 고른 그룹 키 (사용자가 드롭다운으로 바꿨으면 바꾼 값)
    extra = {"category": r.get("final_group")} if _accepts_category() else {}
    try:
        db.save_photo_record(r["original_name"], s3_key, folder,
                             r.get("labels", []), r["size"], file_hash=r["hash"], **extra)
    except sqlite3.IntegrityError:
        # 1)과 3) 사이에 같은 사진이 먼저 저장된 경우 (두 탭에서 동시에 저장 등)
        aws_utils.delete_photo(s3_key)
        return {"status": "duplicate", "key": None}
    except Exception:
        aws_utils.delete_photo(s3_key)
        raise
    return {"status": "saved", "key": s3_key}


# ---------- 로컬 저장 (USE_LOCAL_STORAGE=true 일 때만) ----------
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
                  "category": r.get("final_group"),
                  "saved_at": datetime.now(timezone.utc).isoformat()})
    with open(os.path.join(LOCAL_STORE, "index.json"), "w", encoding="utf-8") as fp:
        json.dump(index, fp, ensure_ascii=False, indent=2)
    return {"status": "saved", "key": key}

