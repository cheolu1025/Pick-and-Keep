"""Rekognition 호출 (B 담당)

- analyze_image: DetectLabels (라벨 + 신뢰도)
- detect_text:   DetectText (이미지 속 글자 줄 목록, 스크린샷 판별 보조)

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


def analyze_image(image_bytes: bytes, max_labels: int = 10,
                  min_confidence: float = 70) -> list[dict]:
    """[{"name": "Food", "confidence": 97.2}, ...] 반환 (신뢰도 높은 순)"""
    if USE_MOCK:
        return _mock_labels(image_bytes)

    res = _rekognition().detect_labels(
        Image={"Bytes": image_bytes},
        MaxLabels=max_labels,
        MinConfidence=min_confidence,
    )
    return [{"name": l["Name"], "confidence": round(l["Confidence"], 1)}
            for l in res["Labels"]]


def detect_text(image_bytes: bytes, min_confidence: float = 80) -> list[str]:
    """이미지 속 글자 줄 목록. 한글은 잘 못 읽으므로 내용보다 '줄 수'를 판별에 쓴다."""
    if USE_MOCK:
        return ["10:24", "Settings", "Wi-Fi"] if len(image_bytes) % 2 else []

    res = _rekognition().detect_text(Image={"Bytes": image_bytes})
    return [t["DetectedText"] for t in res["TextDetections"]
            if t["Type"] == "LINE" and t["Confidence"] >= min_confidence]


# ---------- mock ----------
_MOCK_SETS = [
    [("Dog", 98.1), ("Pet", 97.5), ("Animal", 97.5), ("Mammal", 97.5)],
    [("Cat", 96.3), ("Pet", 96.3), ("Animal", 96.3)],
    [("Food", 95.2), ("Meal", 90.4), ("Dish", 88.7)],
    [("Flower", 93.0), ("Plant", 93.0), ("Blossom", 85.1)],
    [("Person", 99.0), ("Smile", 88.0), ("Face", 87.3)],
    [("Nature", 94.2), ("Outdoors", 94.2), ("Landscape", 90.0), ("Sky", 89.5)],
]


def _mock_labels(image_bytes: bytes) -> list[dict]:
    # 같은 사진이면 항상 같은 결과가 나오도록 바이트 길이로 고정
    rnd = random.Random(len(image_bytes))
    return [{"name": n, "confidence": c} for n, c in rnd.choice(_MOCK_SETS)]
