import hashlib
import json
from datetime import UTC, datetime
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, now_datetime

from docarchive import run, store
from docarchive.tests.factories import (
	fields_target,
	make_business_document,
	make_document_type,
	make_own_company,
	make_party,
	make_settings,
	party_fields,
)
from docarchive.tests.fake_s3 import LockedFakeS3

DOCUMENT = "Business Document"


class ArchiveRunTestCase(IntegrationTestCase):
	def setUp(self):
		self.enterContext(patch.object(frappe.db, "commit"))
		self.company = make_own_company().name
		self.type = make_document_type(default_retention_years=8)
		self.party = make_party()
		self.fakes = {}
		frappe.db.delete(DOCUMENT)
		frappe.db.delete("Archive Copy")
		self.enterContext(patch("docarchive.store._client_for", side_effect=self.fake_for))
		self.configure(["primary", "secondary"])

	def fake_for(self, config):
		return self.fakes.setdefault(config["bucket"], LockedFakeS3())

	def configure(self, names, enabled=1, **modes):
		frappe.db.delete(DOCUMENT, {"archive_status": ("!=", "Archived")})
		make_settings(
			enabled=enabled,
			default_own_company=self.company,
			targets=[fields_target(name, lock_mode=modes.get(name, "GOVERNANCE")) for name in names],
		)

	def fake(self, name):
		return self.fakes.setdefault(f"archive-{name}", LockedFakeS3())

	def document(self, age_hours=48, **fields):
		doc = make_business_document(
			**{"document_type": self.type.name, **party_fields(self.party), **fields}
		)
		old = add_to_date(now_datetime(), hours=-age_hours)
		frappe.db.set_value(DOCUMENT, doc.name, "modified", old, update_modified=False)
		return frappe.get_doc(DOCUMENT, doc.name)

	def puts(self):
		return [put for fake in self.fakes.values() for put in fake.puts()]


class TestEligibility(ArchiveRunTestCase):
	def test_disabled_run_does_nothing(self):
		self.configure(["primary"], enabled=0)
		self.document()
		self.assertEqual(run.nightly(), {"status": "disabled"})
		self.assertEqual(self.puts(), [])

	def test_without_targets_does_nothing(self):
		with patch("docarchive.store.targets", return_value=None):
			self.assertEqual(run.nightly(), {"status": "not configured"})

	def test_eligibility(self):
		ready = self.document()
		young = self.document(age_hours=1)
		incomplete = self.document(counterparty_type=None, counterparty=None)
		no_archive = make_document_type(archive=0)
		other = self.document(document_type=no_archive.name)
		eligible = run.eligible()
		self.assertIn(ready.name, eligible)
		for doc in (young, incomplete, other):
			self.assertNotIn(doc.name, eligible)

	def test_min_age_is_configurable(self):
		young = self.document(age_hours=1)
		frappe.db.set_single_value("Archive Settings", "min_age_hours", 0)
		frappe.clear_document_cache("Archive Settings", "Archive Settings")
		self.assertIn(young.name, run.eligible())

	def test_started_documents_are_retried_regardless_of_age(self):
		doc = self.document()
		with patch("docarchive.store.read_sha256", side_effect=RuntimeError):
			run.nightly()
		frappe.db.set_value(DOCUMENT, doc.name, "modified", now_datetime(), update_modified=False)
		self.assertIn(doc.name, run.eligible())

	def test_dry_run_writes_nothing(self):
		self.configure(["primary"], enabled=0)
		doc = self.document()
		result = run.nightly(dry_run=True)
		self.assertEqual(result["status"], "dry run")
		self.assertIn({"name": doc.name, "object_key": run.object_key(doc, "pdf")}, result["documents"])
		self.assertEqual(self.puts(), [])
		self.assertIsNone(frappe.db.get_value(DOCUMENT, doc.name, "archive_started_at"))

	def test_missing_retention_marks_failed_without_start(self):
		no_years = make_document_type(default_retention_years=0)
		doc = self.document(document_type=no_years.name)
		self.assertEqual(run.archive_document(doc.name, store.targets()), "Failed")
		doc.reload()
		self.assertEqual((doc.archive_status, doc.archive_started_at), ("Failed", None))
		self.assertIn("retention rule", doc.archive_error)
		self.assertEqual(self.puts(), [])

	def test_schedule_enqueues_deduplicated(self):
		with patch("frappe.enqueue") as enqueue:
			run.schedule()
		enqueue.assert_called_once_with(
			"docarchive.run.nightly", queue="long", job_id="docarchive-nightly", deduplicate=True
		)
		hooks = frappe.get_hooks("scheduler_events")["cron"]["30 2 * * *"]
		self.assertIn("docarchive.run.schedule", hooks)

	def test_nightly_reassigns_before_archiving(self):
		party = make_party()
		doc = self.document(counterparty_type=None, counterparty=None)
		doc.flags.archive_write = True
		doc.source_system = "Billing"
		doc.source_party_id = party.name
		doc.save(ignore_permissions=True)
		with self.patch_hooks({"docarchive_assign": ["docarchive.tests.test_run.assign_contact"]}):
			run.reassign()
		self.assertEqual(frappe.db.get_value(DOCUMENT, doc.name, "counterparty"), party.name)


