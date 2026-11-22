"""Flask 라우트 (B 담당)

1~2주차 범위: / (업로드 화면), /upload, /classify (라벨 표시)
3주차 이후: /save, /keep, /hold, /stats 는 A의 함수가 준비되면 연결
"""
import json
import os

from dotenv import load_dotenv

load_dotenv()  # .env를 먼저 읽어야 아래 모듈들이 설정값을 가져감

from flask import (Flask, abort, flash, redirect, render_template, request,
                   send_from_directory, url_for)

import classify
import image_utils

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-only-change-me")
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024  # 한 번에 최대 200MB 업로드


def _batch_path(batch_id: str) -> str:
    # batch_id는 우리가 만든 12자리 hex만 허용 (경로 조작 방지)
    if not (len(batch_id) == 12 and all(c in "0123456789abcdef" for c in batch_id)):
        abort(404)
    path = os.path.join(image_utils.TMP_DIR, batch_id)
    if not os.path.isdir(path):
        abort(404)
    return path


@app.route("/")
def index():
    return render_template("index.html")


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
        saved = image_utils.save_upload(f, batch_path)
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
        results.append(result)

    # /classify, /save 에서 쓰도록 분류 결과를 tmp/<batch_id>/result.json 에 저장
    with open(os.path.join(batch_path, "result.json"), "w", encoding="utf-8") as fp:
        json.dump(results, fp, ensure_ascii=False, indent=2)

    if skipped:
        flash(f"지원하지 않는 형식이라 제외했어요: {', '.join(skipped)}")
    return redirect(url_for("classify_view", batch_id=batch_id))


@app.route("/classify/<batch_id>")
def classify_view(batch_id):
    path = _batch_path(batch_id)
    with open(os.path.join(path, "result.json"), encoding="utf-8") as fp:
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
