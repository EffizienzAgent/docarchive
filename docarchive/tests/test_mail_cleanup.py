from unittest.mock import patch

import frappe
from frappe.utils import add_days, now_datetime

from docarchive import mail
from docarchive.document_types import INTERNAL_CORRESPONDENCE
from docarchive.mail import archiving, cleanup
from docarchive.tests.factories import eml, make_communication, make_email_account, make_party
from docarchive.tests.test_mail_archiving import MailArchivingTestCase

ORIGINAL = "Mail Original"
DOCUMENT = "Business Document"


class TestCleanup(MailArchivingTestCase):
	def aged(self, doctype, name, days=91):
		frappe.db.set_value(doctype, name, "creation", add_days(now_datetime(), -days), update_modified=False)

	def original(self, days=91, account=None, raw=None):
		comm = make_communication(account or self.account)
		name = mail.capture(comm, raw or eml(), "t")
		self.aged(ORIGINAL, name, days)
		return name

	def archived(self, days=91, account=None, raw=None):
		original = self.original(days, account, raw)
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

	def test_original_with_document_kept(self):
		name, original = self.archived()
		self.assign_party(name)
		self.assertEqual(cleanup.run(), {"originals": 0, "documents": 0})
		self.assertTrue(frappe.db.exists(ORIGINAL, original))

	def assign_party(self, name):
		party = make_party()
		frappe.db.set_value(DOCUMENT, name, {"counterparty_type": party.doctype, "counterparty": party.name})

	def test_recent_document_kept(self):
		name, _ = self.archived(days=89)
		self.assertEqual(cleanup.run(), {"originals": 0, "documents": 0})
		self.assertTrue(frappe.db.exists(DOCUMENT, name))

	def test_other_account_untouched(self):
		other = make_email_account().name
		self.configure_two(other, first_days=30, other_days=90)
		_, original = self.archived()
		kept_doc, kept_original = self.archived(days=60, account=other)
		loose = self.original(days=60, account=other)
		self.assertEqual(cleanup.run(), {"originals": 0, "documents": 1})
		self.assertFalse(frappe.db.exists(ORIGINAL, original))
		self.assertTrue(frappe.db.exists(DOCUMENT, kept_doc))
		self.assertTrue(frappe.db.exists(ORIGINAL, kept_original))
		self.assertTrue(frappe.db.exists(ORIGINAL, loose))

	def configure_two(self, other, first_days, other_days):
		settings = frappe.get_single("Archive Settings")
		settings.set("mail_accounts", [])
		for account, days in ((self.account, first_days), (other, other_days)):
			settings.append("mail_accounts", {"email_account": account, "mode": "Manual", "keep_days": days})
		settings.save(ignore_permissions=True)

	def test_counterparty_assigned_before_deletion_keeps_document(self):
		name, _ = self.archived()
		self.assign_party(name)
		with patch.object(cleanup, "_old_documents", return_value=[name]):
			self.assertEqual(cleanup.run()["documents"], 0)
		self.assertTrue(frappe.db.exists(DOCUMENT, name))

	def test_old_document_without_counterparty_deleted(self):
		name, original = self.archived()
		self.assertEqual(cleanup.run(), {"originals": 0, "documents": 1})
		self.assertFalse(frappe.db.exists(DOCUMENT, name))
		self.assertFalse(frappe.db.exists(ORIGINAL, original))

	def test_internal_document_without_counterparty_kept(self):
		name, original = self.archived(raw=eml(From="a@acme.test", To="b@acme.test"))
		self.assertEqual(frappe.db.get_value(DOCUMENT, name, "document_type"), INTERNAL_CORRESPONDENCE)
		self.assertEqual(cleanup.run()["documents"], 0)
		self.assertTrue(frappe.db.exists(DOCUMENT, name))
		self.assertTrue(frappe.db.exists(ORIGINAL, original))
		with patch.object(cleanup, "_old_documents", return_value=[name]):
			self.assertEqual(cleanup.run()["documents"], 0)
		self.assertTrue(frappe.db.exists(DOCUMENT, name))

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

	def test_document_marked_for_legal_hold_kept(self):
		name, original = self.archived()
		frappe.db.set_value(DOCUMENT, name, "hold_on_archive", 1)
		self.assertEqual(cleanup.run()["documents"], 0)
		self.assertTrue(frappe.db.exists(DOCUMENT, name))
		self.assertTrue(frappe.db.exists(ORIGINAL, original))

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
		return patch.object(cleanup, "_remove_original", remover)
