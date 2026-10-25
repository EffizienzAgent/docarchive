from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from docarchive.patches import add_mail_types
from docarchive.permissions import MANAGER_ROLE
from docarchive.setup import apply_defaults
from docarchive.tests.factories import (
	make_business_document,
	make_document_type,
	make_own_company,
	make_settings,
)

TYPE = "Business Document Type"


class TestInstall(IntegrationTestCase):
	def setUp(self):
		frappe.db.savepoint("test_install")
		self.addCleanup(frappe.db.rollback, save_point="test_install")

	def test_app_installed(self):
		self.assertIn("docarchive", frappe.get_installed_apps())

	def test_manager_role_created(self):
		apply_defaults()
		self.assertTrue(frappe.db.exists("Role", MANAGER_ROLE))

	def test_default_upload_type_seeded(self):
		apply_defaults()
		self.assertTrue(frappe.db.get_single_value("Archive Settings", "default_upload_type"))

	def test_mail_types_added_to_existing_install(self):
		frappe.delete_doc(TYPE, "Correspondence", force=True)
		frappe.delete_doc(TYPE, "Internal Correspondence", force=True)
		add_mail_types.execute()
		t = frappe.get_doc(TYPE, "Correspondence")
		self.assertEqual(
			(t.slug, t.default_retention_years, t.counterparty_required), ("correspondence", 6, 1)
		)
		self.assertEqual(t.is_correspondence, 1)
		internal = frappe.get_doc(TYPE, "Internal Correspondence")
		self.assertEqual((internal.counterparty_required, internal.is_correspondence), (0, 1))

	def test_deleted_mail_type_not_recreated_on_migrate(self):
		frappe.delete_doc(TYPE, "Correspondence", force=True)
		apply_defaults()
		self.assertFalse(frappe.db.exists(TYPE, "Correspondence"))

	def test_existing_type_gets_correspondence_flag(self):
		frappe.db.set_value(TYPE, "Correspondence", {"is_correspondence": 0, "default_retention_years": 7})
		add_mail_types.execute()
		self.assertEqual(
			frappe.db.get_value(TYPE, "Correspondence", ["is_correspondence", "default_retention_years"]),
			(1, 7),
		)

	def test_foreign_type_with_documents_is_left_alone(self):
		frappe.delete_doc(TYPE, "Correspondence", force=True)
		make_document_type(type_name="Correspondence", slug="letters", is_correspondence=0)
		make_settings(default_own_company=make_own_company().name)
		make_business_document(document_type="Correspondence")
		errors = frappe.db.count("Error Log")
		add_mail_types.execute()
		self.assertEqual(frappe.db.get_value(TYPE, "Correspondence", "is_correspondence"), 0)
		self.assertEqual(frappe.db.count("Error Log"), errors + 1)

	def test_foreign_type_without_documents_gets_flag(self):
		frappe.delete_doc(TYPE, "Correspondence", force=True)
		frappe.db.delete("Business Document", {"document_type": "Correspondence"})
		make_document_type(type_name="Correspondence", slug="letters", is_correspondence=0)
		add_mail_types.execute()
		self.assertEqual(frappe.db.get_value(TYPE, "Correspondence", "is_correspondence"), 1)

	def test_slug_taken_by_other_type_is_logged(self):
		frappe.delete_doc(TYPE, "Correspondence", force=True)
		make_document_type(type_name="Korrespondenz", slug="correspondence")
		errors = frappe.db.count("Error Log")
		add_mail_types.execute()
		self.assertFalse(frappe.db.exists(TYPE, "Correspondence"))
		self.assertEqual(frappe.db.count("Error Log"), errors + 1)
		self.assertTrue(frappe.db.exists(TYPE, "Internal Correspondence"))

	def test_failing_mail_type_does_not_raise(self):
		frappe.delete_doc(TYPE, "Correspondence", force=True)
		with patch("docarchive.document_types._create", side_effect=frappe.ValidationError("boom")):
			add_mail_types.execute()
		self.assertFalse(frappe.db.exists(TYPE, "Correspondence"))
