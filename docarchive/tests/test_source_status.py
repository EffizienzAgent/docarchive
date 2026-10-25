from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from docarchive import api
from docarchive.mail import archiving
from docarchive.tests.factories import (
	make_business_document,
	make_outsider,
	make_own_company,
	make_settings,
	unique,
)
from docarchive.tests.test_mail_archiving import MailArchivingTestCase

DOCUMENT = "Business Document"
SET_SOURCE = []


def set_source(doc):
	if SET_SOURCE:
		doc.source_doctype, doc.source_name = SET_SOURCE


class TestSourceStatus(IntegrationTestCase):
	def setUp(self):
		make_settings(default_own_company=make_own_company().name)

	def document(self, **fields):
		return make_business_document(archive_write=True, **fields)

	def test_newest_document_of_the_source_follows_replacements(self):
		source = unique("SRC")
		older = self.document(source_doctype="ToDo", source_name=source)
		newer = self.document(source_doctype="ToDo", source_name=source)
		replacement = self.document(replaces=newer.name)
		frappe.db.set_value(DOCUMENT, replacement.name, "archive_status", "Archived")
		self.assertEqual(
			api.source_status("ToDo", source),
			{
				"business_document": replacement.name,
				"archive_status": "Archived",
				"retain_until": None,
			},
		)
		self.assertNotEqual(older.name, replacement.name)

	def test_nothing_for_unknown_or_unreadable_sources(self):
		source = unique("SRC")
		self.document(source_doctype="ToDo", source_name=source)
		self.assertIsNone(api.source_status("ToDo", unique("other")))
		frappe.set_user(make_outsider(f"{unique('outsider')}@example.test"))
		self.addCleanup(frappe.set_user, "Administrator")
		self.assertIsNone(api.source_status("ToDo", source))

	def test_assign_hook_may_set_any_source(self):
		SET_SOURCE[:] = ["ToDo", unique("TODO")]
		self.addCleanup(SET_SOURCE.clear)
		real = frappe.get_hooks

		def hooks(name=None, *args, **kwargs):
			if name == "docarchive_assign":
				return [f"{__name__}.set_source"]
			return real(name, *args, **kwargs)

		with patch("frappe.get_hooks", side_effect=hooks):
			doc = make_business_document()
			self.assertEqual((doc.source_doctype, doc.source_name), tuple(SET_SOURCE))
			SET_SOURCE.clear()
			doc.source_name = "typed by a user"
			doc.save(ignore_permissions=True)
		self.assertEqual(frappe.db.get_value(DOCUMENT, doc.name, "source_name"), doc.source_name)
		self.assertNotEqual(doc.source_name, "typed by a user")

	def test_user_input_cannot_set_the_source(self):
		doc = make_business_document(source_doctype="ToDo", source_name="x")
		self.assertEqual((doc.source_doctype, doc.source_name), (None, None))


class TestMailStatusOfTheDocument(MailArchivingTestCase):
	def test_status_has_archive_status_and_retain_until(self):
		comm = self.communication()
		name = archiving.archive(self.capture(comm=comm))
		frappe.db.set_value(DOCUMENT, name, {"archive_status": "Archived", "retain_until": "2036-01-01"})
		status = archiving.status(comm.name)
		self.assertEqual(
			(status["business_document"], status["archive_status"], str(status["retain_until"])),
			(name, "Archived", "2036-01-01 00:00:00"),
		)

	def test_unreadable_document_hides_them(self):
		comm = self.communication()
		archiving.archive(self.capture(comm=comm))
		user = make_outsider(f"{unique('reader')}@example.test")
		frappe.share.add("Communication", comm.name, user, read=1)
		frappe.set_user(user)
		self.addCleanup(frappe.set_user, "Administrator")
		status = archiving.status(comm.name)
		self.assertEqual(
			(status["archived"], status["archive_status"], status["retain_until"]), (True, None, None)
		)
