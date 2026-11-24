# Pick & Keep 팀 작업 가이드 (v2.3, 11/24 업데이트)

> 박철우(A: 인프라·저장 담당), 유채민(B: AI·분류·화면 담당)이 각자 무엇을 하고, 무엇을 함께 맞춰야 하는지 정리한 문서입니다.
> 저장소에 `docs/` 폴더를 만들어 이 파일을 넣어두고 같이 보면 좋습니다.

---

## 0-1. 🆕 v2.1~v2.3 변경 사항 (11/22~11/24)

| 구분 | 내용 | 영향 |
|---|---|---|
| SQS 방식 변경 | 분류 결과·진행 상태를 DB가 아니라 B의 `tmp/<batch_id>/<photo_id>.result.json`에 기록. **`update_classification`, `get_status`는 만들지 않음**. `status` 열은 남기되 항상 `done` | A: 워커 systemd 등록 + 실제 큐 생성만 |
| 중복 방지 | `photos.file_hash` 열 + 중복 방지 인덱스, `is_duplicate()` 추가, `save_photo_record(..., file_hash=None)` 선택 인자. 해시는 **HEIC 변환 전 원본** SHA-256 | B: 저장 전에 `is_duplicate` 확인 |
| 열람 기록 | `get_photo_url(key, mark_viewed=False)` 추가. 목록·썸네일은 `False`, 사진을 눌러 크게 볼 때만 기본값(`True`) | B: 크게 보기 화면 필요 |
| HEIC 저장 | HEIC는 변환한 JPG(원래 해상도)를 S3에 업로드. `size`는 **원본 크기** | 없음 |
| 임시 파일 | `tmp/<batch_id>/`에 두고 `result.json`으로 전달 (원본 이름, 경로, 원본 크기, 해시, 라벨, 그룹) | 없음 |
| 보류함 함수 확정 | `delete_photo_record`, `move_photo` 이름 확정, 구현 완료 | - |

| 🆕 v2.2 시연 모드 | 보류 기간을 `.env`로 설정. `get_old_hold_photos()`를 인자 없이 부르면 설정을 따름, `hold_period_label()` 추가. 알림 메일은 사진당 한 번만 (`notified_at` 열) | B: 배너에서 인자 없이 호출, 문구에 `hold_period_label()` 사용 |
| 🆕 v2.2 사진 번호로 찾기 | `get_photo(photo_id)` 추가. 크게 보기·보류함 버튼은 `s3_key` 대신 `id`를 주고받음 (이동해도 id는 그대로) | B: `/photo/<id>` 형태로 사용 |
| 🆕 v2.3 그룹 저장 | `photos.category` 열 추가, `save_photo_record(..., category=None)` 선택 인자, `update_category(photo_id, category)` 추가. 값은 그룹 **key**(`"food"`, `"screenshot"` 등), 화면 이름은 B의 `classify.GROUP_INFO` | B: 저장 때 `category=r["final_group"]`, 보관함에서 그룹 변경 시 `update_category` |
| 🆕 v2.3 라벨 형식 | `labels` 항목에 `area`(사진에서 물체가 차지하는 비율)도 포함 가능. JSON으로 그대로 저장되므로 A 쪽 변경 없음 | 없음 |

> 변경 기록과 이유는 `docs/PickAndKeep_기능정리_발표노트.md` 2장(결정 기록)에도 남긴다.

---

## 0. v2 변경 사항 (v1 대비)

> 🆕 표시가 붙은 부분이 이번에 추가·수정된 내용입니다.

| 구분 | 내용 | A에게 영향 |
|---|---|---|
| B 기능 추가 | Rekognition **DetectText**로 스크린샷 판별 보강 | 없음 |
| B 기능 추가 | Rekognition **중복 호출 방지**(해시 캐시) | 없음 (B 자체 캐시 사용) |
| B 기능 추가 | Rekognition **병렬 호출**로 처리 속도 개선 | 없음 |
| B 기능 추가 | **SQS 비동기 처리** (필수 기능 완성 후, 11/17 합의 완료) | DB `status` 열, 함수 2개, systemd 서비스 1개, WAL 모드 |
| 확인 완료 | Learner Lab에서 **SQS 큐 생성·메시지 송수신 동작 확인** (11/12) | - |
| 환경 | **Windows 사용자용 명령어** 추가 (5장) | - |

### 🆕 A(박철우)가 확인할 것 (v2 기준)

SQS 비동기 처리 도입이 확정되면서(11/17) A 쪽에 추가된 작업이다. 나머지 추가 기능(DetectText, 해시 캐시, 병렬 호출)은 B 파일 안에서만 처리하므로 A에게 영향이 없다.

**지금 (1~2주차, DB 만들 때)**
- [x] `photos` 테이블에 `status` 열을 처음부터 포함 (v2.1: 항상 `done`, SQS는 사용 안 함)
- [x] DB 연결 시 WAL 모드 켜기: `PRAGMA journal_mode=WAL;`
  → gunicorn 여러 프로세스가 같은 DB 파일에 동시에 쓸 때 잠금 오류 감소

