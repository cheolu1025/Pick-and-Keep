"""Rekognition 호출 (B 담당)

- analyze_image: DetectLabels (라벨 + 신뢰도 + 사진에서 차지하는 크기)
- detect_text_lines: DetectText (글자 줄 + 위치, 스크린샷 판별 보조)
- detect_text:       글자 줄 목록만

REKOGNITION_MOCK=true 이면 AWS 없이 가짜 응답을 돌려준다.
(Learner Lab이 꺼져 있을 때 화면 개발용)
"""
import os
import random

import boto3

USE_MOCK = os.environ.get("REKOGNITION_MOCK", "false").lower() == "true"
REGION = os.environ.get("AWS_REGION", "us-east-1")

_client = None


def _rekognition():
    global _client
    if _client is None:  # boto3 클라이언트는 한 번만 만들고 재사용 (스레드 간 공유 가능)
        _client = boto3.client("rekognition", region_name=REGION)
    return _client


def analyze_image(image_bytes: bytes, max_labels: int = 20,
                  min_confidence: float = 70) -> list[dict]:
    """[{"name": "Dog", "confidence": 98.1, "area": 0.42}, ...] 반환 (신뢰도 높은 순)

    area: 그 물체가 사진에서 차지하는 가장 큰 크기 (0~1, 사진 전체 = 1).
          Rekognition이 위치를 알려주는 라벨(사람, 동물, 물건 등)만 있고,
          장면 라벨(Nature, Indoors 등)은 None.
    max_labels 20: 10개만 받으면 뒤에 큰 물체(TV 등)가 있을 때 Cat 같은 중요 라벨이 잘린다.
    """
    if USE_MOCK:
        return _mock_labels(image_bytes)

    res = _rekognition().detect_labels(
        Image={"Bytes": image_bytes},
        MaxLabels=max_labels,
        MinConfidence=min_confidence,
    )
    labels = []
    for l in res["Labels"]:
        boxes = [i["BoundingBox"] for i in l.get("Instances", [])]
        area = max((b["Width"] * b["Height"] for b in boxes), default=None)
        labels.append({"name": l["Name"], "confidence": round(l["Confidence"], 1),
                       "area": round(area, 3) if area is not None else None})
    return labels


def detect_text_lines(image_bytes: bytes, min_confidence: float = 80) -> list[dict]:
    """이미지 속 글자 줄과 위치. [{"text": "3:50", "top": 0.012}, ...]
    top: 글자 줄의 위쪽 끝 위치 (0 = 사진 맨 위, 1 = 맨 아래). 폰 상단 시계 감지에 쓴다."""
    if USE_MOCK:
        # 가짜 글자는 사진 가운데쯤에 두어 '맨 위 시계' 규칙에 걸리지 않게 한다
        return [{"text": "OPEN", "top": 0.4}] if len(image_bytes) % 2 else []

    res = _rekognition().detect_text(Image={"Bytes": image_bytes})
    return [{"text": t["DetectedText"], "top": round(t["Geometry"]["BoundingBox"]["Top"], 3)}
            for t in res["TextDetections"]
            if t["Type"] == "LINE" and t["Confidence"] >= min_confidence]


def detect_text(image_bytes: bytes, min_confidence: float = 80) -> list[str]:
    """이미지 속 글자 줄 목록 (위치 없이). 한글은 잘 못 읽으므로 내용보다 '줄 수'를 판별에 쓴다."""
    return [l["text"] for l in detect_text_lines(image_bytes, min_confidence)]


# ---------- mock ----------
_MOCK_SETS = [
    [("Dog", 98.1, 0.45), ("Pet", 97.5, None), ("Animal", 97.5, None), ("Mammal", 97.5, None)],
    [("Cat", 96.3, 0.38), ("Pet", 96.3, None), ("Animal", 96.3, None)],
    [("Food", 95.2, None), ("Meal", 90.4, None), ("Dish", 88.7, 0.5)],
    [("Flower", 93.0, None), ("Plant", 93.0, None), ("Blossom", 85.1, None)],
    [("Person", 99.0, 0.35), ("Smile", 88.0, None), ("Face", 87.3, None)],
    [("Nature", 94.2, None), ("Outdoors", 94.2, None), ("Landscape", 90.0, None), ("Sky", 89.5, None)],
]


def _mock_labels(image_bytes: bytes) -> list[dict]:
    # 같은 사진이면 항상 같은 결과가 나오도록 바이트 길이로 고정
    rnd = random.Random(len(image_bytes))
    return [{"name": n, "confidence": c, "area": a} for n, c, a in rnd.choice(_MOCK_SETS)]
