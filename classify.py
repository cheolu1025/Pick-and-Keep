"""분류 (B 담당)

1. 스크린샷 판별
   1차 규칙 점수 → 확실하면 Rekognition 호출 생략
   애매하면(1~3점) DetectText로 확인
     - 맨 위에 시계(3:50 등) → 스크린샷 (폰 상태 표시줄)
     - 글자 5줄 이상 → 스크린샷
     - 스크린샷 단서(글자 3~4줄, 문서 라벨, 단색 영역)가 있으면 → '확인 필요' (사용자가 결정)
     - 아무 단서도 없으면 → 사진
2. 종류별 그룹화 (pick_group, 정하는 순서는 아래 그룹화 부분 주석 참고)
"""
import re

import image_utils
import rekognition_utils

# ---------------- 스크린샷 판별 ----------------

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

# 폰 상태 표시줄 시계: 사진 맨 위 6% 안에 "3:50", "12:07" 같은 글자
STATUS_BAR_TOP = 0.06
CLOCK_PATTERN = re.compile(r"^\d{1,2}:\d{2}")

# '확인 필요' 단서 (자동으로 스크린샷이라 정하지 않고, 사용자에게 물어볼 때만 쓴다)
REVIEW_TEXT_LINES = 3          # 글자 3~4줄
DOCUMENT_LABELS = {"Text", "Page", "Document", "Paper", "File", "Webpage", "Menu",
                   "Screenshot", "Letter", "Diagram", "Plot", "Chart"}
REVIEW_DOC_CONF = 90
REVIEW_FLAT_RATIO = 0.50       # 실험(38장): 사진 최대 29.7%(밤 사진), 스크린샷 57.6~88.5%


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


# ---------------- 종류별 그룹화 ----------------
#
# 정하는 순서
#   1) 동물: 강아지·고양이·다른 동물 중 '신뢰도가 가장 높은' 종 (양 99.6% > 강아지 85%)
#      사진에서 일정 크기 이상일 때만 (갯벌 위 작은 새 같은 배경 동물 제외)
#   2) 사람: 얼굴이 보이거나(Face, Head) 여러 명(People)일 때만
#      (건배하는 손, 풍경 속 지나가는 사람은 제외)
#   3) 음식·음료
#   4) 종을 모르는 동물 (Animal, Pet...): 음식보다 뒤 (해산물을 Animal로 보는 경우가 있음)
#   5) 일부러 찍는 자연 장면(꽃·눈·노을 등)이 있으면 자연·풍경 (건물은 배경인 경우가 많음)
#   6) 자연·도시·실내 중 신뢰도가 가장 높은 그룹
#   7) 기타
# 동물·사람은 신뢰도 90% 이상만 인정: 진짜 주인공은 보통 95~100%,
#   눈송이를 새로 착각한 Bird 82.8% 같은 실수는 걸러진다. 음식은 85%.
#
# labels 목록은 '앞쪽일수록 구체적인 이름'. 신뢰도가 같으면 앞쪽을 기준 라벨로 보여준다.

SUBJECT_MIN_CONF = 90
ANIMAL_MIN_AREA = 0.01   # 동물이 사진의 1% 이상일 때만 주인공
FOOD_MIN_CONF = 85

ANIMAL_GROUPS = [
    {"key": "dog", "name": "강아지", "emoji": "🐶", "labels": ["Dog", "Puppy", "Canine"]},
    {"key": "cat", "name": "고양이", "emoji": "🐱", "labels": ["Cat", "Kitten"]},
    {"key": "animal", "name": "다른 동물", "emoji": "🐾",
     "labels": ["Sheep", "Bird", "Horse", "Cow", "Fish", "Rabbit", "Hamster", "Duck",
                "Insect", "Reptile", "Rodent"]},
]
# 동물 사진에 거의 항상 같이 붙는 일반 라벨: 종을 알 수 없을 때만 '다른 동물'로 쓴다
GENERIC_ANIMAL_LABELS = ["Animal", "Pet", "Mammal", "Livestock", "Wildlife"]

PERSON_GROUP = {"key": "person", "name": "사람", "emoji": "👤",
                "labels": ["Selfie", "Portrait", "People", "Person", "Face", "Head", "Baby",
                           "Child", "Man", "Woman", "Boy", "Girl", "Adult"]}
# 사람 그룹이 되려면 이 중 하나가 있어야 한다 (얼굴이 보이거나 여러 명)
PERSON_EVIDENCE = {"Face": 85, "Head": 85, "Selfie": 85, "Portrait": 85, "People": 95}

