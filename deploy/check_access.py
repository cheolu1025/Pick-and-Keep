"""Check (and optionally fix) access to the Pick & Keep EC2 server.

Run from the project root on the laptop (venv active):
    python3 deploy/check_access.py          # read-only check
    python3 deploy/check_access.py --fix    # replace the SSH rule with this laptop's current IP

What it checks:
  - server state and current public IP (changes every time the lab restarts)
  - this laptop's public IP (changes when you move to another Wi-Fi)
  - whether the security group's SSH rule allows this laptop
Only the SSH (port 22) rule is changed by --fix; HTTP (port 80) is never touched.
"""

import os
import ssl
import sys
import urllib.request
from datetime import date

import boto3
from botocore.exceptions import ClientError
from botocore.httpsession import get_cert_path

INSTANCE_NAME = os.getenv("EC2_INSTANCE_NAME", "pick-and-keep-server")
KEY_PATH = "~/.ssh/pick-and-keep-key.pem"
REGION = os.getenv("AWS_REGION", "us-east-1")


def my_public_ip() -> str:
    # python.org macOS builds ship without CA certs; reuse botocore's bundle
    ctx = ssl.create_default_context(cafile=get_cert_path(True))
    with urllib.request.urlopen("https://checkip.amazonaws.com", timeout=5, context=ctx) as resp:
        return resp.read().decode().strip()


def find_instance(ec2) -> dict | None:
    resp = ec2.describe_instances(Filters=[
        {"Name": "tag:Name", "Values": [INSTANCE_NAME]},
        {"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"]},
    ])
    instances = [i for r in resp["Reservations"] for i in r["Instances"]]
    return instances[0] if instances else None


def ssh_cidrs(sg: dict) -> list[str]:
    return [r["CidrIp"] for p in sg["IpPermissions"]
            if p.get("FromPort") == 22 and p.get("ToPort") == 22
            for r in p.get("IpRanges", [])]


def replace_ssh_rule(ec2, sg_id: str, old: list[str], new_cidr: str) -> None:
    ec2.authorize_security_group_ingress(GroupId=sg_id, IpPermissions=[{
        "IpProtocol": "tcp", "FromPort": 22, "ToPort": 22,
        "IpRanges": [{"CidrIp": new_cidr, "Description": f"A laptop SSH {date.today()}"}],
    }])
    stale = [c for c in old if c != new_cidr]
    if stale:
        ec2.revoke_security_group_ingress(GroupId=sg_id, IpPermissions=[{
            "IpProtocol": "tcp", "FromPort": 22, "ToPort": 22,
            "IpRanges": [{"CidrIp": c} for c in stale],
        }])


def main() -> int:
    fix = "--fix" in sys.argv
    ec2 = boto3.client("ec2", region_name=REGION)
    try:
        inst = find_instance(ec2)
    except ClientError as e:
        code = e.response["Error"]["Code"]
        print(f"[ERROR] AWS call failed: {code}")
        if "Expired" in code or "Invalid" in code:
            print("  -> Learner Lab credentials expired: copy AWS Details again into ~/.aws/credentials")
        return 1

    if inst is None:
        print(f"[ERROR] instance '{INSTANCE_NAME}' not found")
        return 1

    state = inst["State"]["Name"]
    server_ip = inst.get("PublicIpAddress")
    print(f"server   : {INSTANCE_NAME} ({state})")
    if state != "running" or not server_ip:
        print("  -> server is not running. Start Lab (or start the instance) and run this again.")
        return 1
    print(f"server IP: {server_ip}   <- changes every lab restart")

    laptop_cidr = f"{my_public_ip()}/32"
    sg = ec2.describe_security_groups(GroupIds=[inst["SecurityGroups"][0]["GroupId"]])["SecurityGroups"][0]
    allowed = ssh_cidrs(sg)
    print(f"laptop IP: {laptop_cidr}   <- changes when you move to another Wi-Fi")
    print(f"SSH allow: {', '.join(allowed) or '(none)'}  [{sg['GroupName']}]")

    ok = laptop_cidr in allowed or "0.0.0.0/0" in allowed
    if not ok and fix:
        replace_ssh_rule(ec2, sg["GroupId"], allowed, laptop_cidr)
        print(f"[FIXED] SSH rule now allows only {laptop_cidr}")
        ok = True

    print()
    print(f"web      : http://{server_ip}")
    if ok:
        print(f"SSH      : OK -> ssh -i {KEY_PATH} ubuntu@{server_ip}")
        return 0
    print("SSH      : BLOCKED (laptop IP not allowed). Run with --fix to update the SSH rule.")
    return 2


if __name__ == "__main__":
    sys.exit(main())
