import frappe
from frappe.tests import IntegrationTestCase

from docarchive.permissions import MANAGER_ROLE
from docarchive.setup import apply_defaults


class TestInstall(IntegrationTestCase):
	def test_app_installed(self):
		self.assertIn("docarchive", frappe.get_installed_apps())

	def test_manager_role_created(self):
		apply_defaults()
		self.assertTrue(frappe.db.exists("Role", MANAGER_ROLE))

	def test_default_upload_type_seeded(self):
		apply_defaults()
		self.assertTrue(frappe.db.get_single_value("Archive Settings", "default_upload_type"))

	def test_mail_types_added_to_existing_install(self):
		frappe.delete_doc("Business Document Type", "Correspondence", force=True)
		apply_defaults()
		t = frappe.get_doc("Business Document Type", "Correspondence")
		self.assertEqual((t.slug, t.default_retention_years, t.counterparty_required), ("correspondence", 6, 1))
		self.assertEqual(t.is_correspondence, 1)
		internal = frappe.get_doc("Business Document Type", "Internal Correspondence")
		self.assertEqual((internal.counterparty_required, internal.is_correspondence), (0, 1))
