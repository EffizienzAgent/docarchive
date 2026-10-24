from datetime import UTC, datetime, timedelta

import frappe

from docarchive import run, store
from docarchive.tests.factories import make_document_type
from docarchive.tests.fake_s3 import LockedFakeS3
from docarchive.tests.test_run import DOCUMENT, ArchiveRunTestCase
from docarchive.tests.test_store import fake_target

INITIAL = datetime(2037, 1, 1, tzinfo=UTC)


class TestExtendLocked(ArchiveRunTestCase):
	def locked(self, mode="GOVERNANCE", until=INITIAL):
		target = fake_target()
		store.put_locked(target, "k", b"x", mode, until)
		return target

	def retention_calls(self, target):
		return [kwargs for op, kwargs in target._client.calls if op == "put_object_retention"]

	def test_extends_to_a_later_date(self):
		target = self.locked()
		later = INITIAL + timedelta(days=365)
		self.assertTrue(store.extend_locked(target, "k", "GOVERNANCE", later))
		[call] = self.retention_calls(target)
		self.assertEqual(call["Retention"], {"Mode": "GOVERNANCE", "RetainUntilDate": later})
		self.assertEqual(store.retention(target, "k")["retain_until"], later)

	def test_never_shortens(self):
		target = self.locked()
		for until in (INITIAL, INITIAL - timedelta(days=1)):
			with self.subTest(until=until):
				self.assertFalse(store.extend_locked(target, "k", "GOVERNANCE", until))
		self.assertEqual(self.retention_calls(target), [])

	def test_refuses_invalid_dates_and_modes(self):
		target = self.locked()
		cases = {
			"naive": ("GOVERNANCE", datetime(2040, 1, 1)),
			"past": ("GOVERNANCE", datetime(2020, 1, 1, tzinfo=UTC)),
			"unparseable": ("GOVERNANCE", "soon"),
			"missing": ("GOVERNANCE", None),
			"bad mode": ("NONE", datetime(2040, 1, 1, tzinfo=UTC)),
			"other mode": ("COMPLIANCE", datetime(2040, 1, 1, tzinfo=UTC)),
		}
		for case, (mode, until) in cases.items():
			with self.subTest(case), self.assertRaises(store.LockError):
				store.extend_locked(target, "k", mode, until)
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
				self.assertEqual(str(doc.retain_until), "2037-01-01 00:00:00")
				self.move_end(doc, "2030-06-30")
				result = run.nightly()
				self.assertEqual(result["extended"], 1)
				until = datetime(2041, 1, 1, tzinfo=UTC)
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
				self.assertEqual(str(doc.retain_until), "2041-01-01 00:00:00")
				self.assertEqual({str(c.retain_until) for c in doc.archive_copies}, {"2041-01-01 00:00:00"})
				self.assertEqual(doc.archive_status, "Archived")

	def test_first_end_date_extends(self):
		doc = self.archived()
		self.assertEqual(str(doc.retain_until), "2037-01-01 00:00:00")
		self.move_end(doc, "2031-03-31")
		run.nightly()
		doc.reload()
		self.assertEqual(str(doc.retain_until), "2042-01-01 00:00:00")

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
		self.assertEqual(str(doc.retain_until), "2041-01-01 00:00:00")

	def test_failed_target_is_retried(self):
		doc = self.archived(contract_end_date="2026-12-31")
		self.move_end(doc, "2030-06-30")
		self.fake("secondary").fail.add("put_object_retention")
		result = run.nightly()
		self.assertEqual(result["extension_failed"], [doc.name])
		doc.reload()
		self.assertEqual(str(doc.retain_until), "2037-01-01 00:00:00")
		copies = {c.target_name: c for c in doc.archive_copies}
		self.assertEqual(str(copies["primary"].retain_until), "2041-01-01 00:00:00")
		self.assertEqual(str(copies["secondary"].retain_until), "2037-01-01 00:00:00")
		self.assertIn("InternalError", copies["secondary"].error)

		self.fake("secondary").fail.clear()
		before = len(self.retention_calls())
		self.assertEqual(run.nightly()["extended"], 1)
		self.assertEqual(
			[call[0] for call in self.retention_calls()[before:]], ["archive-secondary", "archive-secondary"]
		)
		doc.reload()
		self.assertEqual(str(doc.retain_until), "2041-01-01 00:00:00")

	def test_stale_mode_is_refused(self):
		doc = self.archived(contract_end_date="2026-12-31")
		fake = self.fake("primary")
		for key in (doc.object_key, doc.meta_key):
			fake.put_object_retention(
				Bucket="archive-primary",
				Key=key,
				Retention={"Mode": "COMPLIANCE", "RetainUntilDate": datetime(2037, 1, 1, tzinfo=UTC)},
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
		self.assertEqual(str(doc.retain_until), "2041-01-01 00:00:00")
		self.assertEqual(doc.retention_mode, "COMPLIANCE, GOVERNANCE")

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
				Retention={"Mode": "COMPLIANCE", "RetainUntilDate": datetime(2035, 1, 1, tzinfo=UTC)},
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

	def test_new_uploads_use_the_configured_mode(self):
		self.configure(["primary", "secondary"], primary="COMPLIANCE")
		doc = self.document()
		run.archive_document(doc.name, store.targets())
		modes = {p["Bucket"]: p["ObjectLockMode"] for p in self.puts()}
		self.assertEqual(modes, {"archive-primary": "COMPLIANCE", "archive-secondary": "GOVERNANCE"})

	def test_fake_refuses_mode_change_without_bypass(self):
		fake = LockedFakeS3()
		target = store.Target("t", "b", "GOVERNANCE", fake)
		store.put_locked(target, "k", b"x", "GOVERNANCE", datetime(2035, 1, 1, tzinfo=UTC))
		with self.assertRaises(Exception):
			fake.put_object_retention(
				Bucket="b",
				Key="k",
				Retention={"Mode": "COMPLIANCE", "RetainUntilDate": datetime(2036, 1, 1, tzinfo=UTC)},
			)
