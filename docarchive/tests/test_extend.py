import hashlib
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import frappe

from docarchive import run, store
from docarchive.tests.factories import make_document_type
from docarchive.tests.fake_s3 import LockedFakeS3
from docarchive.tests.test_run import DOCUMENT, ArchiveRunTestCase
from docarchive.tests.test_store import fake_target

INITIAL = datetime(2037, 1, 2, tzinfo=UTC)


SHA_X = hashlib.sha256(b"x").hexdigest()


class TestExtendLocked(ArchiveRunTestCase):
	def locked(self, mode="GOVERNANCE", until=INITIAL):
		target = fake_target()
		version = store.put_locked(target, "k", b"x", mode, until)["version_id"]
		return target, version

	def retention_calls(self, target):
		return [kwargs for op, kwargs in target._client.calls if op == "put_object_retention"]

	def test_extends_the_given_version_to_a_later_date(self):
		target, version = self.locked()
		later = INITIAL + timedelta(days=365)
		self.assertTrue(store.extend_locked(target, "k", version, SHA_X, "GOVERNANCE", later))
		[call] = self.retention_calls(target)
		self.assertEqual(call["Retention"], {"Mode": "GOVERNANCE", "RetainUntilDate": later})
		self.assertEqual(call["VersionId"], version)
		self.assertEqual(store.retention(target, "k", version)["retain_until"], later)

	def test_never_shortens(self):
		target, version = self.locked()
		for until in (INITIAL, INITIAL - timedelta(days=1)):
			with self.subTest(until=until):
				self.assertFalse(store.extend_locked(target, "k", version, SHA_X, "GOVERNANCE", until))
		self.assertEqual(self.retention_calls(target), [])

	def test_refuses_invalid_dates_and_modes(self):
		target, version = self.locked()
		cases = {
			"naive": ("GOVERNANCE", datetime(2040, 1, 1)),
			"past": ("GOVERNANCE", datetime(2020, 1, 1, tzinfo=UTC)),
			"unparseable": ("GOVERNANCE", "soon"),
			"missing": ("GOVERNANCE", None),
			"bad mode": ("NONE", datetime(2040, 1, 2, tzinfo=UTC)),
			"other mode": ("COMPLIANCE", datetime(2040, 1, 2, tzinfo=UTC)),
		}
		for case, (mode, until) in cases.items():
			with self.subTest(case), self.assertRaises(store.LockError):
				store.extend_locked(target, "k", version, SHA_X, mode, until)
		self.assertEqual(self.retention_calls(target), [])

	def test_refuses_a_version_with_other_content(self):
		target, version = self.locked()
		later = INITIAL + timedelta(days=1)
		for case, (version_id, sha) in {"sha": (version, "0" * 64), "no version": (None, SHA_X)}.items():
			with self.subTest(case), self.assertRaises(frappe.ValidationError):
				store.extend_locked(target, "k", version_id, sha, "GOVERNANCE", later)
		target._client.versions[("archive-primary", "k")][0]["Body"] = b"tampered"
		with self.assertRaises(store.VerifyError):
			store.extend_locked(target, "k", version, SHA_X, "GOVERNANCE", later)
		self.assertEqual(self.retention_calls(target), [])


