"""Show or set the S3 lifecycle rule: keep/ -> Glacier Instant Retrieval after 90 days.

Usage (laptop, project root, venv active, Learner Lab running):
    venv/bin/python deploy/setup_lifecycle.py           # show current + planned rules (no change)
    venv/bin/python deploy/setup_lifecycle.py --apply   # register the rule on the bucket

Only the rule with ID 'keep-to-glacier-ir' is added or replaced; other rules on
the bucket are kept. hold/ is not touched (hold photos are deleted or moved by
the user, so archiving them would only add the Glacier IR 90-day minimum charge).
For the live demo, see archive_keep.py (KEEP_DEMO_MINUTES).
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aws_utils  # noqa: E402

RULE_ID = "keep-to-glacier-ir"
KEEP_DAYS = 90

RULE = {
    "ID": RULE_ID,
    "Status": "Enabled",
    "Filter": {"Prefix": "keep/"},
    "Transitions": [{"Days": KEEP_DAYS, "StorageClass": "GLACIER_IR"}],
}


def current_rules(s3, bucket: str) -> list[dict]:
    try:
        return s3.get_bucket_lifecycle_configuration(Bucket=bucket)["Rules"]
    except s3.exceptions.ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchLifecycleConfiguration":
            return []
        raise


def main() -> int:
    s3, bucket = aws_utils._s3(), aws_utils._bucket()
    rules = current_rules(s3, bucket)
    print(f"bucket : {bucket}")
    print(f"current: {json.dumps(rules, indent=2) if rules else '(no lifecycle rules)'}")

    planned = [r for r in rules if r.get("ID") != RULE_ID] + [RULE]
    print(f"planned: {json.dumps(planned, indent=2)}")

    if "--apply" not in sys.argv:
        print("\n(dry run) add --apply to register the rule")
        return 0
    s3.put_bucket_lifecycle_configuration(Bucket=bucket,
                                          LifecycleConfiguration={"Rules": planned})
    print(f"\napplied: {[r['ID'] for r in current_rules(s3, bucket)]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
