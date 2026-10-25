import frappe

from docarchive.mail import archiving
from docarchive.tests.factories import make_outsider, unique
from docarchive.tests.test_mail_archiving import MailArchivingTestCase

DOCUMENT = "Business Document"


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