class TestContractExtension(ArchiveRunTestCase):
	def setUp(self):
		super().setUp()
		self.type = make_document_type(retention_from="Contract End Date", default_retention_years=10)

	def archived(self, **fields):
		doc = self.document(**fields)
		self.assertEqual(run.archive_document(doc.name, store.targets()), "Archived")
		doc.reload()
		return doc

	def move_end(self, doc, value):
		doc.contract_end_date = value
		doc.save(ignore_permissions=True)

	def retention_calls(self):
		return [
			(bucket, kwargs["Key"], kwargs["Retention"])
			for bucket, fake in self.fakes.items()
			for op, kwargs in fake.calls
			if op == "put_object_retention"
		]

	def test_later_end_date_extends_every_target_and_both_objects(self):
		for names in (["primary"], ["primary", "secondary", "third"]):
			with self.subTest(targets=len(names)):
				self.fakes.clear()
				self.configure(names)
				doc = self.archived(contract_end_date="2026-12-31")
				self.assertEqual(str(doc.retain_until), "2037-01-02 00:00:00")
				self.move_end(doc, "2030-06-30")
				result = run.nightly()
				self.assertEqual(result["extended"], 1)
				until = datetime(2041, 1, 2, tzinfo=UTC)
				self.assertEqual(
					sorted(self.retention_calls(), key=str),
					sorted(
						(
							(f"archive-{n}", key, {"Mode": "GOVERNANCE", "RetainUntilDate": until})
							for n in names
							for key in (doc.object_key, doc.meta_key)
						),
						key=str,
					),
				)
				doc.reload()
				self.assertEqual(str(doc.retain_until), "2041-01-02 00:00:00")
				self.assertEqual({str(c.retain_until) for c in doc.archive_copies}, {"2041-01-02 00:00:00"})
				self.assertEqual(doc.archive_status, "Archived")

	def test_first_end_date_extends(self):
		doc = self.archived()
		self.assertEqual(str(doc.retain_until), "2037-01-02 00:00:00")
		self.move_end(doc, "2031-03-31")
		run.nightly()
		doc.reload()
		self.assertEqual(str(doc.retain_until), "2042-01-02 00:00:00")

	def test_nothing_to_extend_makes_no_call(self):
		self.archived(contract_end_date="2026-12-31")
		run.nightly()
		self.assertEqual(self.retention_calls(), [])

	def test_never_shortens_even_if_the_rule_drops(self):
		doc = self.archived(contract_end_date="2030-06-30")
		frappe.db.set_value("Business Document Type", self.type.name, "default_retention_years", 1)
		run.nightly()
		self.assertEqual(self.retention_calls(), [])
		doc.reload()
		self.assertEqual(str(doc.retain_until), "2041-01-02 00:00:00")

	def test_failed_target_is_retried(self):
		doc = self.archived(contract_end_date="2026-12-31")
		self.move_end(doc, "2030-06-30")
		self.fake("secondary").fail.add("put_object_retention")
		result = run.nightly()
		self.assertEqual(result["extension_failed"], [doc.name])
		doc.reload()
		self.assertEqual(str(doc.retain_until), "2037-01-02 00:00:00")
		copies = {c.target_name: c for c in doc.archive_copies}
		self.assertEqual(str(copies["primary"].retain_until), "2041-01-02 00:00:00")
		self.assertEqual(str(copies["secondary"].retain_until), "2037-01-02 00:00:00")
		self.assertIn("InternalError", copies["secondary"].error)

		self.fake("secondary").fail.clear()
		before = len(self.retention_calls())
		self.assertEqual(run.nightly()["extended"], 1)
		self.assertEqual(
			[call[0] for call in self.retention_calls()[before:]], ["archive-secondary", "archive-secondary"]
		)
		doc.reload()
		self.assertEqual(str(doc.retain_until), "2041-01-02 00:00:00")

	def test_stale_mode_is_refused(self):
		doc = self.archived(contract_end_date="2026-12-31")
		fake = self.fake("primary")
		for key in (doc.object_key, doc.meta_key):
			fake.put_object_retention(
				Bucket="archive-primary",
				Key=key,
				Retention={"Mode": "COMPLIANCE", "RetainUntilDate": datetime(2037, 1, 2, tzinfo=UTC)},
				BypassGovernanceRetention=True,
			)
		fake.calls.clear()
		self.move_end(doc, "2030-06-30")
		run.nightly()
		doc.reload()
		primary = next(c for c in doc.archive_copies if c.target_name == "primary")
		self.assertIn("docarchive-sync-mode", primary.error)
		self.assertEqual([op for op, _kw in fake.calls if op == "put_object_retention"], [])

		self.assertEqual(run.sync_mode("primary")["updated"], [doc.name])
		run.nightly()
		doc.reload()
		self.assertEqual(str(doc.retain_until), "2041-01-02 00:00:00")
		self.assertEqual(doc.retention_mode, "COMPLIANCE, GOVERNANCE")

	def test_extension_writes_a_locked_sidecar_per_target(self):
		doc = self.archived(contract_end_date="2026-12-31")
		self.move_end(doc, "2030-06-30")
		run.nightly()
		doc.reload()
		sidecar = doc.object_key + ".meta.2.json"
		until = datetime(2041, 1, 2, tzinfo=UTC)
		for name in ("primary", "secondary"):
			fake = self.fake(name)
			[put] = [p for p in fake.puts() if p["Key"] == sidecar]
			self.assertEqual((put["ObjectLockMode"], put["ObjectLockRetainUntilDate"]), ("GOVERNANCE", until))
			body = json.loads(put["Body"])
			self.assertEqual(body["retention"]["retain_until"], "2041-01-02T00:00:00Z")
			self.assertEqual(body["extension"]["previous_retain_until"], "2037-01-02T00:00:00Z")
			self.assertEqual(body["document"]["contract_end_date"], "2030-06-30")
			self.assertEqual(len(fake.versions[(f"archive-{name}", doc.meta_key)]), 1)
		self.assertEqual((doc.retention_extensions, doc.extension_until, doc.extension_meta), (1, None, None))
		self.assertEqual(len(json.loads(doc.retention_history)), 1)

		self.move_end(doc, "2032-06-30")
		run.nightly()
		doc.reload()
		[put] = [p for p in self.fake("primary").puts() if p["Key"] == doc.object_key + ".meta.3.json"]
		body = json.loads(put["Body"])
		self.assertEqual([h["index"] for h in body["history"]], [2])
		self.assertEqual(str(doc.retain_until), "2043-01-02 00:00:00")

	def test_sidecar_is_retried_with_the_same_bytes(self):
		doc = self.archived(contract_end_date="2026-12-31")
		self.move_end(doc, "2030-06-30")
		self.fake("secondary").fail.add("put_object")
		run.nightly()
		self.move_end(frappe.get_doc(DOCUMENT, doc.name), "2031-06-30")
		self.fake("secondary").fail.clear()
		run.nightly()
		doc.reload()
		sidecar = doc.object_key + ".meta.2.json"
		bodies = {name: self.fake(name).body(f"archive-{name}", sidecar) for name in ("primary", "secondary")}
		self.assertEqual(bodies["primary"], bodies["secondary"])
		self.assertEqual(str(doc.retain_until), "2041-01-02 00:00:00")
		run.nightly()
		doc.reload()
		self.assertEqual(str(doc.retain_until), "2042-01-02 00:00:00")

	def test_overflow_is_skipped_per_document(self):
		broken = self.archived(contract_end_date="2026-12-31")
		frappe.db.set_value(DOCUMENT, broken.name, "archive_meta", json.dumps({"retention": {"years": 9000}}))
		ok = self.archived(contract_end_date="2026-12-31")
		self.move_end(ok, "2030-06-30")
		self.assertEqual([doc.name for doc, _until in run.extensions({"primary", "secondary"})], [ok.name])

	def test_failing_new_target_does_not_block_the_extension(self):
		doc = self.archived(contract_end_date="2026-12-31")
		self.configure(["primary", "secondary", "third"])
		self.fakes["archive-third"] = LockedFakeS3(fail={"put_object"})
		self.move_end(doc, "2030-06-30")
		run.nightly()
		doc.reload()
		self.assertEqual(doc.archive_status, "Failed")
		copies = {c.target_name: c for c in doc.archive_copies}
		for name in ("primary", "secondary"):
			self.assertEqual(str(copies[name].retain_until), "2041-01-02 00:00:00")
		self.assertEqual(copies["third"].status, "Failed")
		self.assertEqual(str(doc.retain_until), "2041-01-02 00:00:00")

	def test_invoices_are_never_extended(self):
		self.type = make_document_type(default_retention_years=8)
		self.archived()
		run.nightly()
		self.assertEqual(self.retention_calls(), [])