FOOD_GROUP = {"key": "food", "name": "음식", "emoji": "🍽",
              "labels": ["Fried Chicken", "Pizza", "Burger", "Noodle", "Pasta", "Sushi",
                         "Seafood", "Cake", "Sweets", "Dessert", "Bread", "Fruit", "Coffee",
                         "Beer", "Wine", "Juice", "Cocktail", "Alcohol", "Drink", "Beverage",
                         "Meal", "Dish", "Lunch", "Dinner", "Breakfast", "Food"]}

# 사람이 '이것 때문에' 찍는 자연 장면: 있으면 건물·도로보다 우선
NATURE_HIGHLIGHTS = {"labels": ["Cherry Blossom", "Flower", "Blossom", "Snow", "Winter",
                                "Autumn", "Maple", "Sunset", "Sunrise", "Mountain", "Beach",
                                "Sea", "Ocean"],
                     "min_conf": 80}

SCENE_GROUPS = [
    {"key": "nature", "name": "자연·풍경", "emoji": "🌳",
     "labels": NATURE_HIGHLIGHTS["labels"] + [
         "Lake", "River", "Forest", "Tree", "Park", "Garden", "Sky", "Field", "Grass",
         "Woodland", "Grove", "Landscape", "Scenery", "Plant", "Vegetation", "Water",
         "Land", "Nature"]},
    {"key": "city", "name": "도시·건물", "emoji": "🏙",
     "labels": ["Tower", "Skyscraper", "Bridge", "Office Building", "High Rise",
                "Cityscape", "Building", "Street", "Road", "Architecture", "Downtown",
                "Neighborhood", "Town", "Metropolis", "Urban", "City"]},
    {"key": "indoor", "name": "실내·장소", "emoji": "🏠",
     "labels": ["Cafe", "Restaurant", "Theater", "Shop", "Living Room", "Bedroom",
                "Kitchen", "Dining Room", "Cafeteria", "Room", "Furniture", "Home Decor",
                "Interior Design", "Indoors"]},
]
SCENE_MIN_CONF = 80

ETC_GROUP = {"key": "etc", "name": "기타", "emoji": "📁"}

# 화면에 보여줄 순서
_ALL_GROUPS = ANIMAL_GROUPS + [PERSON_GROUP, FOOD_GROUP] + SCENE_GROUPS + [ETC_GROUP]
GROUP_ORDER = [g["key"] for g in _ALL_GROUPS]
GROUP_INFO = {g["key"]: {"name": g["name"], "emoji": g["emoji"]} for g in _ALL_GROUPS}


def _best(names: list[str], labels: list[dict], min_conf: float,
          min_area: float | None = None) -> dict | None:
    """names에 속하는 라벨 중 신뢰도가 가장 높은 것 (같으면 names 앞쪽 = 더 구체적인 이름)"""
    found = []
    for l in labels:
        if l["name"] not in names or l["confidence"] < min_conf:
            continue
        if min_area is not None:
            # 크기 정보가 있으면 크기로, 없으면(mock 등) 신뢰도 95% 이상만 인정
            area = l.get("area")
            if (area is not None and area < min_area) or (area is None and l["confidence"] < 95):
                continue
        found.append(l)
    return max(found, key=lambda l: (l["confidence"], -names.index(l["name"])), default=None)


def pick_group(labels: list[dict]) -> tuple[str, str | None]:
    """(그룹 key, 그룹을 정한 라벨 이름) 반환"""
    # 1) 동물: 종(Dog, Cat, Sheep...)끼리 신뢰도로 비교
    candidates = []
    for order, g in enumerate(ANIMAL_GROUPS):
        b = _best(g["labels"], labels, SUBJECT_MIN_CONF, ANIMAL_MIN_AREA)
        if b:
            candidates.append((b["confidence"], -order, g["key"], b["name"]))
    if candidates:
        _, _, key, name = max(candidates)
        return key, name

    # 2) 사람: 얼굴이 보이거나 여러 명일 때만
    has_evidence = any(l["name"] in PERSON_EVIDENCE and l["confidence"] >= PERSON_EVIDENCE[l["name"]]
                       for l in labels)
    if has_evidence:
        b = _best(PERSON_GROUP["labels"], labels, SUBJECT_MIN_CONF)
        if b:
            return "person", b["name"]

    # 3) 음식·음료
    b = _best(FOOD_GROUP["labels"], labels, FOOD_MIN_CONF)
    if b:
        return "food", b["name"]

    # 4) 동물인 건 확실한데 종을 모를 때
    #    (종은 보였지만 너무 작아서 뺀 경우는 배경이므로 건너뜀: 갯벌 위 작은 새)
    species = [n for g in ANIMAL_GROUPS for n in g["labels"]]
    small_animal = any(l["name"] in species and l["confidence"] >= SUBJECT_MIN_CONF
                       and l.get("area") is not None and l["area"] < ANIMAL_MIN_AREA
                       for l in labels)
    b = None if small_animal else _best(GENERIC_ANIMAL_LABELS, labels, SUBJECT_MIN_CONF,
                                        ANIMAL_MIN_AREA)
    if b:
        return "animal", b["name"]

    # 5) 일부러 찍는 자연 장면
    b = _best(NATURE_HIGHLIGHTS["labels"], labels, NATURE_HIGHLIGHTS["min_conf"])
    if b:
        return "nature", b["name"]

    # 6) 장면 그룹 중 신뢰도가 가장 높은 것
    scenes = []
    for order, g in enumerate(SCENE_GROUPS):
        b = _best(g["labels"], labels, SCENE_MIN_CONF)
        if b:
            scenes.append((b["confidence"], -order, g["key"], b["name"]))
    if scenes:
        _, _, key, name = max(scenes)
        return key, name

    return "etc", None


