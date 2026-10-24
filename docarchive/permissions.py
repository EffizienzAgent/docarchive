import json

import frappe
from frappe import _
from frappe.permissions import setup_custom_perms

MANAGER_ROLE = "Document Manager"
BUILT_IN_ROLES = {MANAGER_ROLE, "System Manager", "Administrator"}
EXCLUDED_ROLES = {"Guest", "All", "Desk User"}
MANAGER_RIGHTS = {
	"Business Document": ("read", "write", "create", "report", "print", "email"),
	"Business Document Type": ("read", "report"),
	"Own Company": ("read", "report"),
}
SETTINGS = "Archive Settings"


def roles_of(settings) -> set[str]:
	if not settings:
		return set()
	return {row.role for row in settings.get("manager_roles") or []} - BUILT_IN_ROLES


def validate_roles(settings):
	for row in settings.manager_roles:
		if row.role in EXCLUDED_ROLES:
			frappe.throw(_("{0} cannot be a manager role.").format(row.role))


def manager_roles() -> set[str]:
	return {MANAGER_ROLE, *roles_of(frappe.get_cached_doc(SETTINGS))}


def ensure_role():
	if not frappe.db.exists("Role", MANAGER_ROLE):
		frappe.get_doc({"doctype": "Role", "role_name": MANAGER_ROLE, "desk_access": 1}).insert(
			ignore_permissions=True
		)


def granted() -> set[tuple[str, str]]:
	value = frappe.db.get_single_value(SETTINGS, "granted_permissions")
	return {tuple(pair) for pair in json.loads(value or "[]")}


def sync_manager_roles(roles):
	before = granted()
	now = set()
	for doctype, rights in MANAGER_RIGHTS.items():
		if roles:
			setup_custom_perms(doctype)
		for role in roles:
			if grant(doctype, role, rights) or (doctype, role) in before:
				now.add((doctype, role))
	for doctype, role in before - now:
		frappe.db.delete("Custom DocPerm", {"parent": doctype, "role": role})
	for doctype in MANAGER_RIGHTS:
		frappe.clear_cache(doctype=doctype)
	frappe.db.set_single_value(SETTINGS, "granted_permissions", json.dumps(sorted(now)))


def grant(doctype, role, rights) -> bool:
	filters = {"parent": doctype, "role": role, "permlevel": 0, "if_owner": 0}
	name = frappe.db.get_value("Custom DocPerm", filters)
	perm = (
		frappe.get_doc("Custom DocPerm", name)
		if name
		else frappe.get_doc(
			{"doctype": "Custom DocPerm", "parenttype": "DocType", "parentfield": "permissions", **filters}
		)
	)
	perm.update(dict.fromkeys(rights, 1))
	perm.save(ignore_permissions=True)
	return not name
