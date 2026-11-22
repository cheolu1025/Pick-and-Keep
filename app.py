"""Flask 라우트 (B 담당)

업로드 방식: 사진을 한 장씩 보내서 진행률을 보여준다.
  POST /batch                       → 업로드 묶음(batch) 만들기, batch_id 반환
  POST /batch/<batch_id>/photo      → 사진 한 장 분류, 결과 반환
  POST /batch/<batch_id>/finish     → 결과 모아서 result.json 저장
  GET  /classify/<batch_id>         → 분류 결과 화면
POST /upload 는 자바스크립트가 꺼져 있을 때를 위한 예비 경로 (한 번에 전부 처리)

3주차 이후: /save, /keep, /hold, /stats 는 A의 함수가 준비되면 연결
"""
import json
import os

from dotenv import load_dotenv

load_dotenv()  # .env를 먼저 읽어야 아래 모듈들이 설정값을 가져감

from flask import (Flask, abort, flash, jsonify, redirect, render_template,
                   request, send_from_directory, url_for)

import classify
import image_utils

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-only-change-me")
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024  # 요청 하나당 최대 200MB


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
    return render_template("index.html")


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
    photos = [r for r in results if not r.get("is_screenshot")]
    return render_template("classify.html", batch_id=batch_id,
                           photos=photos, screenshots=screenshots)


@app.route("/tmp/<batch_id>/<filename>")
def tmp_image(batch_id, filename):
    """분류 결과 화면에 썸네일을 보여주기 위한 경로 (S3 저장 전 임시 사진)"""
    return send_from_directory(os.path.abspath(_batch_path(batch_id)), filename)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