**나중 (5~6주차)**
- [x] ~~`db.py`에 `update_classification`, `get_status` 추가~~ → v2.1에서 취소 (B의 `result.json` 방식)
- [ ] A 계정에 실제 SQS 큐(표준) 생성 → URL을 EC2의 `.env` `SQS_QUEUE_URL`에 입력
  (EC2가 A 계정에 있어서 B 계정의 큐에는 접근할 수 없음)
- [ ] `worker.py`용 systemd 서비스 등록 (Flask 서비스와 같은 방식, 재부팅 시 자동 실행)
- [ ] gunicorn 실행 옵션에 `--timeout 120` 추가 (스위치를 끈 동기 방식에서 사진이 많을 때 502 방지)

**`.env`에 추가된 항목 (4-4 참고)**
```
USE_SQS=false
SQS_QUEUE_URL=
```

**B가 하는 것 (A는 하지 않아도 됨)**
- 테스트용 큐 생성, 메시지 보내기, `worker.py` 코드, 처리 중 화면, `/status` 라우트
- SQS는 필수 기능(업로드 → 분류 → 저장 → 조회)이 동기 방식으로 완성된 뒤 5주차에 연결

---

## 1. 우리가 만드는 것

**Pick & Keep** — AI 분류 기반 사용자 중심 선택적 사진 클라우드 백업 서비스
(2026-2 클라우드컴퓨팅 기말 프로젝트, 기말시험 대체, 6주)

### 한 줄 설명
사진을 전부 백업하는 기존 서비스와 달리, AI(Rekognition)가 사진을 종류별로 묶어 보여주고 **사용자가 보관 위치를 직접 골라** 필요한 사진만 S3에 보관하는 웹 서비스.

### 핵심 기능
| 기능 | 저장 위치 | 동작 |
|---|---|---|
| 평생 보관함 | S3 `keep/` | 영구 보관. 90일 후 라이프사이클 규칙으로 Glacier IR 자동 전환 |
| 보류함 | S3 `hold/` | 임시 보관. 60일 동안 안 열어보면 삭제 여부 확인 (화면 배너 + SNS 이메일) |
| 확보 용량 | (계산) | 두 보관함에 저장한 사진 크기 합계 = 폰에서 지우면 확보되는 용량 |

- 웹 서비스라 폰 사진첩을 직접 지울 수는 없음. 삭제는 사용자가 직접 하고, 서비스는 용량 계산·표시만 담당
- 🆕 스크린샷은 규칙으로 먼저 걸러서 **확실한 경우 Rekognition을 호출하지 않음** (비용 절약). 규칙만으로 애매한 사진만 DetectText로 한 번 더 확인 (7장 ③ 참고)

### 사용자 흐름
1. 폰/PC 브라우저에서 사진 여러 장 업로드 (JPG, PNG, HEIC)
2. 서버에서 HEIC → JPEG 변환, 5MB 이하로 리사이즈
3. 스크린샷 규칙 검사 + Rekognition DetectLabels로 분류 (🆕 여러 장을 병렬로 호출)
4. 강아지, 고양이, 음식처럼 **종류별 그룹**으로 결과 표시
5. 사용자가 그룹 단위로 keep / hold / 저장 안 함 선택 (그룹 안에서 개별 사진 이동 가능)
6. 선택한 사진을 S3에 저장, SQLite에 기록
7. 보관함 조회 시 Presigned URL(임시 링크)로 사진 표시, 열람 시각 갱신

### 사용하는 기술
| 구분 | 내용 |
|---|---|
| 서버 | EC2, Ubuntu 24.04, t3.small 권장 |
| 웹 | Python, Flask, gunicorn + nginx, systemd |
| DB | SQLite (EC2 안의 파일) |
| 저장소 | S3 (퍼블릭 액세스 차단) |
| AI | Rekognition DetectLabels, 🆕 DetectText |
| 알림 | SNS 이메일 |
| 🆕 비동기 처리 | SQS |
| 권한 | IAM 역할 `LabInstanceProfile` (액세스 키 없이 접근) |
| 리전 | us-east-1 |

---

## 2. 현재 상태

- [x] GitHub 저장소 생성 (Public), 팀원 초대
- [x] 프로젝트 기본 구조 push (빈 파일, `requirements.txt`, `.env.example`, `.gitignore`)
- [x] 🆕 Learner Lab에서 SQS 사용 가능 확인 (큐 생성, 메시지 보내기·받기·삭제)
- [x] 🆕 SQS 비동기 처리 도입 합의 (11/17)
- [ ] 이하 1단계부터 진행

---

## 3. 역할 한눈에 보기

