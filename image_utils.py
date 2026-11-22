"""이미지 처리 (B 담당)

- 업로드 파일 저장
- HEIC → JPEG 변환
- Rekognition 전송용 리사이즈 (5MB 이하)
- EXIF 정보, 해상도, 해시 추출 (스크린샷 판별·캐시용)
"""
import hashlib
import io
import os
import uuid

from PIL import Image, ImageOps
from pillow_heif import register_heif_opener

register_heif_opener()  # Pillow가 HEIC/HEIF를 열 수 있게 등록

TMP_DIR = os.environ.get("TMP_DIR", "tmp")
ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".heic", ".heif"}

REKOGNITION_MAX_BYTES = 5 * 1024 * 1024  # Rekognition 바이트 직접 전송 한도
REKOGNITION_MAX_SIDE = 1920              # 분류에는 이 정도 해상도면 충분


def is_allowed(filename: str) -> bool:
    return os.path.splitext(filename.lower())[1] in ALLOWED_EXT


def new_batch_dir() -> tuple[str, str]:
    """업로드 한 번(여러 장)을 묶는 임시 폴더. (batch_id, 경로) 반환"""
    batch_id = uuid.uuid4().hex[:12]
    path = os.path.join(TMP_DIR, batch_id)
    os.makedirs(path, exist_ok=True)
    return batch_id, path


def save_upload(file_storage, batch_path: str) -> dict:
    """업로드된 파일을 원본 그대로 저장.
    원본 파일명(한글 가능)은 따로 보관하고, 디스크에는 영문 UUID 이름으로 저장한다."""
    original_name = file_storage.filename
    ext = os.path.splitext(original_name.lower())[1]
    photo_id = uuid.uuid4().hex[:12]
    stored_path = os.path.join(batch_path, f"{photo_id}{ext}")
    file_storage.save(stored_path)
    return {"photo_id": photo_id, "original_name": original_name, "path": stored_path}


def to_jpeg_if_heic(path: str) -> str:
    """HEIC면 같은 폴더에 JPEG로 변환해서 새 경로 반환, 아니면 그대로 반환.
    EXIF(카메라 정보)는 스크린샷 판별에 필요하므로 유지한다."""
    if os.path.splitext(path.lower())[1] not in (".heic", ".heif"):
        return path
    jpg_path = os.path.splitext(path)[0] + ".jpg"
    with Image.open(path) as img:
        exif = img.info.get("exif")
        rgb = img.convert("RGB")
        if exif:
            rgb.save(jpg_path, "JPEG", quality=92, exif=exif)
        else:
            rgb.save(jpg_path, "JPEG", quality=92)
    return jpg_path


def prepare_for_rekognition(path: str) -> bytes:
    """Rekognition에 보낼 JPEG 바이트 (5MB 이하). 원본 파일은 건드리지 않는다."""
    with Image.open(path) as img:
        img = ImageOps.exif_transpose(img)  # 폰 사진 회전 정보 반영
        img = img.convert("RGB")
        img.thumbnail((REKOGNITION_MAX_SIDE, REKOGNITION_MAX_SIDE))

        quality = 90
        while True:
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=quality)
            data = buf.getvalue()
            if len(data) <= REKOGNITION_MAX_BYTES or quality <= 40:
                return data
            quality -= 10


def get_image_info(path: str) -> dict:
    """스크린샷 판별에 쓰는 정보: 형식, 해상도, 카메라 EXIF 유무"""
    with Image.open(path) as img:
        exif = img.getexif()
        make = exif.get(0x010F)   # 카메라 제조사
        model = exif.get(0x0110)  # 카메라 모델
        return {
            "format": (img.format or "").upper(),
            "width": img.width,
            "height": img.height,
            "has_camera_exif": bool(make or model),
            "camera": " ".join(str(x) for x in (make, model) if x) or None,
        }


def file_hash(path: str) -> str:
    """같은 사진 재업로드 판별용 SHA-256 (3주차 캐시에서 사용)"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def make_thumbnail(path: str, max_side: int = 480) -> str:
    """화면 표시용 작은 JPEG (분류 결과 화면에서 사용)"""
    thumb_path = os.path.splitext(path)[0] + "_thumb.jpg"
    with Image.open(path) as img:
        img = ImageOps.exif_transpose(img).convert("RGB")
        img.thumbnail((max_side, max_side))
        img.save(thumb_path, "JPEG", quality=80)
    return thumb_path
