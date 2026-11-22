"""분류 (B 담당)

1~2주차: 스크린샷 판별
  1차 규칙 점수 → 확실하면 Rekognition 호출 생략
  애매하면 DetectText로 글자 줄 수 확인
3~4주차: 종류별 그룹화 (group_photos, 아직 미구현)
"""
import os

import image_utils
import rekognition_utils

# 자주 쓰이는 폰 화면 해상도 (가로, 세로). 가로 모드 스크린샷도 잡기 위해 뒤집어서도 비교한다.
PHONE_RESOLUTIONS = {
    # iPhone
    (750, 1334), (828, 1792), (1080, 1920), (1125, 2436), (1170, 2532),
    (1179, 2556), (1242, 2208), (1242, 2688), (1284, 2778), (1290, 2796),
    (1206, 2622), (1320, 2868),
    # Galaxy / Android
    (720, 1280), (720, 1600), (1080, 2220), (1080, 2280), (1080, 2340),
    (1080, 2400), (1440, 2560), (1440, 2960), (1440, 3040), (1440, 3088),
    (1440, 3120), (1440, 3200),
}

SCREENSHOT_NAME_HINTS = ("screenshot", "스크린샷", "screen shot", "capture", "캡처")

SURE_SCREENSHOT = 4      # 이 점수 이상이면 스크린샷 확정
SURE_PHOTO = 0           # 이 점수 이하면 일반 사진 확정
TEXT_LINES_THRESHOLD = 5 # 애매할 때 글자 줄이 이 이상이면 스크린샷 (테스트 사진으로 조정)


def screenshot_score(original_name: str, info: dict) -> tuple[int, list[str]]:
    """규칙 점수와 근거 목록 반환. 근거는 화면에 보여주고 튜닝할 때 쓴다."""
    score, reasons = 0, []

    name = original_name.lower()
    if any(h in name for h in SCREENSHOT_NAME_HINTS):
        score += 3
        reasons.append("파일명에 스크린샷 표시")

    w, h = info["width"], info["height"]
    if (w, h) in PHONE_RESOLUTIONS or (h, w) in PHONE_RESOLUTIONS:
        score += 2
        reasons.append(f"폰 화면 해상도 일치 ({w}x{h})")

    if info["format"] == "PNG":
        score += 1
        reasons.append("PNG 형식")

    if info["has_camera_exif"]:
        score -= 3
        reasons.append(f"카메라 정보 있음 ({info['camera']})")
    else:
        score += 1
        reasons.append("카메라 정보 없음")

    return score, reasons


def classify_photo(original_name: str, path: str) -> dict:
    """사진 한 장 분류. 반환 예:
    {"is_screenshot": False, "method": "rules", "score": -2, "reasons": [...],
     "labels": [{"name": "Dog", "confidence": 98.1}, ...], "text_lines": None}
    """
    info = image_utils.get_image_info(path)
    score, reasons = screenshot_score(original_name, info)
    result = {"score": score, "reasons": reasons, "labels": [], "text_lines": None}

    if score >= SURE_SCREENSHOT:
        # 스크린샷 확정 → Rekognition 호출 생략 (비용 절약)
        result.update(is_screenshot=True, method="rules")
        return result

    image_bytes = image_utils.prepare_for_rekognition(path)

    if score > SURE_PHOTO:
        # 애매한 경우에만 DetectText로 한 번 더 확인
        lines = rekognition_utils.detect_text(image_bytes)
        result["text_lines"] = len(lines)
        if len(lines) >= TEXT_LINES_THRESHOLD:
            result.update(is_screenshot=True, method="detect_text")
            return result
        result["method"] = "detect_text"
    else:
        result["method"] = "rules"

    result["is_screenshot"] = False
    result["labels"] = rekognition_utils.analyze_image(image_bytes)
    return result
