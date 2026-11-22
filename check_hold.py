"""Email an alert (SNS) about hold photos that were not viewed for the hold period.

Usage (project root, venv active):
    python3 check_hold.py            # send the alert (needs SNS_TOPIC_ARN in .env)
    python3 check_hold.py --dry-run  # print the email instead of sending it

On EC2 this runs every minute from cron (deploy/crontab.txt). Each photo is
reported only once, so frequent runs do not repeat the same email.
Hold period: .env HOLD_DAYS (default 60) or HOLD_DEMO_MINUTES (demo mode).
"""

import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

import boto3
from dotenv import load_dotenv

import db

load_dotenv()

MAX_LISTED = 10
KST = timezone(timedelta(hours=9))
_IMDS = "http://169.254.169.254/latest"


# ---------- helpers ----------

def _ec2_public_ip() -> str | None:
    """Ask EC2 instance metadata for this server's current public IP (None off EC2)."""
    try:
        token_req = urllib.request.Request(
            f"{_IMDS}/api/token", method="PUT",
            headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"})
        token = urllib.request.urlopen(token_req, timeout=1).read().decode()
        ip_req = urllib.request.Request(
            f"{_IMDS}/meta-data/public-ipv4",
            headers={"X-aws-ec2-metadata-token": token})
        return urllib.request.urlopen(ip_req, timeout=1).read().decode().strip() or None
    except OSError:
        return None


def site_url() -> str | None:
    """Link for the email: .env SITE_URL if set, else this server's current IP."""
    explicit = os.getenv("SITE_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    ip = _ec2_public_ip()
    return f"http://{ip}" if ip else None


def _human_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024


def _kst(iso: str) -> str:
    return datetime.fromisoformat(iso).astimezone(KST).strftime("%Y-%m-%d %H:%M")


def _period_en(period: timedelta) -> str:
    if period < timedelta(days=1):
        return f"{period.total_seconds() / 60:g} min"
    return f"{period.total_seconds() / 86400:g} days"


# ---------- message ----------

def build_message(photos: list[dict], url: str | None) -> tuple[str, str]:
    """Return (subject, body). SNS email subjects must be ASCII, so it is in English."""
    n = len(photos)
    label = db.hold_period_label()
    subject = f"[Pick & Keep] {n} photo(s) in Hold not viewed for {_period_en(db.hold_period())}"

    lines = [f"보류함에 {label} 동안 열어보지 않은 사진이 {n}장 있어요.", ""]
    for p in photos[:MAX_LISTED]:
        lines.append(f"- {p['filename']} ({_human_size(p['size'])}, 마지막 열람 {_kst(p['last_viewed'])})")
    if n > MAX_LISTED:
        lines.append(f"- 외 {n - MAX_LISTED}장")
    total = sum(p["size"] for p in photos)
    lines += ["", f"합계 {_human_size(total)}",
              f"계속 보관할지 정해 주세요: 삭제 / {label} 연장 / 평생 보관함으로 이동"]
    if url:
        lines.append(f"바로 가기: {url}")
    return subject, "\n".join(lines)


# ---------- main ----------

def main() -> int:
    dry_run = "--dry-run" in sys.argv
    stamp = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST")

    db.init_db()
    photos = db.get_photos_to_notify()
    if not photos:
        print(f"{stamp} nothing to notify (hold period {db.hold_period_label()})")
        return 0

    subject, body = build_message(photos, site_url())
    topic_arn = os.getenv("SNS_TOPIC_ARN", "").strip()

    if dry_run or not topic_arn:
        if not topic_arn and not dry_run:
            print(f"{stamp} SNS_TOPIC_ARN is empty: printing instead of sending")
        print(f"Subject: {subject}\n\n{body}")
        return 0  # not marked as notified, so a real run will still send it

    sns = boto3.client("sns", region_name=os.getenv("AWS_REGION", "us-east-1"))
    msg_id = sns.publish(TopicArn=topic_arn, Subject=subject, Message=body)["MessageId"]
    db.mark_notified([p["s3_key"] for p in photos])
    print(f"{stamp} sent alert for {len(photos)} photo(s), MessageId={msg_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
