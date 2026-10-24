import frappe

from docarchive import permissions
from docarchive.document_types import seed_types


def apply_defaults():
	permissions.ensure_role()
	seed_types()
	permissions.sync_manager_roles(permissions.roles_of(frappe.get_single("Archive Settings")))
