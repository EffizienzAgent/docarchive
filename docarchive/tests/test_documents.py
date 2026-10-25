import frappe
from frappe.permissions import add_user_permission
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
			self.assertEqual(documents.upload_defaults()["document_type"], self.type)

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

	def test_create_documents_stores_correspondence_fields(self):
		letter = make_document_type(is_correspondence=1).name
		with self.set_user(self.manager("docs-letter@example.com")):
			[name] = documents.create_documents(
				"Contact",
				self.party.name,
				frappe.as_json([make_file().file_url]),
				letter,
				"2026-03-15",
				correspondence_channel="Letter",
				correspondence_direction="Outgoing",
				correspondence_at="2026-03-15 10:30:00",
			)
		doc = frappe.get_doc("Business Document", name)
		self.assertEqual((doc.correspondence_channel, doc.correspondence_direction), ("Letter", "Outgoing"))
		self.assertEqual(str(doc.correspondence_at), "2026-03-15 10:30:00")

	def test_correspondence_fields_skip_other_types(self):
		with self.set_user(self.manager("docs-not-letter@example.com")):
			[name] = self.create_with(correspondence_channel="Letter")
		self.assertFalse(frappe.db.get_value("Business Document", name, "correspondence_channel"))

	def test_create_documents_links_the_source_document(self):
		source = make_business_document(**party_fields(self.party))
		with self.set_user(self.manager("docs-source@example.com")):
			[name] = self.create_with(source_doctype="Business Document", source_name=source.name)
		self.assertEqual(
			frappe.db.get_value("Business Document", name, ["source_doctype", "source_name"]),
			("Business Document", source.name),
		)

	def test_source_document_must_be_readable(self):
		hidden = make_business_document(own_company=make_own_company().name, **party_fields(self.party))
		user = self.manager("docs-source-hidden@example.com")
		add_user_permission("Own Company", self.company, user)
		with self.set_user(user):
			with self.assertRaises(frappe.PermissionError):
				self.create_with(source_doctype="Business Document", source_name=hidden.name)

	def test_source_must_be_a_business_document(self):
		with self.set_user(self.manager("docs-source-other@example.com")):
			with self.assertRaises(frappe.ValidationError):
				self.create_with(source_doctype="Contact", source_name=self.party.name)

	def test_upload_defaults_list_correspondence_types(self):
		letter = make_document_type(is_correspondence=1).name
		with self.set_user(self.manager("docs-defaults-letter@example.com")):
			types = documents.upload_defaults()["correspondence_types"]
		self.assertIn(letter, types)
		self.assertNotIn(self.type, types)

	def create_with(self, **fields):
		return documents.create_documents(
			"Contact",
			self.party.name,
			frappe.as_json([make_file().file_url]),
			self.type,
			"2026-03-15",
			**fields,
		)
