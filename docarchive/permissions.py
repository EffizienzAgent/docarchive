import frappe
from frappe.permissions import setup_custom_perms

MANAGER_ROLE = "Document Manager"
BUILT_IN_ROLES = {MANAGER_ROLE, "System Manager", "Administrator"}
MANAGER_RIGHTS = {
	"Business Document": ("read", "write", "create", "report", "print", "email"),
	"Business Document Type": ("read", "report"),
	"Own Company": ("read", "report"),
	"Archive Settings": ("read",),
}


def roles_of(settings) -> set[str]:
	if not settings:
		return set()
	return {row.role for row in settings.get("manager_roles") or []} - BUILT_IN_ROLES


def manager_roles() -> set[str]:
	return {MANAGER_ROLE, *roles_of(frappe.get_cached_doc("Archive Settings"))}


def ensure_role():
	if not frappe.db.exists("Role", MANAGER_ROLE):
		frappe.get_doc({"doctype": "Role", "role_name": MANAGER_ROLE, "desk_access": 1}).insert(
			ignore_permissions=True
		)


def sync_manager_roles(roles, removed=()):
	for doctype, rights in MANAGER_RIGHTS.items():
		if roles:
			setup_custom_perms(doctype)
		for role in roles:
			grant(doctype, role, rights)
		for role in removed:
			frappe.db.delete("Custom DocPerm", {"parent": doctype, "role": role})
		frappe.clear_cache(doctype=doctype)


def grant(doctype, role, rights):
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