| | A: 박철우 (인프라·저장) | B: 유채민 (AI·분류·화면) |
|---|---|---|
| 담당 범위 | AWS 리소스, 서버, 사진 저장과 기록 | 사용자가 올리고, 고르고, 보는 흐름 전체 |
| AWS | EC2, S3, 라이프사이클, SNS, IAM | Rekognition (Labels + Text), 🆕 SQS |
| 담당 파일 | `aws_utils.py`, `db.py`, `check_hold.py`, `deploy/` | `rekognition_utils.py`, `classify.py`, `image_utils.py`, `app.py`(라우트), `templates/`, `static/`, 🆕 `worker.py` |
| 브랜치 | `feature/a-infra` | `feature/b-ui` |
| 발표 | EC2, S3, IAM, SNS | Rekognition, 분류, 사용자 화면, 🆕 성능·비용 개선 |

**원칙: 자기 담당 파일만 고친다.** 상대 파일을 고쳐야 하면 먼저 말하고, 상대가 고치게 한다.

---

## 4. 함께 맞춰야 하는 것 (가장 중요)

### 4-1. AWS 계정은 서로 다르다
Learner Lab은 **학생마다 별도의 AWS 계정**이다. A가 만든 버킷과 EC2는 B 계정에서 보이지 않는다.

| 용도 | 어느 계정 |
|---|---|
| 최종 서비스 (EC2, 실제 버킷, SNS, 🆕 SQS 큐) | **A 계정** |
| B의 Rekognition 테스트 | B 계정 (버킷 필요 없음) |
| B가 저장 기능까지 테스트할 때 | B 계정에 테스트용 버킷을 따로 만들고 B의 `.env`에 입력 |
| 🆕 B의 SQS 테스트 | B 계정에 테스트용 큐를 만들고 B의 `.env`에 입력 |

→ 그래서 **코드에 버킷 이름, 주제 ARN, 큐 URL 등을 직접 쓰지 않고 항상 `.env`에서 읽는다.**

### 4-2. 함수 약속 (인터페이스)
B는 A가 만든 함수를 호출해 화면에 연결한다. **이름과 입력·출력은 바꾸지 않는다.** 바꿔야 하면 먼저 상대에게 공유한다.

```python
# ===== A 담당: aws_utils.py =====
def upload_photo(file_path: str, folder: str) -> str:
    """S3에 업로드하고 S3 키를 반환. folder: "keep" | "hold" """

def get_photo_url(s3_key: str, expires: int = 3600, *,
                  mark_viewed: bool = True) -> str:
    """Presigned URL 반환. mark_viewed=True면 DB의 last_viewed도 갱신
    🆕 v2.1: 목록·썸네일은 mark_viewed=False, 사진을 눌러 크게 볼 때만 기본값"""

def delete_photo(s3_key: str) -> None:
    """S3 객체 삭제"""


# ===== A 담당: db.py =====
def init_db() -> None:
    """테이블 생성"""

def save_photo_record(filename: str, s3_key: str, folder: str,
                      labels: list[dict], size: int,
                      file_hash: str | None = None,
                      category: str | None = None) -> int:
    """사진 정보 저장 후 id 반환
    🆕 v2.1: file_hash(원본 SHA-256) 선택 인자. 같은 해시가 있으면 sqlite3.IntegrityError
    🆕 v2.3: category(사용자가 고른 최종 그룹 key, 예: "food") 선택 인자. 안 넘기면 None
    filename=원본 이름, size=원본 크기"""

def is_duplicate(file_hash: str) -> dict | None:
    """🆕 v2.1: 같은 해시의 사진이 있으면 그 사진 정보, 없으면 None"""

def get_photos(folder: str) -> list[dict]:
    """해당 보관함의 사진 목록"""

def get_photo(photo_id: int) -> dict | None:
    """🆕 v2.2: 사진 하나를 id로 찾기, 없으면 None
    화면끼리는 s3_key 대신 id를 주고받음 (이동하면 s3_key는 바뀌지만 id는 그대로)"""

def update_category(photo_id: int, category: str | None) -> bool:
    """🆕 v2.3: 저장된 사진의 그룹 바꾸기 (보관함 화면용). 없는 id면 False
    id로 찾으므로 평생 보관함으로 옮긴 사진도 그대로 동작. 열람 기록은 바뀌지 않음"""

def update_last_viewed(s3_key: str) -> None:
    """열람 시각 갱신"""

def get_saved_size() -> int:
    """저장된 사진 크기 합계(바이트) = 확보 가능 용량"""

def get_old_hold_photos(days: float | None = None) -> list[dict]:
    """hold 폴더 중 보류 기간이 지난 사진
    🆕 v2.2: 인자 없이 부르면 .env 설정(HOLD_DAYS, 시연 모드면 HOLD_DEMO_MINUTES)을 따름
    숫자를 넘기면 예전처럼 동작 (예: get_old_hold_photos(60))"""

def hold_period_label() -> str:
    """🆕 v2.2: 화면·메일에 쓸 보류 기간 글자. 평소 '60일', 시연 모드 '2분'"""


# ===== B 담당: rekognition_utils.py =====
def analyze_image(image_bytes: bytes, max_labels: int = 10,
                  min_confidence: float = 70) -> list[dict]:
    """[{"name": "Food", "confidence": 97.2}, ...] 반환
    🆕 v2.3: 항목에 "area"(물체가 차지하는 비율)가 추가될 수 있음. DB에는 JSON 그대로 저장"""

# 🆕 B 담당 (B 내부에서만 사용, A 영향 없음)
def detect_text(image_bytes: bytes) -> list[str]:
    """이미지 속 글자 목록 반환. 스크린샷 판별 보조용"""

def analyze_images_parallel(images: list[bytes], max_workers: int = 5) -> list[list[dict]]:
    """여러 장을 병렬로 analyze_image 호출. 결과 순서는 입력 순서와 같음"""
```

