"""Flask 라우트 (B 담당)

업로드 방식: 사진을 한 장씩 보내서 진행률을 보여준다.
  POST /batch                       → 업로드 묶음(batch) 만들기, batch_id 반환
  POST /batch/<batch_id>/photo      → 사진 한 장 분류, 결과 반환
  POST /batch/<batch_id>/finish     → 결과 모아서 result.json 저장
  GET  /classify/<batch_id>         → 분류 결과 + 보관 위치 선택 화면
  POST /batch/<batch_id>/save       → 고른 사진 저장 (storage.py → A의 함수)
  GET  /saved/<batch_id>            → 저장 완료 화면
POST /upload 는 자바스크립트가 꺼져 있을 때를 위한 예비 경로 (한 번에 전부 처리)

이후: /keep, /hold, /stats 는 A의 함수가 준비되면 연결
"""
import json
import os

from dotenv import load_dotenv

load_dotenv()  # .env를 먼저 읽어야 아래 모듈들이 설정값을 가져감

from flask import (Flask, abort, flash, jsonify, redirect, render_template,
                   request, send_from_directory, session, url_for)

import classify
import db
import image_utils
import storage

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-only-change-me")
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024  # 요청 하나당 최대 200MB

db.init_db()  # A: photos 테이블이 없으면 만든다 (여러 번 불러도 괜찮음)


def _batch_path(batch_id: str) -> str:
    # batch_id는 우리가 만든 12자리 hex만 허용 (경로 조작 방지)
    if not (len(batch_id) == 12 and all(c in "0123456789abcdef" for c in batch_id)):
        abort(404)
    path = os.path.join(image_utils.TMP_DIR, batch_id)
    if not os.path.isdir(path):
        abort(404)
    return path


def _process_one(file_storage, batch_path: str) -> dict:
    """사진 한 장: 저장 → HEIC 변환 → 스크린샷 판별/라벨 → 썸네일"""
    saved = image_utils.save_upload(file_storage, batch_path)
    try:
        path = image_utils.to_jpeg_if_heic(saved["path"])
        result = classify.classify_photo(saved["original_name"], path)
        thumb = image_utils.make_thumbnail(path)
        result.update(
            photo_id=saved["photo_id"],
            original_name=saved["original_name"],
            path=path,
            thumb=os.path.basename(thumb),
            size=os.path.getsize(saved["path"]),  # 원본 크기 = 확보 가능 용량 계산용
            hash=image_utils.file_hash(saved["path"]),
            error=None,
        )
    except Exception as e:  # 한 장 실패가 전체를 멈추지 않게
        app.logger.exception("분류 실패: %s", saved["original_name"])
        result = {"photo_id": saved["photo_id"], "original_name": saved["original_name"],
                  "error": str(e), "is_screenshot": False, "labels": [], "thumb": None}

    # 한 장씩 결과를 파일로 남겨둔다 (finish에서 모음)
    with open(os.path.join(batch_path, f"{saved['photo_id']}.result.json"), "w",
              encoding="utf-8") as fp:
        json.dump(result, fp, ensure_ascii=False)
    return result


def _save_results(batch_path: str, results: list) -> None:
    with open(os.path.join(batch_path, "result.json"), "w", encoding="utf-8") as fp:
        json.dump(results, fp, ensure_ascii=False, indent=2)


@app.route("/")
def index():
    return render_template("index.html", film=_film_photos(6))


SAMPLE_DIR = os.path.join(app.static_folder, "sample")


def _sample_sets() -> list[list[str]]:
    """기본 사진 묶음 목록. static/sample/ 아래 폴더 하나가 한 묶음 (예: sample/city, sample/warm)"""
    if not os.path.isdir(SAMPLE_DIR):
        return []
    sets = []
    for d in sorted(os.listdir(SAMPLE_DIR)):
        folder = os.path.join(SAMPLE_DIR, d)
        if os.path.isdir(folder):
            files = sorted(f for f in os.listdir(folder) if image_utils.is_allowed(f))
            if files:
                sets.append([f"sample/{d}/{f}" for f in files])
    return sets


def _film_photos(n: int) -> list[dict]:
    """첫 화면 오른쪽 사진 n장: 최근 평생 보관한 사진을 먼저, 모자라면 기본 사진으로 채움.
    기본 사진 묶음이 여러 개면 첫 화면을 열 때마다 번갈아 보여준다 (session에 순번 저장)"""
    photos = storage.recent_keep_photos(n)
    sets = _sample_sets()
    if len(photos) < n and sets:
        turn = session.get("film_set", -1) + 1
        session["film_set"] = turn
        for path in sets[turn % len(sets)][:n - len(photos)]:
            photos.append({"url": url_for("static", filename=path), "name": "기본 사진"})
    return photos


# ---------- 한 장씩 업로드 (진행률 표시용) ----------
@app.route("/batch", methods=["POST"])
def create_batch():
    batch_id, _ = image_utils.new_batch_dir()
    return jsonify(batch_id=batch_id)


@app.route("/batch/<batch_id>/photo", methods=["POST"])
def upload_one(batch_id):
    batch_path = _batch_path(batch_id)
    f = request.files.get("photo")
    if not f or not f.filename:
        return jsonify(error="사진이 없어요."), 400
    if not image_utils.is_allowed(f.filename):
        return jsonify(error="지원하지 않는 형식", skipped=f.filename), 415
    result = _process_one(f, batch_path)
    return jsonify(photo_id=result["photo_id"], is_screenshot=result.get("is_screenshot"),
                   error=result.get("error"))


