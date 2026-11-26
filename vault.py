"""보관함 화면(평생 보관함, 보류함, 크게 보기, 확보 용량)에서 쓰는 도우미 (B 담당)

A의 db.py, aws_utils.py를 여기서만 부른다. 지켜야 할 약속 (A 전달 문서):
  - 목록·썸네일은 get_photo_url(key, mark_viewed=False)  → 목록만 봐서는 '열람'이 아님
  - 크게 보기(/photo/<id>)에서만 get_photo_url(key)       → 이때만 열람 시각 갱신
  - 화면끼리는 s3_key 대신 id를 넘긴다 (보류 → 평생으로 옮기면 s3_key가 바뀜)
  - 배너는 get_old_hold_photos()를 인자 없이 (시연 모드 2분도 따라감)
"""
from datetime import datetime, timedelta, timezone

import aws_utils
import classify
import db
import storage

KST = timezone(timedelta(hours=9))
FOLDER_NAME = {"keep": "평생 보관함", "hold": "보류함"}

# 그룹: 고르기 화면과 같은 순서 + 스크린샷. DB category는 영문 키 (A와 약속)
GROUP_KEYS = classify.GROUP_ORDER + ["screenshot"]
GROUP_NAME = {k: v["name"] for k, v in classify.GROUP_INFO.items()} | {"screenshot": "스크린샷"}


def group_of(p: dict) -> str:
    """사진의 그룹 키. category가 없는 예전 사진은 저장된 AI 라벨로 다시 계산한다
    (화면에만 보여줄 뿐 DB는 바꾸지 않음. 라벨도 없으면 '기타')"""
    if p.get("category") in GROUP_NAME:
        return p["category"]
    if p.get("labels"):
        key, _ = classify.pick_group(p["labels"])
        if key in GROUP_NAME:
            return key
    return "etc"


def group_photos(photos: list[dict]) -> list[dict]:
    """[{"key", "name", "photos"}] 그룹 순서대로, 빈 그룹은 뺌"""
    by = {}
    for p in photos:
        by.setdefault(group_of(p), []).append(p)
    return [{"key": k, "name": GROUP_NAME[k], "photos": by[k]} for k in GROUP_KEYS if k in by]


def group_options() -> list[tuple[str, str]]:
    return [(k, GROUP_NAME[k]) for k in GROUP_KEYS]


def available() -> bool:
    """S3 버킷이 설정돼 있어야 보관함을 볼 수 있다 (로컬 저장 모드나 설정 오류면 False)"""
    return storage.mode() == "aws" and storage.problem() is None


def _parse(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso)
    except ValueError:
        return None


def kst_date(iso: str | None) -> str:
    """'2026-11-22T09:00:00+00:00' → '2026.11.22' (한국 시간)"""
    t = _parse(iso)
    return t.astimezone(KST).strftime("%Y.%m.%d") if t else ""


def _remaining(p: dict) -> dict:
    """보류함 사진의 남은 기간. {"old": bool, "text": "58일 남음" | "1분 남음" | "오래 안 봤어요"}"""
    last = _parse(p.get("last_viewed"))
    if last is None:
        return {"old": False, "text": ""}
    left = last + db.hold_period() - datetime.now(timezone.utc)
    if left.total_seconds() <= 0:
        return {"old": True, "text": "오래 안 봤어요"}
    if db.hold_period() < timedelta(days=1):           # 시연 모드: 분 단위
        return {"old": False, "text": f"{max(1, round(left.total_seconds() / 60))}분 남음"}
    return {"old": False, "text": f"{max(1, left.days + (1 if left.seconds else 0))}일 남음"}


def _top_labels(p: dict, n: int = 3) -> list[str]:
    labels = sorted(p.get("labels") or [], key=lambda l: -l.get("confidence", 0))
    return [l["name"] for l in labels[:n]]


def list_photos(folder: str) -> list[dict]:
    """목록용 사진들. url은 mark_viewed=False (목록만 봐서는 열람 아님)"""
    photos = db.get_photos(folder)
    for p in photos:
        p["url"] = aws_utils.get_photo_url(p["s3_key"], mark_viewed=False)
        p["date"] = kst_date(p.get("uploaded_at"))
        p["top_labels"] = _top_labels(p)
        if folder == "hold":
            p["remain"] = _remaining(p)
    return photos


def open_photo(photo_id: int) -> dict | None:
    """크게 보기용 사진 한 장. 여기서만 열람 시각이 기록된다."""
    p = db.get_photo(photo_id)
    if p is None:
        return None
    url = aws_utils.get_photo_url(p["s3_key"])           # mark_viewed=True (기본값)
    p = db.get_photo(photo_id)                           # 갱신된 last_viewed로 다시 읽기
    p["url"] = url
    p["date"] = kst_date(p.get("uploaded_at"))
    p["viewed"] = kst_date(p.get("last_viewed"))
    p["labels"] = sorted(p.get("labels") or [], key=lambda l: -l.get("confidence", 0))
    if p["folder"] == "hold":
        p["remain"] = _remaining(p)
    p["group"] = group_of(p)
    return p


def change_group(photo_id: int, key: str) -> dict | None:
    """보관함에서 그룹 바꾸기 (열람으로 치지 않음)"""
    if key not in GROUP_NAME:
        return None
    p = db.get_photo(photo_id)
    if p and db.update_category(photo_id, key):
        p["new_group"] = GROUP_NAME[key]
        return p
    return None


# ---------- 보류함 버튼 3개 (+ 평생 보관함 삭제) ----------

def delete(photo_id: int) -> dict | None:
    p = db.get_photo(photo_id)
    if p:
        aws_utils.delete_photo(p["s3_key"])
        db.delete_photo_record(p["s3_key"])
    return p


def extend(photo_id: int) -> dict | None:
    p = db.get_photo(photo_id)
    if p:
        db.update_last_viewed(p["s3_key"])
    return p


def move_to_keep(photo_id: int) -> dict | None:
    p = db.get_photo(photo_id)
    if p and p["folder"] == "hold":
        aws_utils.move_photo(p["s3_key"], "keep")       # S3 이동 + DB 갱신까지 A 함수가 처리
    return p


# ---------- 첫 화면 배너, 확보 용량 ----------

def old_hold_banner() -> str | None:
    """'60일 동안 안 본 사진이 3장 있어요' 또는 None. 실패해도 첫 화면은 떠야 함"""
    if not available():
        return None
    try:
        old = db.get_old_hold_photos()                   # 인자 없이! (.env 기간을 따름)
    except Exception:
        return None
    return f"보류함에 {db.hold_period_label()} 동안 안 본 사진이 {len(old)}장 있어요" if old else None


def stats() -> dict:
    keep, hold = db.get_photos("keep"), db.get_photos("hold")
    return {
        "keep": len(keep), "hold": len(hold),
        "keep_size": sum(p["size"] or 0 for p in keep),
        "hold_size": sum(p["size"] or 0 for p in hold),
        "total_size": db.get_saved_size(),
        "old": len(db.get_old_hold_photos()),
        "period": db.hold_period_label(),
    }