#### ✅ 보류함 함수 (v2.1 확정, 구현 완료)
보류함 알림에서 사용자가 고를 수 있는 선택지(삭제 / 60일 연장 / 평생 보관함으로 이동)를 처리하는 함수. A가 만들고 B가 화면에서 호출한다.

```python
# A 담당 (확정)
def delete_photo_record(s3_key: str) -> None:
    """DB에서 사진 기록 삭제 (delete_photo와 함께 호출)"""

def move_photo(s3_key: str, to_folder: str) -> str:
    """hold → keep 이동. S3 복사 후 원본 삭제, DB의 folder와 s3_key 갱신, 새 키 반환"""

# '60일 연장'은 update_last_viewed(s3_key)로 처리 (열람 시각을 지금으로 갱신)
```

#### 🆕 SQS 비동기 처리 (v2.1에서 방식 변경)
SQS는 **업로드 → 분류** 사이에만 들어간다. A의 S3 저장, Presigned URL, 라이프사이클, 60일 검사에는 영향이 없다.
분류 시점에는 아직 DB 기록(photo_id)이 없으므로, **분류 결과와 진행 상태는 DB가 아니라 B의 `tmp/<batch_id>/<photo_id>.result.json`에 기록**한다. 워커가 이 파일에 쓰고 `/status`는 이 파일을 읽는다.

| 맞닿는 곳 | 왜 필요한지 | A가 할 것 |
|---|---|---|
| EC2 실행 | 워커가 Flask와 별도로 계속 돌아야 함 | 워커용 systemd 서비스 파일 1개 (Flask와 같은 방식) |
| 실제 큐 | EC2가 A 계정에 있어 A 계정 큐만 접근 가능 | A 계정에 표준 큐 생성, URL을 EC2 `.env`에 입력 |

- **B가 담당**: 큐 생성(테스트용), 메시지 보내기, 워커 코드(`worker.py`), `result.json` 기록, 처리 중 화면
- **B → A 전달할 것**: 워커 실행 명령, 워커가 필요로 하는 `.env` 항목
- **권장**: `result.json`은 임시 파일에 먼저 쓰고 `os.replace`로 바꿔치기 (`/status`가 쓰다 만 파일을 읽지 않게)
- **안전장치**: `.env`의 `USE_SQS=false`면 기존 동기 방식 그대로 동작. 시연 직전 문제가 생겨도 바로 되돌릴 수 있음
- **도입 시점**: 업로드 → 분류 → 저장 → 조회가 동기 방식으로 완성된 뒤 (5주차)
- `photos.status` 열은 남아 있지만 v2.1부터 사용하지 않음 (항상 `done`)

### 4-3. 데이터 규칙

**SQLite 테이블** (A가 만들고, B는 함수로만 접근)
```sql
CREATE TABLE IF NOT EXISTS photos (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    filename     TEXT NOT NULL,                -- 원본 파일명 (한글 가능)
    s3_key       TEXT NOT NULL UNIQUE,
    folder       TEXT NOT NULL CHECK (folder IN ('keep', 'hold')),
    labels       TEXT,                         -- JSON 문자열
    size         INTEGER NOT NULL,             -- 바이트
    uploaded_at  TEXT NOT NULL,                -- ISO 8601
    last_viewed  TEXT NOT NULL,                -- ISO 8601, 업로드 시 uploaded_at과 동일
    status       TEXT NOT NULL DEFAULT 'done'  -- v2.1: 사용 안 함, 항상 done
        CHECK (status IN ('pending', 'done', 'failed')),
    file_hash    TEXT,                         -- 🆕 v2.1: 원본 SHA-256, 중복 방지 (NULL 허용)
    notified_at  TEXT,                         -- 🆕 v2.2: 보류함 알림 메일 보낸 시각 (A 내부용)
    category     TEXT                          -- 🆕 v2.3: 그룹 key (예: 'food'), NULL 허용
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_photos_file_hash ON photos (file_hash);
```
- 시각은 전부 UTC ISO 8601 문자열 (예: `2026-11-22T09:00:00+00:00`)
- 새로 추가된 열(`file_hash`, `notified_at`, `category`)은 `init_db()`가 기존 `photos.db`에 자동으로 붙임 → DB 파일을 지울 필요 없음

