from datetime import UTC, datetime
from unittest.mock import patch

import frappe

from docarchive import archive_now, run
from docarchive.tests.factories import make_document_type, make_manager, make_outsider
from docarchive.tests.test_run import DOCUMENT, ArchiveRunTestCase

QUEUE_ARGS = ("queue", "job_id", "deduplicate", "enqueue_after_commit", "now")


def run_now(method, **kwargs):
	frappe.get_attr(method)(**{k: v for k, v in kwargs.items() if k not in QUEUE_ARGS})


class TestArchiveNow(ArchiveRunTestCase):
	def setUp(self):
		super().setUp()
		self.enqueue = self.enterContext(patch("frappe.enqueue", side_effect=run_now))
		self.addCleanup(frappe.set_user, "Administrator")

	def manager(self):
		user = make_manager("archive-now-manager@example.com")
		frappe.share.add("Contact", self.party.name, user, read=1)
		return user

	def test_preview_shows_retention_and_mode(self):
		doc = self.document(age_hours=0)
		[row] = archive_now.preview([doc.name])
		self.assertEqual(
			(row["eligible"], row["retain_until"], row["modes"]),
			(True, datetime(2035, 1, 2, tzinfo=UTC).isoformat(), ["GOVERNANCE"]),
		)

	def test_archives_immediately_to_every_target(self):
		doc = self.document(age_hours=0)
		frappe.set_user(self.manager())
		result = archive_now.archive_now(frappe.as_json([doc.name]))
		frappe.set_user("Administrator")
		self.assertEqual(result["queued"], [doc.name])
		self.assertEqual(frappe.db.get_value(DOCUMENT, doc.name, "archive_status"), "Archived")
		puts = self.puts()
		self.assertEqual(len(puts), 4)
		for put in puts:
			self.assertEqual(put["ObjectLockMode"], "GOVERNANCE")
			self.assertEqual(put["ObjectLockRetainUntilDate"], datetime(2035, 1, 2, tzinfo=UTC))
		self.assertEqual(self.enqueue.call_args.kwargs["queue"], "long")
		self.assertTrue(self.enqueue.call_args.kwargs["deduplicate"])

	def test_double_click_is_idempotent(self):
		doc = self.document(age_hours=0)
		archive_now.archive_now([doc.name])
		again = archive_now.archive_now([doc.name])
		self.assertEqual((again["queued"], again["started"]), ([], [doc.name]))
		self.assertEqual(len(self.puts()), 4)
		self.assertEqual(self.enqueue.call_count, 1)

	def test_permission_denied(self):
		doc = self.document(age_hours=0)
		frappe.set_user(make_outsider("archive-now-outsider@example.com"))
		for method in (archive_now.preview, archive_now.archive_now):
			with self.subTest(method.__name__), self.assertRaises(frappe.PermissionError):
				method([doc.name])
		self.assertEqual(self.puts(), [])

	def test_post_only(self):
		self.assertEqual(frappe.allowed_http_methods_for_whitelisted_func[archive_now.archive_now], ["POST"])

	def test_ineligible_refused(self):
		incomplete = self.document(counterparty_type=None, counterparty=None)
		no_archive = self.document(document_type=make_document_type(archive=0).name)
		for doc in (incomplete, no_archive):
			with self.subTest(doc.name), self.assertRaises(frappe.ValidationError):
				archive_now.archive_now([doc.name])
			self.assertFalse(archive_now.preview([doc.name])[0]["eligible"])
		self.configure(["primary"], enabled=0)
		doc = self.document()
		with self.assertRaisesRegex(frappe.ValidationError, "switched off"):
			archive_now.archive_now([doc.name])
		self.assertEqual(self.puts(), [])

	def test_bulk_skips_ineligible(self):
		ready = self.document(age_hours=0)
		incomplete = self.document(counterparty_type=None, counterparty=None)
		result = archive_now.archive_now([ready.name, incomplete.name])
		self.assertEqual(result["queued"], [ready.name])
		self.assertIn(incomplete.name, result["refused"])
		self.assertEqual(frappe.db.get_value(DOCUMENT, incomplete.name, "archive_started_at"), None)

	def test_failed_target_is_left_for_the_nightly_run(self):
		doc = self.document(age_hours=0)
		self.fakes["archive-secondary"] = self.fake("secondary").__class__(fail={"put_object"})
		archive_now.archive_now([doc.name])
		doc.reload()
		self.assertEqual([c.status for c in doc.archive_copies], ["Archived", "Failed"])
		self.assertIn(doc.name, run.eligible())

	def test_marks_documents_as_queued_until_the_job_ran(self):
		doc = self.document(age_hours=0)
		self.enqueue.side_effect = None
		archive_now.archive_now([doc.name])
		self.assertTrue(frappe.db.get_value(DOCUMENT, doc.name, "archive_queued_at"))
		run_now(self.enqueue.call_args.args[0], **self.enqueue.call_args.kwargs)
		self.assertEqual(
			frappe.db.get_value(DOCUMENT, doc.name, ["archive_status", "archive_queued_at"]),
			("Archived", None),
		)

	def test_failed_run_clears_the_queued_mark(self):
		doc = self.document(age_hours=0)
		self.fakes["archive-secondary"] = self.fake("secondary").__class__(fail={"put_object"})
		archive_now.archive_now([doc.name])
		self.assertIsNone(frappe.db.get_value(DOCUMENT, doc.name, "archive_queued_at"))
