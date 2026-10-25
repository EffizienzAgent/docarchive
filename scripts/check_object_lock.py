#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["boto3"]
# ///
"""Check the key permissions of one Object Lock archive bucket.

The writer key (the one the site uses) may upload with retention, extend it and
read, but never delete, shorten, bypass governance or change bucket settings.
The optional archive admin key may read, release legal holds and delete expired
or (with bypass) GOVERNANCE versions, but never upload, change retention or
bucket settings, and never delete a COMPLIANCE version.

Keys come from AWS profiles (--writer-profile, --admin-profile) or from
WRITER_ACCESS_KEY_ID/WRITER_SECRET_ACCESS_KEY and ADMIN_ACCESS_KEY_ID/
ADMIN_SECRET_ACCESS_KEY. Without an admin key the check objects stay under
the prefix: the locked ones until their short retention ends, the unlocked one
until someone deletes it.

    uv run scripts/check_object_lock.py --endpoint https://fsn1.your-objectstorage.com \\
        --region fsn1 --bucket example-archive-fsn1 --writer-profile writer --admin-profile archive-admin
"""

import argparse
import base64
import hashlib
import os
import secrets
import sys
import time
from datetime import UTC, datetime, timedelta

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

BODY = b"docarchive object lock check\n"
COMPLIANCE_SECONDS = 90


class Checker:
	def __init__(self):
		self.failed = 0

	def check(self, name, want, call):
		try:
			call()
			got, detail = "ok", ""
		except ClientError as error:
			got, detail = "denied", error.response.get("Error", {}).get("Code") or "denied"
		except (BotoCoreError, KeyError) as error:
			got, detail = "error", str(error)
		if got == want:
			print(f"ok   {name}" + (f" ({detail})" if detail else ""))
		else:
			self.failed += 1
			print(f"FAIL {name}: expected {want}, got {got}" + (f" ({detail[:160]})" if detail else ""))


def client(endpoint, region, profile, env_prefix):
	if profile:
		session = boto3.Session(profile_name=profile)
	elif os.environ.get(f"{env_prefix}_ACCESS_KEY_ID"):
		session = boto3.Session(
			aws_access_key_id=os.environ[f"{env_prefix}_ACCESS_KEY_ID"],
			aws_secret_access_key=os.environ[f"{env_prefix}_SECRET_ACCESS_KEY"],
		)
	else:
		return None
	config = Config(
		s3={"addressing_style": "path"},
		request_checksum_calculation="when_required",
		response_checksum_validation="when_required",
		retries={"max_attempts": 3},
	)
	return session.client("s3", endpoint_url=endpoint, region_name=region, config=config)


def in_seconds(seconds):
	return datetime.now(UTC) + timedelta(seconds=seconds)


def parse_args():
	parser = argparse.ArgumentParser(
		description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
	)
	parser.add_argument("--endpoint", required=True)
	parser.add_argument("--region", required=True)
	parser.add_argument("--bucket", required=True)
	parser.add_argument("--writer-profile")
	parser.add_argument("--admin-profile")
	parser.add_argument("--prefix", default="_check/")
	parser.add_argument(
		"--legal-hold",
		choices=("off", "upload", "api"),
		default="off",
		help="what the writer may do with legal holds: nothing (skip the checks), set one at upload, "
		"or also set one with PutObjectLegalHold. It may never release one. Needs the admin key.",
	)
	parser.add_argument(
		"--admin-bypass",
		choices=("allowed", "denied"),
		default="allowed",
		help="whether the admin key may delete GOVERNANCE versions with BypassGovernanceRetention",
	)
	parser.add_argument("--no-compliance", action="store_true", help="skip the short-lived COMPLIANCE object")
	return parser.parse_args()