class TestSyncMode(ArchiveRunTestCase):
	def switch(self, doc, keys, target="primary"):
		for key in keys:
			self.fake(target).put_object_retention(
				Bucket=f"archive-{target}",
				Key=key,
				Retention={"Mode": "COMPLIANCE", "RetainUntilDate": datetime(2035, 1, 2, tzinfo=UTC)},
				BypassGovernanceRetention=True,
			)

	def test_records_the_mode_after_the_switch(self):
		doc = self.document()
		run.archive_document(doc.name, store.targets())
		doc.reload()
		self.switch(doc, (doc.object_key, doc.meta_key))
		result = run.sync_mode("primary")
		self.assertEqual((result["checked"], result["updated"], result["mismatched"]), (1, [doc.name], []))
		doc.reload()
		modes = {c.target_name: c.lock_mode for c in doc.archive_copies}
		self.assertEqual(modes, {"primary": "COMPLIANCE", "secondary": "GOVERNANCE"})
		self.assertEqual(doc.retention_mode, "COMPLIANCE, GOVERNANCE")
		self.assertEqual(run.sync_mode("primary")["updated"], [])

	def test_needs_both_objects(self):
		doc = self.document()
		run.archive_document(doc.name, store.targets())
		doc.reload()
		self.switch(doc, (doc.object_key,))
		result = run.sync_mode("primary")
		self.assertEqual((result["updated"], result["mismatched"]), ([], [doc.name]))
		self.assertEqual(frappe.db.get_value(DOCUMENT, doc.name, "retention_mode"), "GOVERNANCE")

	def test_never_downgrades(self):
		doc = self.document()
		run.archive_document(doc.name, store.targets())
		doc.reload()
		doc.archive_copies[0].lock_mode = "COMPLIANCE"
		doc.flags.archive_write = True
		doc.save(ignore_permissions=True)
		result = run.sync_mode("primary")
		self.assertEqual((result["updated"], result["mismatched"]), ([], [doc.name]))
		doc.reload()
		self.assertEqual(doc.archive_copies[0].lock_mode, "COMPLIANCE")

	def test_errors_are_reported_per_document(self):
		first = self.document()
		second = self.document()
		for doc in (first, second):
			run.archive_document(doc.name, store.targets())
		calls = iter([RuntimeError("boom")])

		def flaky(*args):
			error = next(calls, None)
			if error:
				raise error
			return original(*args)

		original = store.retention
		with patch("docarchive.store.retention", side_effect=flaky):
			result = run.sync_mode("primary")
		self.assertEqual(len(result["errors"]), 1)
		self.assertEqual(result["checked"], 2)

	def test_new_uploads_use_the_configured_mode(self):
		self.configure(["primary", "secondary"], primary="COMPLIANCE")
		doc = self.document()
		run.archive_document(doc.name, store.targets())
		modes = {p["Bucket"]: p["ObjectLockMode"] for p in self.puts()}
		self.assertEqual(modes, {"archive-primary": "COMPLIANCE", "archive-secondary": "GOVERNANCE"})

	def test_fake_refuses_mode_change_without_bypass(self):
		fake = LockedFakeS3()
		target = store.Target("t", "b", "GOVERNANCE", fake)
		store.put_locked(target, "k", b"x", "GOVERNANCE", datetime(2035, 1, 2, tzinfo=UTC))
		with self.assertRaises(Exception):
			fake.put_object_retention(
				Bucket="b",
				Key="k",
				Retention={"Mode": "COMPLIANCE", "RetainUntilDate": datetime(2036, 1, 2, tzinfo=UTC)},
			)
