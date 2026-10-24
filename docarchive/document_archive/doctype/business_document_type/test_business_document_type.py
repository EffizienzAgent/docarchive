import frappe
from frappe.tests import IntegrationTestCase

from docarchive.document_types import DEFAULT_TYPES, seed_types
from docarchive.tests.factories import make_document_type


class TestBusinessDocumentType(IntegrationTestCase):
	def setUp(self):
		frappe.db.delete("Business Document Type")
		seed_types()

	def test_seeded_types(self):
		for name, slug in (
			("Outgoing Invoice", "outgoing-invoice"),
			("Incoming Invoice", "incoming-invoice"),
			("Contract", "contract"),
			("Bank Statement", "bank-statement"),
		):
			doc = frappe.get_doc("Business Document Type", name)
			self.assertEqual(doc.slug, slug)
			self.assertEqual(doc.archive, 1)
		self.assertEqual(
			frappe.db.get_value("Business Document Type", "Contract", "retention_from"), "Contract End Date"
		)
		statement = frappe.get_doc("Business Document Type", "Bank Statement")
		self.assertEqual(statement.retention_from, "Document Date")
		self.assertEqual(statement.counterparty_required, 0)
		self.assertEqual(statement.default_retention_years, 10)

	def test_seed_keeps_existing_records(self):
		frappe.db.set_value("Business Document Type", "Bank Statement", "default_retention_years", 12)
		frappe.delete_doc("Business Document Type", "Incoming Invoice", force=True)
		seed_types()
		self.assertEqual(
			frappe.db.get_value("Business Document Type", "Bank Statement", "default_retention_years"), 12
		)
		self.assertFalse(frappe.db.exists("Business Document Type", "Incoming Invoice"))
		self.assertEqual(len(DEFAULT_TYPES), 4)

	def test_type_names_are_translated(self):
		self.assertTrue(frappe.get_meta("Business Document Type").translated_doctype)

	def test_slug_must_be_path_safe(self):
		with self.assertRaises(frappe.ValidationError):
			make_document_type(slug="Not A Slug")

	def test_contract_end_required_only_with_contract_end_retention(self):
		doc = make_document_type(retention_from="Document Date", contract_end_required=1)
		self.assertEqual(doc.contract_end_required, 0)