**S3 키 규칙**
```
keep/2026/11/<UUID>_<영문파일명>.jpg
hold/2026/11/<UUID>_<영문파일명>.jpg
```
- 한글 파일명은 URL에서 인코딩되어 다루기 어려우므로, S3 키에는 UUID 기반 영문 이름을 쓰고 원본 이름은 DB `filename`에 저장

**Flask 라우트** (B가 작성)
| 라우트 | 메서드 | 설명 | 호출하는 A 함수 |
|---|---|---|---|
| `/` | GET | 메인 화면, 보류함 안내 배너 | `get_old_hold_photos` |
| `/upload` | POST | 사진 업로드, 변환 | - |
| `/classify` | GET | 분류 결과 화면 (종류별 그룹) | - |
| `/save` | POST | 선택한 사진을 keep/hold에 저장 | `is_duplicate`, `upload_photo`, `save_photo_record(..., category=)` |
| `/keep` | GET | 평생 보관함 조회 | `get_photos`, `get_photo_url(key, mark_viewed=False)` |
| `/hold` | GET | 보류함 조회 | `get_photos`, `get_photo_url(key, mark_viewed=False)` |
| 🆕 `/photo/<id>` (이름은 B가 결정) | GET | 사진 크게 보기 = "열람" | `get_photo(id)` → `get_photo_url(key)` |
| `/stats` | GET | 확보 용량 표시 | `get_saved_size` |
| 🆕 `/status` | GET | 처리 중 화면이 분류 진행 상태 확인 (SQS) | - (B의 `result.json` 읽음) |

#### ✅ 합의 완료 (v2.1)
- **임시 파일 위치**: `tmp/<batch_id>/` (`.gitignore`에 포함됨). 저장 후 삭제는 B가 담당
- **분류 결과 전달**: 사진마다 `result.json` (원본 이름, 파일 경로, 원본 크기, 해시, 라벨, 그룹)
- **S3 업로드 파일**: HEIC는 변환한 JPG(원래 해상도, Rekognition용 축소본 아님)를 `upload_photo`에 넘김. `save_photo_record`의 `size`는 원본 크기
- 🆕 (11/24 B 확인) 일반 사진은 원본 그대로, HEIC만 원래 해상도 JPG로 변환해 업로드. 5MB 축소본은 Rekognition 호출 때 메모리에서만 사용. B 테스트 버킷: `pick-and-keep-chaemin-test`
- 🆕 **그룹 저장 (v2.3)**: 분류 화면에서 바꾼 최종 그룹(`final_group`)을 `category`로 저장

#### ⚠️ 추가로 합의할 것
- 🆕 **gunicorn 타임아웃**: 동기 방식에서 사진을 많이 올리면 gunicorn 기본 타임아웃(30초)을 넘겨 502 에러가 날 수 있음. A가 배포 설정할 때 `--timeout 120` 정도로 늘려두기 (SQS가 켜지면 이 문제 자체가 줄어들지만, 스위치를 끈 경우를 대비해 유지)

### 4-4. `.env` 설정 항목
각자 `.env.example`을 복사해서 `.env`를 만들고 본인 값으로 채운다. **`.env`는 절대 GitHub에 올리지 않는다.**
```
AWS_REGION=us-east-1
S3_BUCKET_NAME=      # A: 실제 버킷 / B: 본인 테스트 버킷
SNS_TOPIC_ARN=       # A만 사용
DB_PATH=photos.db
USE_SQS=false        # 🆕 SQS 사용 여부 (SQS 완성 전까지 false)
SQS_QUEUE_URL=       # 🆕 A: 실제 큐 / B: 본인 테스트 큐
HOLD_DAYS=60         # 🆕 v2.2: 보류 기간(일). 없으면 60
HOLD_DEMO_MINUTES=   # 🆕 v2.2: 시연 모드. 값(예: 2)이 있으면 보류 기간이 그 분(分)으로 바뀜. 평소엔 비워 둠
SITE_URL=            # 🆕 v2.2: 알림 메일 속 링크 (비우면 EC2가 자기 현재 주소를 자동으로 넣음)
```
- `.env`를 바꾸면 Flask를 재시작해야 반영됨 (서버에서는 A가 처리)

### 4-5. Git 협업 규칙
- 각자 브랜치에서 작업 → push → GitHub에서 Pull Request로 `main`에 병합
- 작업 시작 전 항상 최신 `main`을 받아서 내 브랜치에 합치기
  ```bash
  git checkout main
  git pull
  git checkout 내브랜치
  git merge main
  ```
- 커밋 메시지는 무엇을 했는지 한 줄로 (예: `S3 업로드 함수 작성`)
- `app.py`는 B가 관리. A가 고칠 일이 있으면 B에게 요청
- **절대 올리면 안 되는 것**: `.env`, `*.pem`(EC2 키), AWS 자격 증명. 저장소가 Public이므로 특히 주의

