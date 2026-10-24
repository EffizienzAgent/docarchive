import frappe
from frappe.tests import IntegrationTestCase


class TestInstall(IntegrationTestCase):
	def test_app_installed(self):
		self.assertIn("docarchive", frappe.get_installed_apps())