# ---------------- 사진 한 장 분류 ----------------

def classify_photo(original_name: str, path: str) -> dict:
    """사진 한 장 분류. 반환 예:
    {"is_screenshot": False, "method": "rules", "score": -2, "reasons": [...],
     "labels": [{"name": "Dog", "confidence": 98.1, "area": 0.4}, ...],
     "group": "dog", "group_label": "Dog", "text_lines": None}
    """
    rekognition_utils.reset_counts()
    try:
        result = _classify(original_name, path)
    finally:
        result_counts = rekognition_utils.counts()
    result["api_calls"], result["cache_hits"] = result_counts["calls"], result_counts["hits"]
    return result


def _classify(original_name: str, path: str) -> dict:
    info = image_utils.get_image_info(path)
    score, reasons = screenshot_score(original_name, info)
    result = {"score": score, "reasons": reasons, "labels": [], "text_lines": None,
              "group": None, "group_label": None}

    if score >= SURE_SCREENSHOT:
        # 스크린샷 확정 → Rekognition 호출 생략 (비용 절약)
        result.update(is_screenshot=True, method="rules")
        return result

    image_bytes = image_utils.prepare_for_rekognition(path)

    lines = []
    if score > SURE_PHOTO:
        # 애매한 경우에만 DetectText로 한 번 더 확인
        lines = rekognition_utils.detect_text_lines(image_bytes)
        result["text_lines"] = len(lines)
        clock = next((l["text"] for l in lines
                      if l["top"] <= STATUS_BAR_TOP and CLOCK_PATTERN.match(l["text"])), None)
        if clock:
            reasons.append(f"맨 위 시계 '{clock}'")
            result.update(is_screenshot=True, method="status_bar")
            return result
        if len(lines) >= TEXT_LINES_THRESHOLD:
            result.update(is_screenshot=True, method="detect_text")
            return result
        result["method"] = "detect_text"
    else:
        result["method"] = "rules"

    result["is_screenshot"] = False
    result["labels"] = rekognition_utils.analyze_image(image_bytes)
    result["group"], result["group_label"] = pick_group(result["labels"])

    if score > SURE_PHOTO:
        hints = review_hints(len(lines), result["labels"], path)
        if hints:
            result["review"] = True        # 화면에서 '확인 필요' 칸에 보여줌
            result["review_reasons"] = hints
    return result


def review_hints(text_lines: int, labels: list[dict], path: str) -> list[str]:
    """애매한 사진이 스크린샷일 수도 있는 단서들. 하나라도 있으면 사용자에게 확인받는다."""
    hints = []
    if text_lines >= REVIEW_TEXT_LINES:
        hints.append(f"글자 {text_lines}줄")
    doc = max((l for l in labels if l["name"] in DOCUMENT_LABELS
               and l["confidence"] >= REVIEW_DOC_CONF),
              key=lambda l: l["confidence"], default=None)
    if doc:
        hints.append(f"문서 라벨 {doc['name']} {doc['confidence']}%")
    flat = image_utils.flat_color_ratio(path)
    if flat >= REVIEW_FLAT_RATIO:
        hints.append(f"단색 영역 {flat:.0%}")
    return hints


def group_photos(results: list[dict]) -> list[dict]:
    """결과 화면용: 사진(스크린샷·확인 필요 제외)을 그룹별로 묶어서 화면 순서대로 반환
    [{"key": "dog", "name": "강아지", "emoji": "🐶", "photos": [...]}, ...]
    """
    buckets = {key: [] for key in GROUP_ORDER}
    for r in results:
        if not r.get("is_screenshot") and not r.get("review"):
            buckets[r.get("group") or "etc"].append(r)
    return [{"key": k, **GROUP_INFO[k], "photos": buckets[k]}
            for k in GROUP_ORDER if buckets[k]]
