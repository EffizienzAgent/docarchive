import frappe
from frappe.tests import IntegrationTestCase

from docarchive.mail import archiving
from docarchive.tests.factories import (
	PDF,
	eml,
	make_business_document,
	make_file,
	make_manager,
	make_outsider,
	make_own_company,
	make_party,
	make_settings,
	unique,
)

DOCUMENT = "Business Document"


def upload(raw, file_name=None, **fields):
	return make_file(raw, file_name or f"{unique('mail')}.eml", **fields)


class TestArchiveUpload(IntegrationTestCase):
	def setUp(self):
		self.company = make_own_company().name
		make_settings(default_own_company=self.company)
		self.raw = eml()
		self.message_id = f"{self.raw.split(b'Message-ID: <')[1].split(b'>')[0].decode()}"

	def test_upload_creates_correspondence(self):
		file = upload(self.raw)
		name = archiving.archive_upload(file.file_url, "Incoming")
		doc = frappe.get_doc(DOCUMENT, name)
		self.assertEqual(
			(doc.correspondence_channel, doc.correspondence_direction, doc.mail_message_id),
			("E-mail", "Incoming", self.message_id),
		)
		self.assertEqual(doc.own_company, self.company)
		self.assertEqual(str(doc.document_date), "2026-03-10")
		self.assertTrue(doc.file)
		original = frappe.db.get_value(
			"Mail Original",
			{"business_document": name},
			["origin", "email_account", "communication", "internal"],
			as_dict=True,
		)
		self.assertEqual(
			(original.origin, original.email_account, original.communication, original.internal),
			("manual-upload", None, None, 0),
		)

	def test_upload_deletes_temporary_file(self):
		file = upload(self.raw)
		archiving.archive_upload(file.file_url, "Outgoing")
		self.assertFalse(frappe.db.exists("File", file.name))

	def test_upload_uses_passed_own_company(self):
		other = make_own_company().name
		name = archiving.archive_upload(upload(self.raw).file_url, "Incoming", own_company=other)
		self.assertEqual(frappe.db.get_value(DOCUMENT, name, "own_company"), other)

	def test_upload_for_gap_must_match(self):
		file = upload(self.raw)
		self.assertRaises(
			frappe.ValidationError,
			archiving.archive_upload,
			file.file_url,
			"Incoming",
			gap_message_id="other@x",
		)
		self.assertTrue(frappe.db.exists("File", file.name))

	def test_upload_for_gap_accepts_matching_message(self):
		name = archiving.archive_upload(upload(self.raw).file_url, "Incoming", gap_message_id=self.message_id)
		self.assertEqual(frappe.db.get_value(DOCUMENT, name, "mail_message_id"), self.message_id)

	def test_upload_of_archived_message_returns_existing(self):
		first = archiving.archive_upload(upload(self.raw).file_url, "Incoming")
		again = upload(self.raw)
		self.assertEqual(archiving.archive_upload(again.file_url, "Incoming"), first)
		self.assertEqual(frappe.db.count("Mail Original", {"message_id": self.message_id}), 1)
		self.assertFalse(frappe.db.exists("File", again.name))

	def test_upload_without_date_uses_now(self):
		raw = eml(Date="")
		name = archiving.archive_upload(upload(raw).file_url, "Incoming")
		self.assertTrue(frappe.db.get_value(DOCUMENT, name, "correspondence_at"))

	def test_upload_refuses_other_extensions(self):
		file = upload(PDF, "mail.pdf")
		self.assertRaises(frappe.ValidationError, archiving.archive_upload, file.file_url, "Incoming")

	def test_upload_refuses_attached_and_public_files(self):
		attached = upload(
			self.raw,
			attached_to_doctype="Note",
			attached_to_name=frappe.get_doc({"doctype": "Note", "title": unique("n")}).insert().name,
		)
		public = upload(self.raw, is_private=0)
		for file in (attached, public):
			self.assertRaises(frappe.ValidationError, archiving.archive_upload, file.file_url, "Incoming")

	def test_upload_refuses_foreign_file(self):
		file = upload(self.raw)
		frappe.set_user(make_manager("uploader-b@example.test"))
		self.addCleanup(frappe.set_user, "Administrator")
		self.assertRaises(frappe.ValidationError, archiving.archive_upload, file.file_url, "Incoming")

	def test_upload_refuses_bad_direction(self):
		file = upload(self.raw)
		self.assertRaises(frappe.ValidationError, archiving.archive_upload, file.file_url, "Sideways")

	def test_upload_needs_create_permission(self):
		file = upload(self.raw)
		frappe.set_user(make_outsider("outsider-b@example.test"))
		self.addCleanup(frappe.set_user, "Administrator")
		self.assertRaises(frappe.PermissionError, archiving.archive_upload, file.file_url, "Incoming")

	def test_user_cannot_edit_direction_of_manual_upload(self):
		name = archiving.archive_upload(upload(self.raw).file_url, "Incoming")
		doc = frappe.get_doc(DOCUMENT, name)
		doc.correspondence_direction = "Outgoing"
		doc.correspondence_channel = "Fax"
		doc.save(ignore_permissions=True)
		doc.reload()
		self.assertEqual((doc.correspondence_direction, doc.correspondence_channel), ("Incoming", "E-mail"))

	def test_letter_stays_editable(self):
		doc = make_business_document(correspondence_channel="Letter", correspondence_direction="Incoming")
		doc.correspondence_direction = "Outgoing"
		doc.save(ignore_permissions=True)
		doc.reload()
		self.assertEqual(doc.correspondence_direction, "Outgoing")

	def test_discard_removes_unstarted_manual_upload(self):
		name = archiving.archive_upload(upload(self.raw).file_url, "Incoming")
		result = archiving.discard([name])
		self.assertEqual(result["discarded"], [name])
		self.assertFalse(frappe.db.exists(DOCUMENT, name))

	def test_gap_upload_takes_company_and_counterparty_from_thread(self):
		other = make_own_company().name
		party = make_party()
		child = eml(**{"In-Reply-To": f"<{self.message_id}>"})
		parent = archiving.archive_upload(upload(child).file_url, "Outgoing", own_company=other)
		frappe.db.set_value(
			DOCUMENT, parent, {"counterparty_type": party.doctype, "counterparty": party.name}
		)
		name = archiving.archive_upload(upload(self.raw).file_url, "Incoming", gap_message_id=self.message_id)
		doc = frappe.get_doc(DOCUMENT, name)
		self.assertEqual((doc.own_company, doc.counterparty), (other, party.name))

	def test_gap_upload_without_thread_uses_default_company(self):
		name = archiving.archive_upload(upload(self.raw).file_url, "Incoming", gap_message_id=self.message_id)
		doc = frappe.get_doc(DOCUMENT, name)
		self.assertEqual((doc.own_company, doc.counterparty), (self.company, None))

	def test_upload_refuses_non_mail(self):
		file = upload(PDF, "mail.eml")
		self.assertRaises(frappe.ValidationError, archiving.archive_upload, file.file_url, "Incoming")

	def test_upload_sets_passed_counterparty(self):
		party = make_party()
		name = archiving.archive_upload(
			upload(self.raw).file_url, "Incoming", counterparty_type="Contact", counterparty=party.name
		)
		self.assertEqual(frappe.db.get_value(DOCUMENT, name, "counterparty"), party.name)

	def test_upload_counterparty_must_be_readable(self):
		party = make_party()
		frappe.set_user(make_manager("uploader-party@example.test"))
		self.addCleanup(frappe.set_user, "Administrator")
		file = upload(self.raw)
		self.assertRaises(
			frappe.PermissionError,
			archiving.archive_upload,
			file.file_url,
			"Incoming",
			counterparty_type="Contact",
			counterparty=party.name,
		)
