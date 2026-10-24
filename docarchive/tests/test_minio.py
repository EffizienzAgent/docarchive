import hashlib
import os
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from docarchive import run, store
from docarchive.tests.factories import (
	make_business_document,
	make_document_type,
	make_own_company,
	make_party,
	make_settings,
	party_fields,
)

ENDPOINT = os.environ.get("DOCARCHIVE_TEST_S3_ENDPOINT")


def config(bucket, name):
	return frappe._dict(
		name=name,
		lock_mode="GOVERNANCE",
		endpoint=ENDPOINT,
		region=os.environ.get("DOCARCHIVE_TEST_S3_REGION", "us-east-1"),
		bucket=bucket,
		access_key=os.environ.get("DOCARCHIVE_TEST_S3_ACCESS_KEY"),
		secret_key=os.environ.get("DOCARCHIVE_TEST_S3_SECRET_KEY"),
	)


class TestMinio(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		if not ENDPOINT:
			raise unittest.SkipTest("DOCARCHIVE_TEST_S3_ENDPOINT is not set")
		super().setUpClass()
		suffix = uuid.uuid4().hex[:8]
		cls.configs = [config(f"docarchive-test-{n}-{suffix}", n) for n in ("primary", "secondary")]
		for c in cls.configs:
			store._client_for(c).create_bucket(Bucket=c.bucket, ObjectLockEnabledForBucket=True)

	def setUp(self):
		self.enterContext(patch.object(frappe.db, "commit"))
		self.targets = [store.from_config(c) for c in self.configs]
		self.s3 = self.targets[0]._client

	def until(self, days=1):
		return datetime.now(UTC).replace(microsecond=0) + timedelta(days=days)

	def test_put_locked_sets_retention(self):
		key = f"check/{uuid.uuid4().hex}.txt"
		until = self.until(2)
		result = store.put_locked(self.targets[0], key, b"locked", "GOVERNANCE", until, "text/plain")
		head = store.head(self.targets[0], key)
		self.assertEqual(head["ObjectLockMode"], "GOVERNANCE")
		self.assertEqual(head["ObjectLockRetainUntilDate"], until)
		self.assertEqual(head["Metadata"]["sha256"], hashlib.sha256(b"locked").hexdigest())
		self.assertEqual(store.retention(self.targets[0], key, result["version_id"])["mode"], "GOVERNANCE")
		self.assertEqual(store.read_sha256(self.targets[0], key, result["version_id"]), result["sha256"])

	def test_retention_only_grows(self):
		key = f"check/{uuid.uuid4().hex}.txt"
		until = self.until(2)
		store.put_locked(self.targets[0], key, b"x", "GOVERNANCE", until)
		later = until + timedelta(days=1)
		self.assertTrue(store.extend_locked(self.targets[0], key, "GOVERNANCE", later))
		self.assertEqual(store.retention(self.targets[0], key)["retain_until"], later)
		self.assertFalse(store.extend_locked(self.targets[0], key, "GOVERNANCE", until))
		with self.assertRaises(Exception):
			self.s3.put_object_retention(
				Bucket=self.targets[0].bucket,
				Key=key,
				Retention={"Mode": "GOVERNANCE", "RetainUntilDate": until},
			)
		self.assertEqual(store.retention(self.targets[0], key)["retain_until"], later)

	def test_locked_version_cannot_be_deleted(self):
		key = f"{store.SMOKE_PREFIX}20261025T010203Z-{uuid.uuid4().hex[:8]}.txt"
		result = store.put_locked(self.targets[0], key, b"x", "GOVERNANCE", self.until())
		with self.assertRaises(Exception) as caught:
			store.smoke_delete(self.targets[0], key, result["version_id"])
		self.assertTrue(store.refused(caught.exception))
		self.assertEqual(store.head(self.targets[0], key)["VersionId"], result["version_id"])

	def test_smoke(self):
		make_settings(targets=[])
		settings = frappe.get_single("Archive Settings")
		with patch("docarchive.run.smoke_target", return_value=self.targets[1]):
			result = run.smoke("secondary")
		checks = result["checks"]
		self.assertTrue(
			all(checks[name] for name in ("put", "versioned", "sha256", "retention", "delete_refused"))
		)
		self.assertEqual(checks["still_there"], checks["bypass_delete_refused"])
		self.assertEqual(result["ok"], all(checks.values()))
		self.assertEqual(settings.enabled, 0)

	def test_archive_document_to_two_buckets(self):
		company = make_own_company().name
		make_settings(default_own_company=company)
		doc = make_business_document(
			document_type=make_document_type(default_retention_years=8).name, **party_fields(make_party())
		)
		self.assertEqual(run.archive_document(doc.name, self.targets), "Archived")
		doc.reload()
		for target in self.targets:
			for key in (doc.object_key, doc.meta_key):
				head = store.head(target, key)
				self.assertEqual(head["ObjectLockMode"], "GOVERNANCE")
				self.assertEqual(head["ObjectLockRetainUntilDate"], datetime(2035, 1, 2, tzinfo=UTC))
		copy = next(c for c in doc.archive_copies if c.target_name == "secondary")
		self.assertEqual(
			store.read_sha256(self.targets[1], doc.object_key, copy.object_version_id), doc.content_sha256
		)
		frappe.db.set_value("Archive Copy", {"parent": doc.name}, "status", "Pending")
		self.assertEqual(run.archive_document(doc.name, self.targets), "Archived")
		versions = self.s3.list_object_versions(Bucket=self.targets[0].bucket, Prefix=doc.object_key)
		self.assertEqual(
			sorted(v["Key"] for v in versions["Versions"]), sorted((doc.object_key, doc.meta_key))
		)
