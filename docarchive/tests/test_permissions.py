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

	def admin_row(self, role):
		return frappe.get_doc(
			{
				"doctype": "Custom DocPerm",
				"parent": "Business Document",
				"parenttype": "DocType",
				"parentfield": "permissions",
				"role": role,
				"read": 1,
			}
		).insert(ignore_permissions=True)

	def test_sync_keeps_rows_it_did_not_create(self):
		make_settings(default_own_company=self.company, manager_roles=[self.role])
		admin = make_role("Archive Admin Set Role")
		self.admin_row(admin)
		apply_defaults()
		make_settings(default_own_company=self.company, manager_roles=[])
		apply_defaults()
		roles = set(frappe.get_all("Custom DocPerm", {"parent": "Business Document"}, pluck="role"))
		self.assertIn(admin, roles)
		self.assertNotIn(self.role, roles)

	def test_existing_admin_row_survives_role_removal(self):
		self.admin_row(self.role)
		make_settings(default_own_company=self.company, manager_roles=[self.role])
		make_settings(default_own_company=self.company, manager_roles=[])
		roles = set(frappe.get_all("Custom DocPerm", {"parent": "Business Document"}, pluck="role"))
		self.assertIn(self.role, roles)
		frappe.db.delete("Custom DocPerm", {"parent": "Business Document", "role": self.role})

	def test_broad_roles_cannot_be_manager_roles(self):
		for role in ("Guest", "All", "Desk User"):
			with self.subTest(role=role), self.assertRaises(frappe.ValidationError):
				make_settings(default_own_company=self.company, manager_roles=[role])

	def test_document_manager_cannot_read_settings(self):
		manager = make_user("roles-settings-reader@example.com", ["Document Manager"])
		self.assertFalse(frappe.has_permission("Archive Settings", "read", user=manager))
		self.assertTrue(frappe.has_permission("Business Document", "create", user=manager))

	def test_granted_list_cannot_be_forged(self):
		settings = frappe.get_single("Archive Settings")
		settings.granted_permissions = '[["Business Document", "System Manager"]]'
		settings.save(ignore_permissions=True)
		self.assertNotIn(
			"System Manager", frappe.db.get_single_value("Archive Settings", "granted_permissions") or ""
		)
