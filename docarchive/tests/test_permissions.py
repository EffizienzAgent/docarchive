import frappe
from frappe.tests import IntegrationTestCase

from docarchive.permissions import MANAGER_RIGHTS
from docarchive.setup import apply_defaults
from docarchive.tests.factories import (
	make_business_document,
	make_own_company,
	make_role,
	make_settings,
	make_user,
)


class TestManagerRoles(IntegrationTestCase):
	def setUp(self):
		self.company = make_own_company().name
		self.role = make_role("Archive Test Manager")
		self.user = make_user("roles-host-manager@example.com", [self.role])

	def tearDown(self):
		frappe.set_user("Administrator")
		make_settings(default_own_company=self.company, manager_roles=[])

	def can_list(self):
		with self.set_user(self.user):
			try:
				frappe.get_list("Business Document", limit=1)
				return True
			except frappe.PermissionError:
				return False

	def test_configured_role_gets_manager_rights(self):
		self.assertFalse(self.can_list())
		make_settings(default_own_company=self.company, manager_roles=[self.role])
		self.assertTrue(self.can_list())
		for doctype, rights in MANAGER_RIGHTS.items():
			for right in rights:
				self.assertTrue(frappe.has_permission(doctype, right, user=self.user), (doctype, right))
		self.assertFalse(frappe.has_permission("Business Document", "delete", user=self.user))
		self.assertFalse(frappe.has_permission("Archive Settings", "write", user=self.user))

	def test_configured_role_can_file_documents(self):
		make_settings(default_own_company=self.company, manager_roles=[self.role])
		with self.set_user(self.user):
			doc = make_business_document(file=None)
		self.assertEqual(doc.owner, self.user)

	def test_removed_role_loses_rights(self):
		make_settings(default_own_company=self.company, manager_roles=[self.role])
		make_settings(default_own_company=self.company, manager_roles=[])
		self.assertFalse(self.can_list())
		self.assertTrue(
			frappe.has_permission(
				"Business Document", "read", user=make_user("roles-builtin@example.com", ["Document Manager"])
			)
		)

	def test_migrate_removes_roles_not_configured(self):
		make_settings(default_own_company=self.company, manager_roles=[self.role])
		stray = make_role("Archive Stray Role")
		frappe.get_doc(
			{
				"doctype": "Custom DocPerm",
				"parent": "Business Document",
				"parenttype": "DocType",
				"parentfield": "permissions",
				"role": stray,
				"read": 1,
			}
		).insert(ignore_permissions=True)
		apply_defaults()
		roles = set(frappe.get_all("Custom DocPerm", {"parent": "Business Document"}, pluck="role"))
		self.assertNotIn(stray, roles)
		self.assertIn(self.role, roles)
		self.assertTrue(self.can_list())
