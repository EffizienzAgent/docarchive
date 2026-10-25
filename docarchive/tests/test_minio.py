import hashlib
import os
import time
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
		admin_access_key=os.environ.get("DOCARCHIVE_TEST_S3_ACCESS_KEY"),
		admin_secret_key=os.environ.get("DOCARCHIVE_TEST_S3_SECRET_KEY"),
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
		version = store.put_locked(self.targets[0], key, b"x", "GOVERNANCE", until)["version_id"]
		sha = hashlib.sha256(b"x").hexdigest()
		later = until + timedelta(days=1)
		self.assertTrue(store.extend_locked(self.targets[0], key, version, sha, "GOVERNANCE", later))
		self.assertEqual(store.retention(self.targets[0], key, version)["retain_until"], later)
		self.assertFalse(store.extend_locked(self.targets[0], key, version, sha, "GOVERNANCE", until))
		with self.assertRaises(Exception):
			self.s3.put_object_retention(
				Bucket=self.targets[0].bucket,
				Key=key,
				Retention={"Mode": "GOVERNANCE", "RetainUntilDate": until},
			)
		self.assertEqual(store.retention(self.targets[0], key, version)["retain_until"], later)

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

	def short_lock(self, mode="GOVERNANCE", seconds=3):
		key = f"check/{uuid.uuid4().hex}.txt"
		until = datetime.now(UTC) + timedelta(seconds=seconds)
		return key, store.put_locked(self.targets[0], key, b"short", mode, until)["version_id"]

	def test_legal_hold_set_and_release(self):
		target = self.targets[0]
		key = f"check/{uuid.uuid4().hex}.txt"
		version = store.put_locked(target, key, b"held", "GOVERNANCE", self.until())["version_id"]
		self.assertEqual(store.hold_status(target, key, version), "OFF")
		store.set_hold(target, key, version)
		self.assertEqual(store.hold_status(target, key, version), "ON")
		store.release_hold(target, key, version)
		self.assertEqual(store.hold_status(target, key, version), "OFF")

	def test_hold_at_upload(self):
		target = self.targets[0]
		key = f"check/{uuid.uuid4().hex}.txt"
		result = store.put_locked(target, key, b"held", "GOVERNANCE", self.until(), legal_hold=True)
		self.assertEqual(store.hold_status(target, key, result["version_id"]), "ON")
		store.verify(target, key, result["version_id"], result["sha256"], "GOVERNANCE", self.until(), True)
		store.release_hold(target, key, result["version_id"])
		self.assertEqual(store.hold_status(target, key, result["version_id"]), "OFF")

	def test_delete_expired_only_after_expiry_and_without_hold(self):
		target = self.targets[0]
		key, version = self.short_lock()
		other = store.put_locked(target, key, b"newer", "GOVERNANCE", self.until())["version_id"]
		with self.assertRaises(store.LockError):
			store.delete_expired(target, key, version)
		with self.assertRaises(Exception) as caught:
			store._admin_delete(target, key, version)
		self.assertTrue(store.refused(caught.exception))
		store.set_hold(target, key, version)
		time.sleep(4)
		with self.assertRaises(store.LockError):
			store.delete_expired(target, key, version)
		with self.assertRaises(Exception) as caught:
			store._admin_delete(target, key, version)
		self.assertTrue(store.refused(caught.exception))
		store.release_hold(target, key, version)
		self.assertTrue(store.delete_expired(target, key, version))
		self.assertIsNone(store.head(target, key, version))
		self.assertFalse(store.delete_expired(target, key, version))
		self.assertEqual(store.head(target, key, other)["VersionId"], other)

	def test_delete_early_only_for_governance(self):
		target = self.targets[0]
		key = f"check/{uuid.uuid4().hex}.txt"
		version = store.put_locked(target, key, b"early", "GOVERNANCE", self.until(30))["version_id"]
		self.assertTrue(store.delete_early(target, key, version))
		self.assertIsNone(store.head(target, key, version))
		key, version = self.short_lock("COMPLIANCE", seconds=2)
		with self.assertRaises(store.LockError):
			store.delete_early(target, key, version)
		self.assertTrue(store.head(target, key, version))

	def test_document_hold_release_and_early_delete(self):
		from docarchive import expiry, legal_hold

		company = make_own_company().name
		make_settings(default_own_company=company)
		doc = make_business_document(
			document_type=make_document_type(default_retention_years=8).name, **party_fields(make_party())
		)
		self.assertEqual(run.archive_document(doc.name, self.targets), "Archived")
		self.enterContext(patch("docarchive.store.targets", return_value=self.targets))
		self.assertEqual(legal_hold.set_legal_hold([doc.name], "x")[doc.name]["status"], "held")
		doc.reload()
		objects = [
			(t, k, v)
			for t in self.targets
			for c in doc.archive_copies
			if c.target_name == t.name
			for k, v in run.copy_objects(c, doc)
		]
		self.assertEqual({store.hold_status(*o) for o in objects}, {"ON"})
		self.assertEqual(
			legal_hold.release_legal_hold([doc.name], "y", confirm=1)[doc.name]["status"], "released"
		)
		self.assertEqual({store.hold_status(*o) for o in objects}, {"OFF"})
		result = expiry.run_job([doc.name], "Administrator", early=1, reason="z")
		self.assertEqual(result, {doc.name: "Deleted"})
		self.assertEqual([store.head(*o) for o in objects], [None] * len(objects))
		self.assertEqual(frappe.get_doc("Business Document", doc.name).archive_status, "Deleted")