@app.route("/batch/<batch_id>/finish", methods=["POST"])
def finish_batch(batch_id):
    """브라우저가 보낸 순서(photo_ids)대로 결과를 모아 result.json으로 저장"""
    batch_path = _batch_path(batch_id)
    body = request.get_json(silent=True) or {}
    results = []
    for pid in body.get("photo_ids", []):
        if not (len(pid) == 12 and all(c in "0123456789abcdef" for c in pid)):
            continue
        p = os.path.join(batch_path, f"{pid}.result.json")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as fp:
                results.append(json.load(fp))
    _save_results(batch_path, results)

    skipped = body.get("skipped") or []
    if skipped:
        flash(f"지원하지 않는 형식이라 제외했어요: {', '.join(skipped)}")
    return jsonify(redirect=url_for("classify_view", batch_id=batch_id))


# ---------- 예비 경로: 한 번에 전부 (자바스크립트가 꺼져 있을 때) ----------
@app.route("/upload", methods=["POST"])
def upload():
    files = [f for f in request.files.getlist("photos") if f and f.filename]
    if not files:
        flash("사진을 선택해 주세요.")
        return redirect(url_for("index"))

    batch_id, batch_path = image_utils.new_batch_dir()
    results, skipped = [], []
    for f in files:
        if not image_utils.is_allowed(f.filename):
            skipped.append(f.filename)
            continue
        results.append(_process_one(f, batch_path))
    _save_results(batch_path, results)

    if skipped:
        flash(f"지원하지 않는 형식이라 제외했어요: {', '.join(skipped)}")
    return redirect(url_for("classify_view", batch_id=batch_id))


@app.route("/classify/<batch_id>")
def classify_view(batch_id):
    path = _batch_path(batch_id)
    result_file = os.path.join(path, "result.json")
    if not os.path.exists(result_file):
        abort(404)
    with open(result_file, encoding="utf-8") as fp:
        results = json.load(fp)
    screenshots = [r for r in results if r.get("is_screenshot")]
    reviews = [r for r in results if r.get("review")]
    groups = classify.group_photos(results)
    photo_count = sum(len(g["photos"]) for g in groups)
    # 그룹 바꾸기 목록: 9개 그룹 + 스크린샷
    group_options = [(k, classify.GROUP_INFO[k]) for k in classify.GROUP_ORDER]
    group_options.append(("screenshot", {"name": "스크린샷", "emoji": "📱"}))
    return render_template("classify.html", batch_id=batch_id, groups=groups,
                           photo_count=photo_count, screenshots=screenshots,
                           reviews=reviews, group_info=classify.GROUP_INFO,
                           group_options=group_options, all_results=results,
                           storage_mode=storage.mode())


VALID_DEST = {"keep", "hold", "skip"}


@app.route("/batch/<batch_id>/save", methods=["POST"])
def save_batch(batch_id):
    """화면에서 고른 결과를 받아 keep/hold 사진만 저장한다.
    받는 값: {"decisions": [{"photo_id", "group", "dest", "changed"}, ...]}"""
    batch_path = _batch_path(batch_id)
    with open(os.path.join(batch_path, "result.json"), encoding="utf-8") as fp:
        results = {r["photo_id"]: r for r in json.load(fp)}

    valid_groups = set(classify.GROUP_ORDER) | {"screenshot"}
    decisions = (request.get_json(silent=True) or {}).get("decisions", [])
    summary = {"keep": 0, "hold": 0, "skip": 0, "duplicate": 0, "failed": 0,
               "saved_size": 0, "changed": 0, "classified": 0, "mode": storage.mode()}

    for d in decisions:
        r = results.get(d.get("photo_id"))
        dest, group = d.get("dest"), d.get("group")
        if not r or dest not in VALID_DEST or group not in valid_groups:
            continue
        r["final_group"], r["dest"], r["user_changed"] = group, dest, bool(d.get("changed"))

        # AI가 분류한 사진 중 사용자가 그룹을 바꾼 수 → 실제 사용 정확도
        # (확인 필요 사진은 AI가 정하지 않았으므로 빼고 센다)
        if not r.get("error") and not d.get("review"):
            summary["classified"] += 1
            summary["changed"] += r["user_changed"]

        if dest == "skip":
            summary["skip"] += 1
            continue
        try:
            out = storage.save_photo(r, dest)
        except Exception:
            app.logger.exception("저장 실패: %s", r["original_name"])
            summary["failed"] += 1
            r["save_status"] = "failed"
            continue
        r["save_status"] = out["status"]
        if out["status"] == "duplicate":
            summary["duplicate"] += 1
        else:
            summary[dest] += 1
            summary["saved_size"] += r["size"]

    with open(os.path.join(batch_path, "saved.json"), "w", encoding="utf-8") as fp:
        json.dump({"summary": summary, "results": list(results.values())}, fp,
                  ensure_ascii=False, indent=2)
    return jsonify(redirect=url_for("saved_view", batch_id=batch_id))


@app.route("/saved/<batch_id>")
def saved_view(batch_id):
    path = os.path.join(_batch_path(batch_id), "saved.json")
    if not os.path.exists(path):
        return redirect(url_for("classify_view", batch_id=batch_id))
    with open(path, encoding="utf-8") as fp:
        data = json.load(fp)
    return render_template("saved.html", s=data["summary"])


@app.route("/tmp/<batch_id>/<filename>")
def tmp_image(batch_id, filename):
    """분류 결과 화면에 썸네일을 보여주기 위한 경로 (S3 저장 전 임시 사진)"""
    return send_from_directory(os.path.abspath(_batch_path(batch_id)), filename)


@app.route("/local/<path:key>")
def local_photo(key):
    """개발 모드(S3 없이 저장)에서 tmp/_local_store 사진을 보여주는 경로"""
    if storage.mode() != "local":
        abort(404)
    return send_from_directory(os.path.abspath(storage.LOCAL_STORE), key)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
