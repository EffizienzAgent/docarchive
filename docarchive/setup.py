import frappe

from docarchive import permissions
from docarchive.document_types import ensure_mail_types, seed_types


def apply_defaults():
	permissions.ensure_role()
	seed_types()
	ensure_mail_types()
	permissions.sync_manager_roles(permissions.roles_of(frappe.get_single("Archive Settings")))