def assign_contact(doc):
	if doc.source_system == "Billing" and not doc.counterparty:
		doc.counterparty_type, doc.counterparty = "Contact", doc.source_party_id


class TestArchiveDocument(ArchiveRunTestCase):
	def archive(self, doc):
		return run.archive_document(doc.name, store.targets())

	def test_archives_file_and_meta_to_every_target(self):
		for names in (["primary"], ["primary", "secondary"], ["primary", "secondary", "third"]):
			with self.subTest(targets=len(names)):
				self.fakes.clear()
				self.configure(names)
				doc = self.document()
				self.assertEqual(self.archive(doc), "Archived")
				doc.reload()
				until = datetime(2035, 1, 2, tzinfo=UTC)
				puts = self.puts()
				self.assertEqual(len(puts), 2 * len(names))
				for put in puts:
					self.assertEqual(put["ObjectLockMode"], "GOVERNANCE")
					self.assertEqual(put["ObjectLockRetainUntilDate"], until)
					self.assertTrue(put["ContentMD5"])
					self.assertEqual(put["Metadata"]["sha256"], hashlib.sha256(put["Body"]).hexdigest())
				self.assertEqual(
					sorted((p["Bucket"], p["Key"]) for p in puts),
					sorted((f"archive-{n}", k) for n in names for k in (doc.object_key, doc.meta_key)),
				)

	def test_success_records_archive_fields(self):
		doc = self.document()
		self.archive(doc)
		doc.reload()
		type_slug = self.type.slug
		self.assertEqual(doc.archive_status, "Archived")
		self.assertTrue(doc.archive_started_at and doc.archived_at)
		self.assertEqual(doc.archive_sha256, doc.content_sha256)
		self.assertEqual(
			doc.object_key,
			f"{self.company}/2026/{type_slug}/{self.party.name.lower()}/2026-03-15_inv-2026-017_{doc.name.lower()}.pdf",
		)
		self.assertEqual(doc.meta_key, doc.object_key + ".meta.json")
		self.assertEqual((doc.retention_mode, str(doc.retain_until)), ("GOVERNANCE", "2035-01-02 00:00:00"))
		self.assertEqual(
			[(c.target_name, c.status, c.lock_mode) for c in doc.archive_copies],
			[("primary", "Archived", "GOVERNANCE"), ("secondary", "Archived", "GOVERNANCE")],
		)
		self.assertEqual(
			self.fake("primary").body("archive-primary", doc.object_key),
			frappe.get_doc("File", {"file_url": doc.file}).get_content(),
		)

	def test_meta_json_content(self):
		doc = self.document(source_system="Billing", source_party_id="7", archive_write=True)
		self.archive(doc)
		doc.reload()
		meta = json.loads(self.fake("secondary").body("archive-secondary", doc.meta_key))
		self.assertEqual(meta["document"]["id"], doc.name)
		self.assertEqual(meta["file"]["sha256"], doc.archive_sha256)
		self.assertEqual(meta["file"]["size"], doc.archive_size)
		self.assertEqual(meta["counterparty"]["id"], self.party.name)
		self.assertEqual((meta["source"]["system"], meta["source"]["party_id"]), ("Billing", "7"))
		self.assertEqual(
			meta["retention"],
			{
				"counts_from": "Document Date",
				"start_date": "2026-03-15",
				"years": 8,
				"retain_until": "2035-01-02T00:00:00Z",
				"mode": "GOVERNANCE",
			},
		)
		self.assertTrue(meta["archived_at"].endswith("Z"))

	def test_bytes_and_extension_come_from_content_for(self):
		doc = self.document()
		data = run.content.read_bytes(doc.file)
		with patch("docarchive.content.content_for", return_value=(data, "eml")) as content_for:
			self.assertEqual(self.archive(doc), "Archived")
		content_for.assert_called()
		doc.reload()
		self.assertTrue(doc.object_key.endswith(".eml"))

	def test_meta_records_source_record(self):
		party = make_party()
		doc = self.document(archive_write=True, source_doctype="Contact", source_name=party.name)
		self.archive(doc)
		doc.reload()
		meta = json.loads(self.fake("primary").body("archive-primary", doc.meta_key))
		self.assertEqual((meta["source"]["doctype"], meta["source"]["name"]), ("Contact", party.name))

	def test_mode_follows_target(self):
		self.archive(self.document())
		self.assertEqual({p["ObjectLockMode"] for p in self.puts()}, {"GOVERNANCE"})
		self.fakes.clear()
		self.configure(["primary", "secondary"], primary="COMPLIANCE", secondary="COMPLIANCE")
		trial = self.document()
		self.archive(trial)
		self.assertEqual({p["ObjectLockMode"] for p in self.puts()}, {"COMPLIANCE"})
		meta = json.loads(
			self.fake("primary").body("archive-primary", frappe.get_doc(DOCUMENT, trial.name).meta_key)
		)
		self.assertEqual(meta["retention"]["mode"], "COMPLIANCE")
		self.assertEqual(frappe.db.get_value(DOCUMENT, trial.name, "retention_mode"), "COMPLIANCE")

	def test_invalid_retention_fails_before_any_put(self):
		cases = {
			"past": {"document_date": "2010-03-15"},
		}
		for case, fields in cases.items():
			with self.subTest(case):
				doc = self.document(**fields)
				self.assertEqual(self.archive(doc), "Failed")
				doc.reload()
				self.assertEqual((doc.archive_status, doc.archive_started_at), ("Failed", None))
		doc = self.document()
		with patch("docarchive.retention.retain_until", return_value=datetime(2035, 1, 1)):
			self.assertEqual(self.archive(doc), "Failed")
		self.assertEqual(self.puts(), [])

	def test_stored_retain_until_is_validated_before_put(self):
		doc = self.document()
		with patch("docarchive.store.head", side_effect=RuntimeError):
			self.archive(doc)
		for value in ("2010-01-01 00:00:00", None):
			with self.subTest(value=value):
				frappe.db.set_value("Archive Copy", {"parent": doc.name}, "retain_until", value)
				self.assertEqual(self.archive(doc), "Failed")
		self.assertEqual(self.puts(), [])

	def test_invalid_target_mode_fails_before_any_put(self):
		doc = self.document()
		targets = store.targets()
		targets[1].lock_mode = "NONE"
		self.assertEqual(run.archive_document(doc.name, targets), "Failed")
		self.assertEqual(self.puts(), [])
		self.assertIsNone(frappe.db.get_value(DOCUMENT, doc.name, "archive_started_at"))

	def test_archive_started_before_first_put(self):
		doc = self.document()
		seen = []

		def check(*args, **kwargs):
			seen.append(frappe.db.get_value(DOCUMENT, doc.name, "archive_started_at"))
			return original(*args, **kwargs)

		original = store.put_locked
		with patch("docarchive.store.put_locked", side_effect=check):
			self.archive(doc)
		self.assertEqual(len(seen), 4)
		self.assertTrue(all(seen))

	def test_partial_failure_is_retried_without_rewriting(self):
		doc = self.document()
		self.fakes["archive-secondary"] = LockedFakeS3(fail={"put_object"})
		self.assertEqual(self.archive(doc), "Failed")
		doc.reload()
		self.assertEqual(doc.archive_status, "Failed")
		self.assertEqual(doc.archive_attempts, 1)
		self.assertEqual([c.status for c in doc.archive_copies], ["Archived", "Failed"])
		self.assertIn("secondary: Storage error InternalError", doc.archive_error)
		self.assertEqual(len(self.fake("primary").puts()), 2)

		self.fake("secondary").fail.clear()
		self.assertEqual(self.archive(doc), "Archived")
		doc.reload()
		self.assertEqual((doc.archive_status, doc.archive_error), ("Archived", None))
		self.assertEqual(len(self.fake("primary").puts()), 2)
		self.assertEqual(len(self.fake("secondary").versions), 2)

	def test_existing_object_with_same_sha_is_skipped(self):
		doc = self.document()
		self.fakes["archive-secondary"] = LockedFakeS3(fail={"get_object"})
		self.assertEqual(self.archive(doc), "Failed")
		self.assertEqual(len(self.fake("secondary").puts()), 1)
		self.fake("secondary").fail.clear()
		self.assertEqual(self.archive(doc), "Archived")
		doc.reload()
		self.assertEqual([p["Key"] for p in self.fake("secondary").puts()], [doc.object_key, doc.meta_key])
		self.assertTrue(all(len(v) == 1 for v in self.fake("secondary").versions.values()))

	def test_existing_object_must_carry_the_expected_lock(self):
		doc = self.document()
		with patch("docarchive.store.head", side_effect=RuntimeError):
			self.archive(doc)
		doc.reload()
		data = run.content.read_bytes(doc.file)
		cases = {
			"wrong mode": ("COMPLIANCE", datetime(2035, 1, 2, tzinfo=UTC)),
			"shorter date": ("GOVERNANCE", datetime(2030, 1, 1, tzinfo=UTC)),
		}
		for case, (mode, until) in cases.items():
			with self.subTest(case):
				self.fakes.clear()
				store.put_locked(store.targets()[0], doc.object_key, data, mode, until)
				self.assertEqual(self.archive(doc), "Failed")
				self.assertIn(
					"is not locked as expected", frappe.db.get_value(DOCUMENT, doc.name, "archive_error")
				)

	def test_versions_are_recorded_and_used(self):
		doc = self.document()
		self.archive(doc)
		doc.reload()
		for copy in doc.archive_copies:
			fake = self.fake(copy.target_name)
			versions = {
				key: fake.latest(f"archive-{copy.target_name}", key)["VersionId"]
				for key in (doc.object_key, doc.meta_key)
			}
			self.assertEqual(
				(copy.object_version_id, copy.meta_version_id),
				(versions[doc.object_key], versions[doc.meta_key]),
			)
			reads = [kw for op, kw in fake.calls if op == "get_object"]
			self.assertEqual({kw["VersionId"] for kw in reads}, set(versions.values()))

	def test_new_version_under_the_key_is_an_error(self):
		doc = self.document()
		self.fake("primary").fail.add("get_object")
		self.archive(doc)
		doc.reload()
		fake = self.fake("primary")
		fake.fail.clear()
		[version] = fake.versions[("archive-primary", doc.object_key)]
		fake.versions[("archive-primary", doc.object_key)].append({**version, "VersionId": "other"})
		self.assertEqual(self.archive(doc), "Failed")
		self.assertIn("has a new version", frappe.db.get_value(DOCUMENT, doc.name, "archive_error"))

	def test_concurrent_runs_do_not_double_write(self):
		doc = self.document()
		lock = run.document_lock(doc.name)
		self.assertTrue(lock.acquire(blocking=False))
		try:
			self.assertEqual(self.archive(doc), "Pending")
		finally:
			lock.release()
		self.assertEqual(self.puts(), [])
		self.assertEqual(self.archive(doc), "Archived")

	def test_sha_mismatch_is_a_hard_error(self):
		doc = self.document()
		with patch("docarchive.store.head", side_effect=RuntimeError):
			self.archive(doc)
		doc.reload()
		target = store.targets()[0]
		store.put_locked(
			target, doc.object_key, b"other bytes", "GOVERNANCE", datetime(2035, 1, 2, tzinfo=UTC)
		)
		before = len(self.puts())
		self.assertEqual(self.archive(doc), "Failed")
		doc.reload()
		self.assertIn("already exists with different content", doc.archive_error)
		self.assertEqual(len(self.puts()), before + 2)
		self.assertEqual(self.fake("primary").body("archive-primary", doc.object_key), b"other bytes")
		self.assertTrue(frappe.db.exists("Error Log", {"method": ("like", f"%{doc.name}%")}))

	def test_verify_mismatch_marks_failed(self):
		doc = self.document()
		with patch("docarchive.store.read_sha256", return_value="0" * 64):
			self.assertEqual(self.archive(doc), "Failed")
		doc.reload()
		self.assertIn("does not match its checksum", doc.archive_error)

	def test_changed_file_bytes_fail(self):
		doc = self.document()
		with patch("docarchive.content.read_bytes", return_value=b"tampered"):
			self.assertEqual(self.archive(doc), "Failed")
		self.assertEqual(self.puts(), [])

	def test_new_target_gets_a_copy_later(self):
		self.configure(["primary"])
		doc = self.document()
		self.archive(doc)
		self.configure(["primary", "secondary"])
		self.assertIn(doc.name, run.eligible({"primary", "secondary"}))
		self.assertNotIn(doc.name, run.eligible({"primary"}))
		run.nightly()
		doc.reload()
		self.assertEqual([c.target_name for c in doc.archive_copies], ["primary", "secondary"])
		self.assertEqual(len(self.fake("primary").puts()), 2)
		self.assertEqual(doc.archive_status, "Archived")

	def test_one_failure_does_not_stop_others(self):
		broken = self.document()
		ok = self.document()
		with patch(
			"docarchive.content.read_bytes",
			side_effect=lambda url: None if url == broken.file else original(url),
		):
			result = run.nightly()
		self.assertEqual(result["failed"], [broken.name])
		self.assertEqual(frappe.db.get_value(DOCUMENT, ok.name, "archive_status"), "Archived")

	def test_error_message_has_no_secrets(self):
		doc = self.document()
		self.fakes["archive-primary"] = LockedFakeS3(fail={"head_object"})
		self.archive(doc)
		error = frappe.db.get_value(DOCUMENT, doc.name, "archive_error")
		for secret in ("secret", "key", "https://", "s3.example.test"):
			self.assertNotIn(secret, error)

	def test_archived_document_is_locked(self):
		doc = self.document()
		self.archive(doc)
		doc.reload()
		doc.document_number = "changed"
		with self.assertRaises(frappe.ValidationError):
			doc.save(ignore_permissions=True)
		doc.reload()
		doc.archive_copies[0].status = "Failed"
		doc.archive_meta = "{}"
		doc.save(ignore_permissions=True)
		doc.reload()
		self.assertEqual(doc.archive_copies[0].status, "Archived")
		self.assertNotEqual(doc.archive_meta, "{}")