def main():
	args = parse_args()
	writer = client(args.endpoint, args.region, args.writer_profile, "WRITER")
	admin = client(args.endpoint, args.region, args.admin_profile, "ADMIN")
	if not writer:
		sys.exit("No writer key: pass --writer-profile or set WRITER_ACCESS_KEY_ID/WRITER_SECRET_ACCESS_KEY.")
	hold = args.legal_hold if admin else "off"
	if args.legal_hold != "off" and not admin:
		print("SKIP legal hold checks: they need the admin key to release the hold again")
	if not admin:
		print("SKIP admin checks: no admin key")

	bucket = args.bucket
	run = f"{args.prefix}{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{secrets.token_hex(4)}"
	md5 = base64.b64encode(hashlib.md5(BODY).digest()).decode()
	c = Checker()
	versions = {}

	def put(name, s3=writer, **extra):
		key = f"{run}-{name}.txt"
		response = s3.put_object(Bucket=bucket, Key=key, Body=BODY, ContentMD5=md5, **extra)
		versions[name] = (key, response.get("VersionId"))

	def ref(name):
		key, version = versions[name]
		return {"Bucket": bucket, "Key": key, "VersionId": version}

	def retention(s3, name, seconds, bypass=False):
		s3.put_object_retention(
			**ref(name),
			Retention={"Mode": "GOVERNANCE", "RetainUntilDate": in_seconds(seconds)},
			BypassGovernanceRetention=bypass,
		)

	def legal_hold(s3, name, status):
		s3.put_object_legal_hold(**ref(name), LegalHold={"Status": status})

	def delete(s3, name, bypass=False):
		s3.delete_object(**ref(name), BypassGovernanceRetention=bypass)

	def head_mode(name, mode):
		response = writer.head_object(**ref(name))
		if response.get("ObjectLockMode") != mode or not response.get("ObjectLockRetainUntilDate"):
			raise ClientError({"Error": {"Code": f"lock is {response.get('ObjectLockMode')}"}}, "HeadObject")

	def bucket_changes(s3, who):
		c.check(
			f"{who}: put bucket policy denied",
			"denied",
			lambda: s3.put_bucket_policy(Bucket=bucket, Policy='{"Version":"2012-10-17","Statement":[]}'),
		)
		c.check(
			f"{who}: suspend versioning denied",
			"denied",
			lambda: s3.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Suspended"}),
		)
		c.check(
			f"{who}: put object lock configuration denied",
			"denied",
			lambda: s3.put_object_lock_configuration(
				Bucket=bucket, ObjectLockConfiguration={"ObjectLockEnabled": "Enabled"}
			),
		)

	print(f"== {bucket} ({args.endpoint})")
	c.check(
		"writer: put with GOVERNANCE retention",
		"ok",
		lambda: put("governance", ObjectLockMode="GOVERNANCE", ObjectLockRetainUntilDate=in_seconds(600)),
	)
	if "governance" not in versions or not versions["governance"][1]:
		print("FAIL no version id: is versioning (Object Lock) enabled on the bucket?")
		sys.exit(1)
	c.check("writer: retention is stored", "ok", lambda: head_mode("governance", "GOVERNANCE"))
	c.check("writer: extend retention", "ok", lambda: retention(writer, "governance", 1200))
	c.check("writer: shorten retention denied", "denied", lambda: retention(writer, "governance", 300))
	c.check(
		"writer: shorten retention with bypass denied",
		"denied",
		lambda: retention(writer, "governance", 300, bypass=True),
	)
	c.check("writer: delete GOVERNANCE version denied", "denied", lambda: delete(writer, "governance"))
	c.check(
		"writer: delete GOVERNANCE version with bypass denied",
		"denied",
		lambda: delete(writer, "governance", bypass=True),
	)
	bucket_changes(writer, "writer")
	c.check("writer: put without retention", "ok", lambda: put("unlocked"))
	c.check(
		"writer: delete without version id (delete marker) denied",
		"denied",
		lambda: writer.delete_object(Bucket=bucket, Key=versions["unlocked"][0]),
	)
	c.check("writer: delete unlocked version denied", "denied", lambda: delete(writer, "unlocked"))

	if hold != "off":
		c.check(
			"writer: set legal hold with PutObjectLegalHold",
			"ok" if hold == "api" else "denied",
			lambda: legal_hold(writer, "unlocked", "ON"),
		)
		c.check("writer: put with legal hold", "ok", lambda: put("held", ObjectLockLegalHoldStatus="ON"))
		c.check("writer: release legal hold denied", "denied", lambda: legal_hold(writer, "held", "OFF"))

	compliance_until = None
	if not args.no_compliance:
		compliance_until = time.time() + COMPLIANCE_SECONDS
		c.check(
			f"writer: put with {COMPLIANCE_SECONDS}s COMPLIANCE retention",
			"ok",
			lambda: put(
				"compliance",
				ObjectLockMode="COMPLIANCE",
				ObjectLockRetainUntilDate=in_seconds(COMPLIANCE_SECONDS),
			),
		)

	if not admin:
		print(f"Check objects stay under {run}-*")
		sys.exit(1 if c.failed else 0)

	c.check(
		"admin: list versions",
		"ok",
		lambda: admin.list_object_versions(Bucket=bucket, Prefix=run),
	)
	c.check("admin: get object", "ok", lambda: admin.get_object(**ref("governance"))["Body"].read())
	c.check("admin: get retention", "ok", lambda: admin.get_object_retention(**ref("governance")))
	c.check("admin: put object denied", "denied", lambda: put("admin", s3=admin))
	c.check(
		"admin: shorten retention with bypass denied",
		"denied",
		lambda: retention(admin, "governance", 300, bypass=True),
	)
	bucket_changes(admin, "admin")
	c.check(
		"admin: delete GOVERNANCE version without bypass denied",
		"denied",
		lambda: delete(admin, "governance"),
	)
	if "compliance" in versions:
		c.check(
			"admin: delete COMPLIANCE version with bypass denied",
			"denied",
			lambda: delete(admin, "compliance", bypass=True),
		)
	if "held" in versions:
		c.check("admin: get legal hold", "ok", lambda: admin.get_object_legal_hold(**ref("held")))
		c.check("admin: delete held version denied", "denied", lambda: delete(admin, "held", bypass=True))
		c.check("admin: release legal hold", "ok", lambda: legal_hold(admin, "held", "OFF"))
		c.check("admin: delete released version", "ok", lambda: delete(admin, "held"))
	if hold == "api":
		c.check(
			"admin: release legal hold on the unlocked version",
			"ok",
			lambda: legal_hold(admin, "unlocked", "OFF"),
		)
	c.check("admin: delete unlocked version", "ok", lambda: delete(admin, "unlocked"))
	c.check(
		"admin: delete GOVERNANCE version with bypass",
		"ok" if args.admin_bypass == "allowed" else "denied",
		lambda: delete(admin, "governance", bypass=True),
	)

	if compliance_until:
		wait = compliance_until - time.time() + 5
		if wait > 0:
			print(f"     waiting {wait:.0f}s for the COMPLIANCE version to expire")
			time.sleep(wait)
		c.check("admin: delete expired COMPLIANCE version", "ok", lambda: delete(admin, "compliance"))

	sys.exit(1 if c.failed else 0)


if __name__ == "__main__":
	main()