---

## 5. 공통 1단계: 개발 환경 만들기 (A, B 각자)

> 🆕 Mac과 Windows 명령어가 다르므로 본인 OS에 맞는 쪽을 사용한다.

### 5-1. 가상환경과 라이브러리 설치
VS Code에서 프로젝트 폴더를 열고 터미널(`Control + ``)에서:

**Mac**
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

**🆕 Windows (PowerShell)**
```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```
- 실행 정책 오류(`스크립트를 실행할 수 없으므로...`)가 나면 한 번만 실행:
  `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`
- VS Code에서 `Ctrl+Shift+P` → **Python: Select Interpreter** → `venv` 선택해두면 새 터미널에서 자동 활성화됨

- 터미널 앞에 `(venv)`가 보이면 성공
- VS Code나 터미널을 새로 열 때마다 활성화 명령 다시 입력

### 5-2. `.env` 만들기
```bash
cp .env.example .env          # Mac
copy .env.example .env        # 🆕 Windows
```

### 5-3. 내 컴퓨터에서 AWS 쓰게 하기 (Learner Lab 임시 자격 증명)
1. Learner Lab → **Start Lab** → 녹색불 확인
2. **AWS Details** → AWS CLI 옆 **Show** → 내용 전체 복사
3. 자격 증명 파일 열기

   **Mac**
   ```bash
   mkdir -p ~/.aws
   touch ~/.aws/credentials
   open -e ~/.aws/credentials
   ```
   **🆕 Windows (PowerShell)**
   ```powershell
   mkdir $HOME\.aws -Force
   notepad $HOME\.aws\credentials
   ```
   (메모장이 "새로 만들까요?" 물으면 **예**. 저장 시 파일 이름 뒤에 `.txt`가 붙지 않게 주의)
4. 열린 편집기에 붙여넣고 저장
5. 리전 설정

   **Mac**
   ```bash
   printf "[default]\nregion = us-east-1\n" > ~/.aws/config
   ```
   **🆕 Windows (PowerShell)**
   ```powershell
   Set-Content $HOME\.aws\config "[default]`nregion = us-east-1"
   ```
- ⚠️ 랩 세션을 새로 시작할 때마다 자격 증명이 바뀐다. 권한 오류(`ExpiredToken` 등)가 나면 2~4번 다시
- 이 파일은 프로젝트 폴더 밖에 있어 GitHub에 올라가지 않는다

### 5-4. 내 브랜치 만들기
```bash
git checkout -b feature/a-infra   # A
git checkout -b feature/b-ui      # B
```

---

## 6. A(박철우)가 할 일

### 1~2주차
**① S3 버킷 만들기**
1. S3 → 버킷 만들기
2. 이름 예: `pick-and-keep-cheolu` (전 세계 유일)
3. 리전 us-east-1, **모든 퍼블릭 액세스 차단 유지**
4. 버킷 안에 `keep`, `hold` 폴더 생성
5. `.env`의 `S3_BUCKET_NAME`에 입력

**② `db.py` 작성**
- `init_db`, `save_photo_record`, `get_photos`, `update_last_viewed`, `get_saved_size`, `get_old_hold_photos`
- 🆕 테이블에 `status` 열 포함 (4-3 참고), 연결 시 WAL 모드 켜두기
- 테스트: 가짜 데이터 넣고 조회, 삭제 (AWS 없이 가능)

**③ `aws_utils.py` 뼈대 작성**
- `upload_photo`, `get_photo_url`, `delete_photo`
- 테스트: 사진 한 장 hold에 업로드 → S3 콘솔 확인 → Presigned URL을 브라우저에서 열기 → 삭제

**④ B를 위한 임시 버전 먼저 공유 (권장)**
- 실제 기능이 완성되기 전이라도, 함수 이름과 반환 형태만 맞춘 **임시 버전**(가짜 값을 돌려주는 함수)을 먼저 `main`에 올려두면 B가 기다리지 않고 화면 작업을 할 수 있다

**⑤ EC2 세팅**
1. EC2 → 인스턴스 시작
   - 이름: `pick-and-keep-server`
   - AMI: Ubuntu 24.04 LTS
   - 유형: t3.small (막혀 있으면 t3.micro)
   - 키 페어: 새로 만들고 `.pem`은 **프로젝트 폴더 밖** 보관 (예: `~/.ssh/`)
   - 보안 그룹: SSH(22) 내 IP만, HTTP(80) 전체 허용
   - 스토리지: 16~20GB
   - **고급 세부 정보 → IAM 인스턴스 프로파일: `LabInstanceProfile`**
2. SSH 접속 → `git clone` → venv → `pip install` → `.env`(버킷 이름만, 키 불필요)
3. Flask 임시 실행 후 브라우저에서 접속 확인

### 3~4주차
- `upload_photo`, `get_photo_url`(+ `last_viewed` 갱신), `delete_photo` 실제 기능 완성
- `get_saved_size`로 확보 용량 계산 완성
- 추가 합의 함수(`delete_photo_record`, `move_photo`) 작성
- B의 화면과 연결해 업로드 → 저장 → 조회 흐름을 내 컴퓨터와 EC2 양쪽에서 확인

### 5~6주차
- **라이프사이클 규칙**: S3 버킷 → 관리 → 수명 주기 규칙 생성 → 접두사 `keep/` → 90일 후 Glacier Instant Retrieval (시연 확인용 1일 규칙은 따로 테스트)
- **SNS**: 주제 생성 → 이메일 구독 → 메일에서 Confirm subscription → 주제 ARN을 `.env`에 입력
- **`check_hold.py`**: 60일 미열람 hold 사진을 찾아 SNS로 메일 발송 + cron 등록
  - Learner Lab 세션이 꺼지면 cron이 안 돈다 → 시연은 스크립트 직접 실행, DB의 `last_viewed`를 과거 날짜로 바꿔서 시연
- **배포 자동화**: gunicorn + nginx + systemd 설정, `deploy/` 폴더에 사용자 데이터 스크립트로 정리
  - 🆕 gunicorn `--timeout 120` 설정
- 🆕 **SQS 지원 (v2.1):** 실제 큐 생성 후 URL을 `.env`에 입력, `worker.py`용 systemd 서비스 등록 (DB 함수는 필요 없음)

---

## 7. B(유채민)가 할 일

### 1~2주차
**① 이미지 처리 (`image_utils.py`)**
- 업로드된 파일 받기, HEIC → JPEG 변환 (`pillow-heif`)
- Rekognition 전송용으로 5MB 이하 리사이즈 (원본은 그대로 보관)

**② Rekognition 호출 (`rekognition_utils.py`)**
- `analyze_image(image_bytes, ...)` → `[{"name": "Dog", "confidence": 97.2}, ...]`
- 본인 Learner Lab 계정 자격 증명으로 테스트 (버킷 필요 없음)
- 랩이 막혀 있을 때를 대비해 가짜 응답(mock) 스위치를 두고 개발

**③ 스크린샷 판별 규칙 (`classify.py`)** 🆕 DetectText 보강
- 1차: 규칙 검사 — EXIF(카메라 정보) 없음, PNG 형식, 폰 화면 해상도와 일치, 파일명에 Screenshot/스크린샷 포함 등을 점수로 조합
  - 점수가 높으면 → 스크린샷 확정, **Rekognition 호출 생략** (비용 절약)
  - 점수가 낮으면 → 일반 사진, DetectLabels로 분류
- 🆕 2차: **애매한 경우에만** `detect_text`로 글자 양 확인 → 글자가 많으면 스크린샷으로 판단
- 🆕 측정: 테스트 사진 묶음(일반 사진, 스크린샷, 음식, 문서 등)과 정답표를 만들어 **규칙만 vs 규칙 + DetectText 정확도 비교** 기록

**④ 첫 화면**
- `/` 메인(업로드 버튼), `/upload`
- 1~2주차 공동 목표: **사진을 올리면 라벨이 화면에 표시**

### 3~4주차
**⑤ 종류별 그룹화 (`classify.py`)**
- Rekognition은 사진 하나에 라벨을 여러 개 준다 (예: Dog, Pet, Animal, Mammal)
- 카테고리 목록(강아지, 고양이, 꽃, 나무, 음식, 사람, 풍경 등)을 미리 정하고, 그중 신뢰도가 가장 높은 하나로 그룹화. 목록에 없으면 "기타"
- 두 카테고리에 걸치는 사진은 신뢰도 높은 쪽으로

**⑥ 화면**
- `/classify`: 그룹별 결과, 그룹 단위로 keep / hold / 저장 안 함 선택, 그룹 안 개별 사진 이동
- `/save`: A의 `upload_photo`, `save_photo_record` 호출
- `/keep`, `/hold`: A의 `get_photos`, `get_photo_url`로 사진 표시
- `/stats`: A의 `get_saved_size`로 확보 용량 표시

**🆕 ⑦ Rekognition 비용 최적화 (3주차)**
- 사진 파일의 해시(SHA-256)를 키로 분류 결과를 캐시해서, **같은 사진을 다시 올리면 Rekognition을 다시 호출하지 않음**
- 캐시는 B 쪽에서 관리 (예: `tmp/label_cache.json`) → A의 DB에는 영향 없음
- 측정: 같은 테스트 묶음을 두 번 올렸을 때 **호출 횟수 감소율** 기록

**🆕 ⑧ 병렬 처리 (4주차)**
- `ThreadPoolExecutor`로 여러 장을 동시에 Rekognition 호출 (`analyze_images_parallel`)
- 동시 실행 수는 5개로 시작. `ThrottlingException`이 나면 줄이기 (계정별 초당 호출 한도 있음)
- Rekognition 호출만 병렬로 하고, **DB 저장은 결과를 모아서 한 번에** (SQLite 동시 쓰기 문제 방지)
- 실패한 사진은 `try/except`로 따로 표시해서 한 장 실패가 전체를 멈추지 않게
- 측정: **사진 100장 처리 시간 (순차 vs 병렬)** 기록

### 5~6주차
- 화면 다듬기 (폰에서도 보기 좋게)
- `/` 메인에 **60일 미열람 안내 배너** (`get_old_hold_photos` 사용), 삭제 / 연장 / 평생 보관함 이동 버튼 연결
- 시연 시나리오에 맞춰 화면 흐름 점검

**🆕 ⑨ SQS 비동기 처리 (5주차, 필수 기능 완성 후)**
- 업로드 → 사진마다 SQS에 메시지 → `worker.py`가 꺼내서 분류(⑧의 병렬 함수 재사용) → 🆕 결과를 `tmp/<batch_id>/<photo_id>.result.json`에 기록(임시 파일 → `os.replace`) → 메시지 삭제. DB 저장은 사용자가 저장 버튼을 누를 때 `/save`에서 (v2.1 결정, 4-2 SQS 절 참고)
- 큐 URL은 `.env`의 `SQS_QUEUE_URL`에서만 읽음. 실제 서비스용 큐는 A 계정에 A가 생성
- 처리 중 화면: `/status`를 2초 간격으로 확인해서 끝난 사진부터 표시
- 사진이 적으면(예: 5장 이하) SQS 없이 바로 처리하는 방식도 고려
- `USE_SQS=false`로 언제든 기존 방식으로 되돌릴 수 있게 구현
- 측정: **첫 화면까지 걸린 시간 / 전체 완료 시간** (동기 vs 병렬 vs SQS) 비교

---

## 8. 주차별 공동 목표와 맞춰볼 시점

| 주차 | A | B | 함께 확인할 것 |
|---|---|---|---|
| 1~2 | 버킷, `db.py`(🆕 `status` 열 포함), `aws_utils.py` 뼈대, EC2 세팅 | 이미지 변환, Rekognition, 스크린샷 판별(🆕 + DetectText), 첫 화면 | **업로드하면 라벨이 화면에 표시** / 추가 함수와 임시 파일 위치 합의 |
| 3~4 | 저장, Presigned URL, 열람 기록, 용량 계산, 이동·삭제 함수 | 그룹화, 보관 위치 선택, 보관함 조회, 용량 화면, 🆕 해시 캐시, 병렬 처리 | **저장부터 조회까지 한 번에 연결** (내 컴퓨터 → EC2) |
| 5~6 | 라이프사이클, 60일 검사 + SNS, 자동 배포, 🆕 실제 SQS 큐·워커 서비스 | 화면 다듬기, 미열람 배너, 🆕 SQS 워커·처리 중 화면 | **통합 테스트, 시연 리허설, 최종 발표** |

**매주 한 번 짧게 맞춰보기 (권장)**
- 이번 주에 한 것 / 막힌 것 / 다음 주 할 것
- 함수 약속이나 데이터 규칙을 바꿀 일이 있는지
- `main`에 병합된 코드가 양쪽 컴퓨터에서 잘 도는지

---

## 9. 시연 준비 메모
- 90일·60일은 기간 안에 볼 수 없으므로:
  - 라이프사이클은 1일짜리 테스트 규칙으로 동작 확인
  - 보류함은 DB의 `last_viewed`를 과거 날짜로 바꾸고 `check_hold.py` 직접 실행 → 배너와 SNS 메일 확인
- 시연 전 EC2 퍼블릭 IP 확인 (Learner Lab 재시작 시 바뀜)
- 시연 순서(안): 사진 업로드 → 종류별 그룹 확인 → 그룹 단위로 keep/hold 선택 → 보관함에서 사진 열기 → 확보 용량 확인 → 보류함 알림(배너, 이메일) → 삭제/연장/이동
- 🆕 발표용 측정 결과 (B): 스크린샷 판별 정확도, Rekognition 호출 감소율, 처리 시간 비교표
- 🆕 시연 직전 `USE_SQS=true`로 정상 동작 확인, 문제 시 `false`로 전환

---

## 10. 🆕 참고: 지금까지 확인된 것
| 날짜 | 확인 내용 |
|---|---|
| 11/12 | Learner Lab에서 SQS 큐 생성, CloudShell로 메시지 보내기·받기·큐 삭제 성공 |
| 11/17 | SQS 비동기 처리 도입 합의 |
| 11/22 | A: `db.py`, `aws_utils.py`, `test_a_infra.py` main 병합 (PR #1), S3 테스트 11단계 통과 |
| 11/22 | v2.1 합의: SQS는 `result.json` 방식, 중복 방지, 열람 기록 옵션, HEIC→JPG 업로드 (0-1장) |
