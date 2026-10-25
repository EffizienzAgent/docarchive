import frappe
from frappe.tests import IntegrationTestCase

from docarchive import documents
from docarchive.document_archive.doctype.business_document.business_document import missing_fields
from docarchive.tests.factories import (
	fields_target,
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
		self.company = make_own_company().name
		make_settings(default_own_company=self.company)
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

	def test_panel_marks_missing_fields_and_eligible_documents(self):
		make_settings(default_own_company=self.company, enabled=1, targets=[fields_target()])
		numbered = make_document_type(number_required=1).name
		ready = make_business_document(document_type=self.type, **party_fields(self.party))
		incomplete = make_business_document(
			document_type=numbered, document_number=None, **party_fields(self.party)
		)
		panel = documents.counterparty_panel("Contact", self.party.name)
		rows = {row["name"]: row for row in panel["documents"]}
		self.assertTrue(panel["archiving"] and panel["can_write"] and panel["can_create"])
		self.assertFalse(panel["truncated"])
		self.assertEqual((rows[ready.name]["missing"], rows[ready.name]["eligible"]), ([], True))
		self.assertEqual(
			(rows[incomplete.name]["missing"], rows[incomplete.name]["eligible"]),
			(["Document Number"], False),
		)
		self.assertEqual(rows[ready.name]["file"], ready.file)
		self.assertIsNone(rows[ready.name]["archive_reason"])
		self.assertIn("Document Number", rows[incomplete.name]["archive_reason"])

	def test_panel_lists_attachments(self):
		files = [make_file(file_name="Rechnung.pdf"), make_file(file_name="Anlage.pdf")]
		[name] = documents.create_documents(
			"Contact",
			self.party.name,
			documents=frappe.as_json(
				[{"file_url": f.file_url, "document_type": self.type, "title": "Anlage"} for f in files]
			),
			combine=True,
		)
		[row] = documents.counterparty_panel("Contact", self.party.name)["documents"]
		self.assertEqual(row["name"], name)
		self.assertEqual(
			[(item["file"], item["title"]) for item in row["attachments"]], [(files[1].file_url, "Anlage")]
		)

	def test_panel_shows_legal_hold_and_hides_deleted_documents(self):
		held = make_business_document(document_type=self.type, **party_fields(self.party))
		deleted = make_business_document(document_type=self.type, **party_fields(self.party))
		frappe.db.set_value("Business Document", held.name, "legal_hold", 1)
		frappe.db.set_value("Business Document", deleted.name, "archive_status", "Deleted")
		rows = {
			row["name"]: row for row in documents.counterparty_panel("Contact", self.party.name)["documents"]
		}
		self.assertEqual(rows[held.name]["legal_hold"], 1)
		self.assertNotIn(deleted.name, rows)
		listed = [row.name for row in documents.counterparty_documents("Contact", self.party.name)]
		self.assertNotIn(deleted.name, listed)

	def test_panel_without_archiving_has_no_eligible_documents(self):
		make_business_document(document_type=self.type, **party_fields(self.party))
		panel = documents.counterparty_panel("Contact", self.party.name)
		self.assertFalse(panel["archiving"])
		self.assertEqual([row["eligible"] for row in panel["documents"]], [False])
		self.assertEqual(panel["documents"][0]["archive_reason"], "Archiving is switched off.")

	def test_panel_is_empty_for_outsiders(self):
		make_business_document(**party_fields(self.party))
		frappe.share.add("Contact", self.party.name, make_outsider("panel-outsider@example.com"), read=1)
		with self.set_user("panel-outsider@example.com"):
			panel = documents.counterparty_panel("Contact", self.party.name)
		self.assertEqual((panel["documents"], panel["can_create"], panel["can_write"]), ([], False, False))

	def test_panel_needs_counterparty_access(self):
		with self.set_user(make_manager("panel-no-party@example.com")):
			with self.assertRaises(frappe.PermissionError):
				documents.counterparty_panel("Contact", self.party.name)

	def test_create_documents_per_file_details(self):
		other = make_document_type().name
		with self.set_user(self.manager("docs-per-file@example.com")):
			acme = frappe.generate_hash(length=8)
			files = [
				make_file(file_name=f"2026-08-05 Rechnung {acme}.pdf"),
				make_file(file_name="Vertrag.pdf"),
			]
			names = documents.create_documents(
				"Contact",
				self.party.name,
				documents=frappe.as_json(
					[
						{
							"file_url": files[0].file_url,
							"document_type": self.type,
							"document_date": "2026-08-05",
						},
						{
							"file_url": files[1].file_url,
							"document_type": other,
							"document_date": "2026-02-01",
							"title": "Rahmenvertrag",
						},
					]
				),
			)
		docs = [frappe.get_doc("Business Document", name) for name in names]
		self.assertEqual(
			[(d.title, d.document_type, str(d.document_date)) for d in docs],
			[(f"Rechnung {acme}", self.type, "2026-08-05"), ("Rahmenvertrag", other, "2026-02-01")],
		)

	def test_create_documents_takes_date_from_file_name(self):
		with self.set_user(self.manager("docs-file-date@example.com")):
			upload = make_file(file_name=f"Rechnung_20260805_{frappe.generate_hash(length=8)}.pdf")
			[name] = documents.create_documents(
				"Contact", self.party.name, frappe.as_json([upload.file_url]), self.type
			)
		doc = frappe.get_doc("Business Document", name)
		self.assertEqual(str(doc.document_date), "2026-08-05")
		self.assertNotIn("2026", doc.title)
