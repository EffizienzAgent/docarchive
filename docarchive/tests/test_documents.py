import frappe
from frappe.tests import IntegrationTestCase

from docarchive import documents
from docarchive.document_archive.doctype.business_document.business_document import missing_fields
from docarchive.tests.factories import (
	make_business_document,
	make_document_type,
	make_file,
	make_manager,
	make_outsider,
	make_own_company,
	make_party,
	make_settings,
	party_fields,
	unique,
)


class TestCounterpartyDocuments(IntegrationTestCase):
	def setUp(self):
		make_settings(default_own_company=make_own_company().name)
		self.type = make_document_type().name
		self.party = make_party()

	def tearDown(self):
		frappe.set_user("Administrator")

	def manager(self, email):
		user = make_manager(email)
		frappe.share.add("Contact", self.party.name, user, read=1)
		return user

	def create(self, file_urls, document_type=None, date="2026-03-15"):
		return documents.create_documents(
			"Contact", self.party.name, file_urls, document_type or self.type, date
		)

	def test_lists_counterparty_documents_newest_first(self):
		older = make_business_document(document_date="2026-01-10", **party_fields(self.party))
		newer = make_business_document(document_date="2026-03-10", **party_fields(self.party))
		make_business_document(**party_fields(make_party("Other")))
		rows = documents.counterparty_documents("Contact", self.party.name)
		self.assertEqual([r.name for r in rows], [newer.name, older.name])
		self.assertEqual(set(rows[0]), set(documents.DOCUMENT_FIELDS))

	def test_outsider_gets_no_documents(self):
		make_business_document(**party_fields(self.party))
		frappe.share.add("Contact", self.party.name, make_outsider("docs-outsider@example.com"), read=1)
		with self.set_user("docs-outsider@example.com"):
			self.assertEqual(documents.counterparty_documents("Contact", self.party.name), [])

	def test_upload_defaults_to_setting_without_a_date(self):
		make_settings(default_own_company=make_own_company().name, default_upload_type=self.type)
		with self.set_user(make_manager("docs-defaults@example.com")):
			self.assertEqual(documents.upload_defaults(), {"document_type": self.type})

	def test_date_from_file_name(self):
		cases = {
			"Rechnung_2026-03-15.pdf": "2026-03-15",
			"scan20260401-1.pdf": "2026-04-01",
			"Vertrag 1.2.2025 final.pdf": "2025-02-01",
			"INV-12345678.pdf": None,
			"2026-13-01.pdf": None,
			"invoice.pdf": None,
		}
		for name, expected in cases.items():
			with self.subTest(name):
				self.assertEqual(documents.date_from_file_name(name), expected)

	def test_dialog_date_from_file_name_or_left_empty(self):
		with self.set_user(self.manager("docs-dates@example.com")):
			files = [make_file(file_name=f"{unique('scan')}_2026-02-03.pdf"), make_file()]
			names = documents.create_documents(
				"Contact", self.party.name, frappe.as_json([f.file_url for f in files]), self.type
			)
		dated, empty = (frappe.get_doc("Business Document", name) for name in names)
		self.assertEqual(str(dated.document_date), "2026-02-03")
		self.assertIsNone(empty.document_date)
		self.assertIn("Document Date", missing_fields(empty))

	def test_upload_defaults_to_last_used_type(self):
		other = make_document_type().name
		with self.set_user(self.manager("docs-last@example.com")):
			self.create([make_file().file_url], other)
			self.assertEqual(documents.upload_defaults()["document_type"], other)

	def test_upload_defaults_empty_without_permission(self):
		with self.set_user(make_outsider("docs-defaults-outsider@example.com")):
			self.assertEqual(documents.upload_defaults(), {})

	def test_create_documents_one_per_file(self):
		with self.set_user(self.manager("docs-upload@example.com")):
			files = [make_file(file_name="Contract_acme-2026.pdf"), make_file(file_name="Addendum_1.pdf")]
			names = self.create(frappe.as_json([f.file_url for f in files]))
		docs = [frappe.get_doc("Business Document", name) for name in names]
		self.assertEqual([d.title for d in docs], ["Contract acme 2026", "Addendum 1"])
		for doc, file in zip(docs, files, strict=True):
			self.assertEqual((doc.counterparty_type, doc.counterparty), ("Contact", self.party.name))
			self.assertEqual(doc.document_type, self.type)
			self.assertEqual(str(doc.document_date), "2026-03-15")
			attached = frappe.db.get_value(
				"File", file.name, ["attached_to_doctype", "attached_to_name", "is_private"], as_dict=True
			)
			self.assertEqual(
				attached,
				{"attached_to_doctype": "Business Document", "attached_to_name": doc.name, "is_private": 1},
			)

	def test_create_documents_needs_create_permission(self):
		frappe.share.add("Contact", self.party.name, make_outsider("docs-no-create@example.com"), read=1)
		with self.set_user("docs-no-create@example.com"):
			upload = make_file()
			with self.assertRaises(frappe.PermissionError):
				self.create([upload.file_url])

	def test_create_documents_rejects_public_file(self):
		with self.assertRaises(frappe.ValidationError):
			self.create([make_file(is_private=0).file_url])

	def test_discard_uploads_deletes_only_own_unattached_files(self):
		with self.set_user(self.manager("docs-discard-other@example.com")):
			foreign = make_file()
		with self.set_user(self.manager("docs-discard@example.com")):
			mine = make_file()
			used = make_file()
			self.create([used.file_url])
			count = documents.discard_uploads(
				frappe.as_json([mine.file_url, used.file_url, foreign.file_url])
			)
		self.assertEqual(count, 1)
		self.assertFalse(frappe.db.exists("File", mine.name))
		self.assertTrue(frappe.db.exists("File", used.name))
		self.assertTrue(frappe.db.exists("File", foreign.name))

	def test_missing_company_gives_clear_message(self):
		make_settings()
		with self.set_user(self.manager("docs-no-company@example.com")):
			upload = make_file()
			with self.assertRaisesRegex(frappe.ValidationError, "System Manager"):
				self.create([upload.file_url])
