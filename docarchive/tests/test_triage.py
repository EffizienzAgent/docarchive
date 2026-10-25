import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, now_datetime, today

from docarchive import triage
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
)


class TestTriage(IntegrationTestCase):
	def setUp(self):
		make_settings(default_own_company=make_own_company().name)
		self.type = make_document_type(number_required=1).name
		self.party = make_party()
		self.doc = make_business_document(
			document_type=self.type,
			document_number=None,
			file=make_file(file_name="2026-08-05 Rechnung Acme.pdf").file_url,
			**party_fields(self.party),
		)

	def tearDown(self):
		frappe.set_user("Administrator")

	def manager(self, email):
		user = make_manager(email)
		frappe.share.add("Contact", self.party.name, user, read=1)
		return user

	def test_item_shows_file_missing_fields_and_suggested_date(self):
		item = triage.triage_item(self.doc.name)
		self.assertTrue(item["file_name"].startswith("2026-08-05 Rechnung Acme"))
		self.assertEqual(str(item["suggested_date"]), "2026-08-05")
		self.assertEqual(item["missing"], ["Document Number"])
		self.assertEqual(
			(item["needs_contract_end"], item["locked"], item["can_write"]), (False, False, True)
		)
		self.assertTrue(item["can_delete"])

	def test_contract_types_need_contract_end(self):
		contract = make_document_type(retention_from="Contract End Date").name
		self.assertTrue(triage.document_type_rules(contract)["needs_contract_end"])
		self.assertFalse(triage.document_type_rules(self.type)["needs_contract_end"])

	def test_save_updates_document(self):
		with self.set_user(self.manager("triage-save@example.com")):
			result = triage.save_item(
				self.doc.name, frappe.as_json({"document_number": "RE-1", "document_date": "2026-08-05"})
			)
		self.assertEqual(result["item"]["missing"], [])
		doc = frappe.get_doc("Business Document", self.doc.name)
		self.assertEqual((doc.document_number, str(doc.document_date)), ("RE-1", "2026-08-05"))

	def test_validation_error_is_returned_and_nothing_changes(self):
		with self.set_user(self.manager("triage-invalid@example.com")):
			result = triage.save_item(
				self.doc.name,
				frappe.as_json({"document_number": "RE-2", "document_date": add_days(today(), 400)}),
			)
		self.assertIn("future", result["error"])
		self.assertIsNone(frappe.db.get_value("Business Document", self.doc.name, "document_number"))

	def test_cleared_date_comes_from_the_file_name(self):
		with self.set_user(self.manager("triage-file-date@example.com")):
			result = triage.save_item(self.doc.name, frappe.as_json({"document_date": None}))
		self.assertEqual(str(result["item"]["document_date"]), "2026-08-05")

	def test_cleared_date_stays_empty_and_is_missing(self):
		doc = make_business_document(
			file=make_file(file_name="Rechnung.pdf").file_url, **party_fields(self.party)
		)
		with self.set_user(self.manager("triage-no-date@example.com")):
			result = triage.save_item(doc.name, frappe.as_json({"document_date": None}))
		self.assertIn("Document Date", result["item"]["missing"])
		self.assertIsNone(frappe.db.get_value("Business Document", doc.name, "document_date"))

	def test_only_known_fields_are_written(self):
		with self.set_user(self.manager("triage-fields@example.com")):
			triage.save_item(
				self.doc.name, frappe.as_json({"document_number": "RE-3", "archive_status": "Archived"})
			)
		self.assertEqual(frappe.db.get_value("Business Document", self.doc.name, "archive_status"), "Pending")

	def test_outsider_cannot_read_or_save(self):
		with self.set_user(make_outsider("triage-outsider@example.com")):
			with self.assertRaises(frappe.PermissionError):
				triage.triage_item(self.doc.name)
			with self.assertRaises(frappe.PermissionError):
				triage.save_item(self.doc.name, frappe.as_json({"document_number": "X"}))

	def test_reader_cannot_save(self):
		user = make_outsider("triage-reader@example.com")
		frappe.share.add("Business Document", self.doc.name, user, read=1)
		with self.set_user(user):
			self.assertFalse(triage.triage_item(self.doc.name)["can_write"])
			self.assertFalse(triage.triage_item(self.doc.name)["can_delete"])
			with self.assertRaises(frappe.PermissionError):
				triage.save_item(self.doc.name, frappe.as_json({"document_number": "X"}))

	def test_locked_document_cannot_change(self):
		frappe.db.set_value("Business Document", self.doc.name, "archive_started_at", now_datetime())
		self.assertTrue(triage.triage_item(self.doc.name)["locked"])
		self.assertFalse(triage.triage_item(self.doc.name)["can_delete"])
		result = triage.save_item(self.doc.name, frappe.as_json({"document_number": "RE-4"}))
		self.assertIn("error", result)

	def test_open_documents_lists_incomplete_ones(self):
		self.assertIn(self.doc.name, triage.open_documents())

	def test_stale_item_does_not_overwrite_a_newer_change(self):
		stale = triage.triage_item(self.doc.name)
		frappe.db.set_value("Business Document", self.doc.name, "document_number", "OTHER-1")
		with self.set_user(self.manager("triage-stale@example.com")):
			result = triage.save_item(
				self.doc.name, frappe.as_json({"title": "Mine"}), modified=stale["modified"]
			)
		self.assertIn("error", result)
		self.assertEqual(result["item"]["document_number"], "OTHER-1")
		self.assertNotEqual(frappe.db.get_value("Business Document", self.doc.name, "title"), "Mine")

	def test_current_item_saves_with_modified(self):
		current = triage.triage_item(self.doc.name)
		with self.set_user(self.manager("triage-current@example.com")):
			result = triage.save_item(
				self.doc.name, frappe.as_json({"document_number": "RE-9"}), modified=current["modified"]
			)
		self.assertEqual(result["item"]["document_number"], "RE-9")
		self.assertEqual(result["item"]["title"], current["title"])