original = run.content.read_bytes


class TestSmoke(ArchiveRunTestCase):
	def test_smoke_writes_one_day_governance_object_and_delete_is_refused(self):
		result = run.smoke("primary")
		self.assertTrue(result["ok"], result)
		[put] = self.fake("primary").puts()
		self.assertTrue(put["Key"].startswith("_smoke/"))
		self.assertEqual(put["ObjectLockMode"], "GOVERNANCE")
		hours = (put["ObjectLockRetainUntilDate"] - datetime.now(UTC)).total_seconds() / 3600
		self.assertTrue(23 < hours <= 24)
		self.assertEqual(len(self.fake("primary").versions[("archive-primary", put["Key"])]), 1)

	def test_smoke_reports_a_delete_that_succeeds(self):
		fake = self.fake("primary")
		fake.delete_object = lambda **kwargs: {}
		result = run.smoke("primary")
		self.assertFalse(result["ok"])
		self.assertFalse(result["checks"]["delete_refused"])

	def test_smoke_tries_a_bypass_delete(self):
		run.smoke("primary")
		calls = [kw for op, kw in self.fake("primary").calls if op == "delete_object"]
		self.assertEqual([kw["Bypass"] for kw in calls], [False, True])
		self.assertTrue(all(kw["VersionId"] for kw in calls))

	def test_smoke_reports_a_bypass_that_works(self):
		self.fakes["archive-primary"] = LockedFakeS3(bypass_allowed=True)
		result = run.smoke("primary")
		self.assertFalse(result["ok"])
		self.assertTrue(result["checks"]["delete_refused"])
		self.assertFalse(result["checks"]["bypass_delete_refused"])
		self.assertFalse(result["checks"]["still_there"])

	def test_smoke_other_errors_are_not_a_refusal(self):
		from docarchive.tests.fake_s3 import error

		def broken(**kwargs):
			raise error("InternalError", "DeleteObject")

		self.fake("primary").delete_object = broken
		result = run.smoke("primary")
		self.assertFalse(result["checks"]["delete_refused"])
		self.assertFalse(result["ok"])

	def test_smoke_unknown_target(self):
		with self.assertRaises(frappe.ValidationError):
			run.smoke("nope")
