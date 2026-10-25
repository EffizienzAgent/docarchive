#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["boto3"]
# ///
"""Build, and with --apply set, the deny-only policy of an Object Lock archive bucket.

Only the admin key, the writer key and the optional archive admin key get
access. The writer may never delete, bypass governance or change bucket
settings; the archive admin may never upload, change retention or change bucket
settings. All statements are Deny with explicit Action lists, because Ceph-based
storage ignores NotAction. Keys other than the admin key are named in
Principal, never NotPrincipal, so no statement can catch the admin key.

Without --apply the policy is only printed. --profile must be the admin key: a
policy that misses it can lock it out of the bucket, and undoing that needs the
provider's support. The script refuses if the profile's access key ID is not
part of --admin-principal (Hetzner and other Ceph providers use it in the
principal; pass --skip-admin-check where principals are user ARNs instead).

    uv run scripts/apply_bucket_policy.py --endpoint https://fsn1.your-objectstorage.com \\
        --region fsn1 --bucket example-archive-fsn1 --profile admin \\
        --admin-principal arn:aws:iam:::user/p<project>:<admin key> \\
        --writer-principal arn:aws:iam:::user/p<project>:<writer key> --apply
"""

import argparse
import json
import sys
import time

import boto3
from botocore.config import Config

BUCKET_CHANGES = [
	"s3:DeleteBucket",
	"s3:PutBucketObjectLockConfiguration",
	"s3:PutBucketVersioning",
	"s3:PutBucketPolicy",
	"s3:DeleteBucketPolicy",
	"s3:PutLifecycleConfiguration",
	"s3:PutObjectAcl",
	"s3:PutObjectVersionAcl",
	"s3:PutBucketAcl",
	"s3:PutBucketCORS",
	"s3:PutBucketNotification",
]

WRITER_DENIED = [
	"s3:DeleteObject",
	"s3:DeleteObjectVersion",
	"s3:BypassGovernanceRetention",
	*BUCKET_CHANGES,
]

ARCHIVE_ADMIN_DENIED = [
	"s3:PutObject",
	"s3:DeleteObject",
	"s3:PutObjectRetention",
	"s3:AbortMultipartUpload",
	"s3:PutObjectTagging",
	"s3:PutObjectVersionTagging",
	"s3:DeleteObjectTagging",
	"s3:DeleteObjectVersionTagging",
	*BUCKET_CHANGES,
]


def build_policy(args):
	resources = [f"arn:aws:s3:::{args.bucket}", f"arn:aws:s3:::{args.bucket}/*"]
	allowed = [args.admin_principal, args.writer_principal]
	if args.archive_admin_principal:
		allowed.append(args.archive_admin_principal)

	def deny(sid, principal, actions, **extra):
		return {
			"Sid": sid,
			"Effect": "Deny",
			"Principal": {"AWS": [principal]},
			"Action": actions,
			"Resource": resources,
			**extra,
		}

	writer_denied = list(WRITER_DENIED)
	if args.writer_legal_hold == "deny":
		writer_denied.append("s3:PutObjectLegalHold")
	statements = [
		{
			"Sid": "OnlyAllowedKeys",
			"Effect": "Deny",
			"NotPrincipal": {"AWS": allowed},
			"Action": ["s3:*"],
			"Resource": resources,
		},
		deny("WriterCannotDestroy", args.writer_principal, writer_denied),
	]
	if args.writer_legal_hold == "on-only":
		# Null keeps this fail-safe where the provider does not fill the condition key.
		statements += [
			deny(
				"WriterCannotReleaseLegalHold",
				args.writer_principal,
				["s3:PutObjectLegalHold"],
				Condition={"StringNotEquals": {"s3:object-lock-legal-hold": "ON"}},
			),
			deny(
				"WriterLegalHoldNeedsStatus",
				args.writer_principal,
				["s3:PutObjectLegalHold"],
				Condition={"Null": {"s3:object-lock-legal-hold": "true"}},
			),
		]
	if args.archive_admin_principal:
		admin_denied = list(ARCHIVE_ADMIN_DENIED)
		if args.archive_admin_bypass == "denied":
			admin_denied.append("s3:BypassGovernanceRetention")
		statements.append(deny("ArchiveAdminLimits", args.archive_admin_principal, admin_denied))
	return {"Version": "2012-10-17", "Statement": statements}


def parse_args():
	parser = argparse.ArgumentParser(
		description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
	)
	parser.add_argument("--endpoint", required=True)
	parser.add_argument("--region", required=True)
	parser.add_argument("--bucket", required=True)
	parser.add_argument("--profile", help="AWS profile of the admin key")
	parser.add_argument("--admin-principal", required=True)
	parser.add_argument("--writer-principal", required=True)
	parser.add_argument("--archive-admin-principal")
	parser.add_argument(
		"--writer-legal-hold",
		choices=("deny", "on-only"),
		default="deny",
		help="deny: the writer cannot touch legal holds. on-only: it may set one but never release it "
		"(Ceph then only allows it at upload)",
	)
	parser.add_argument(
		"--archive-admin-bypass",
		choices=("allowed", "denied"),
		default="allowed",
		help="whether the archive admin may delete GOVERNANCE versions before they expire",
	)
	parser.add_argument("--skip-admin-check", action="store_true")
	parser.add_argument("--apply", action="store_true", help="set the policy instead of only printing it")
	return parser.parse_args()


def main():
	args = parse_args()
	if len({args.admin_principal, args.writer_principal, args.archive_admin_principal} - {None}) < (
		3 if args.archive_admin_principal else 2
	):
		sys.exit("The admin, writer and archive admin principals must differ.")
	policy = build_policy(args)
	print(json.dumps(policy, indent=2))
	if not args.apply:
		print("\nNot applied. Pass --apply to set this policy.", file=sys.stderr)
		return

	session = boto3.Session(profile_name=args.profile)
	access_key = session.get_credentials().access_key
	if access_key not in args.admin_principal and not args.skip_admin_check:
		sys.exit(
			"The profile's access key ID is not part of --admin-principal. Refusing: the policy could "
			"lock this key out of the bucket."
		)
	s3 = session.client(
		"s3",
		endpoint_url=args.endpoint,
		region_name=args.region,
		config=Config(s3={"addressing_style": "path"}, request_checksum_calculation="when_required"),
	)
	s3.put_bucket_policy(Bucket=args.bucket, Policy=json.dumps(policy))
	time.sleep(10)
	stored = json.loads(s3.get_bucket_policy(Bucket=args.bucket)["Policy"])
	if [s.get("Sid") for s in stored["Statement"]] != [s["Sid"] for s in policy["Statement"]]:
		sys.exit("The stored policy differs from the one just set.")
	print(f"\nApplied to {args.bucket}; the admin key can still read the policy.", file=sys.stderr)


if __name__ == "__main__":
	main()
