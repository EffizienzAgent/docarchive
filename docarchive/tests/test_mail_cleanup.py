import frappe
from frappe.utils import add_days, now_datetime

from docarchive import mail
from docarchive.mail import archiving, cleanup
from docarchive.tests.factories import eml, make_party
from docarchive.tests.test_mail_archiving import MailArchivingTestCase

ORIGINAL = "Mail Original"
DOCUMENT = "Business Document"


class TestCleanup(MailArchivingTestCase):
	def aged(self, doctype, name, days=91):
		frappe.db.set_value(doctype, name, "creation", add_days(now_datetime(), -days), update_modified=False)

	def original(self, days=91):
		name = mail.capture(self.communication(), eml(), "t")
		self.aged(ORIGINAL, name, days)
		return name

	def archived(self, days=91):
		original = self.original(days)
		name = archiving.archive(original)
		self.aged(DOCUMENT, name, days)
		return name, original

	def test_old_unarchived_original_deleted(self):
		original = self.original()
		self.assertEqual(cleanup.run(), {"originals": 1, "documents": 0})
		self.assertFalse(frappe.db.exists(ORIGINAL, original))

	def test_recent_original_kept(self):
		original = self.original(days=89)
		self.assertEqual(cleanup.run(), {"originals": 0, "documents": 0})
		self.assertTrue(frappe.db.exists(ORIGINAL, original))

	def test_archived_original_kept(self):
		name, original = self.archived()
		frappe.db.set_value(DOCUMENT, name, "counterparty", None)
		frappe.db.set_value(DOCUMENT, name, "archive_started_at", now_datetime())
		self.assertEqual(cleanup.run()["originals"], 0)
		self.assertTrue(frappe.db.exists(ORIGINAL, original))

	def test_old_document_without_counterparty_deleted(self):
		name, original = self.archived()
		self.assertEqual(cleanup.run(), {"originals": 0, "documents": 1})
		self.assertFalse(frappe.db.exists(DOCUMENT, name))
		self.assertFalse(frappe.db.exists(ORIGINAL, original))

	def test_old_document_with_counterparty_kept(self):
		name, _ = self.archived()
		party = make_party()
		frappe.db.set_value(DOCUMENT, name, {"counterparty_type": party.doctype, "counterparty": party.name})
		self.assertEqual(cleanup.run()["documents"], 0)
		self.assertTrue(frappe.db.exists(DOCUMENT, name))

	def test_started_document_kept(self):
		name, _ = self.archived()
		frappe.db.set_value(DOCUMENT, name, "archive_started_at", now_datetime())
		self.assertEqual(cleanup.run()["documents"], 0)
		self.assertTrue(frappe.db.exists(DOCUMENT, name))

	def test_account_off_is_skipped(self):
		original = self.original()
		self.configure(mode="Off")
		self.assertEqual(cleanup.run(), {"originals": 0, "documents": 0})
		self.assertTrue(frappe.db.exists(ORIGINAL, original))

	def test_original_without_communication_deleted(self):
		original = self.original()
		frappe.db.set_value(ORIGINAL, original, "communication", None)
		self.assertEqual(cleanup.run()["originals"], 1)

	def test_failure_does_not_stop_the_job(self):
		first, second = self.original(), self.original()
		real = cleanup._remove_original

		def flaky(name):
			if name == first:
				raise frappe.ValidationError("boom")
			real(name)

		with self.patch_remover(flaky):
			self.assertEqual(cleanup.run()["originals"], 1)
		self.assertTrue(frappe.db.exists(ORIGINAL, first))
		self.assertFalse(frappe.db.exists(ORIGINAL, second))

	def patch_remover(self, remover):
		from unittest.mock import patch

		return patch.object(cleanup, "_remove_original", remover)
