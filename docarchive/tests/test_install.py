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
