import frappe
from frappe import _


@frappe.whitelist()
def doctypes() -> list[str]:
	return list(dict.fromkeys(frappe.get_hooks("docarchive_counterparty_doctypes")))


def title_of(doctype, name) -> str | None:
	if not (doctype and name):
		return None
	try:
		field = frappe.get_meta(doctype).get_title_field()
		row = frappe.db.get_value(doctype, name, ["name", field], as_dict=True)
	except Exception:
		return None
	if not row:
		return None
	return row.get(field) or row.name


def validate(doc):
	if not doc.counterparty:
		return
	if not doc.counterparty_type:
		frappe.throw(_("Choose the counterparty type."))
	allowed = doctypes()
	meta = frappe.get_meta(doc.counterparty_type)
	if (allowed and doc.counterparty_type not in allowed) or meta.istable or meta.issingle:
		frappe.throw(_("{0} cannot be a counterparty.").format(_(doc.counterparty_type)))


def check_read(doc, before=None):
	party = (doc.counterparty_type, doc.counterparty)
	if not all(party) or doc.flags.archive_write or doc.flags.ignore_permissions:
		return
	if before and (before.counterparty_type, before.counterparty) == party:
		return
	try:
		allowed = frappe.has_permission(doc.counterparty_type, "read", doc.counterparty)
	except frappe.DoesNotExistError:
		allowed = False
	if not allowed:
		frappe.throw(_("You have no access to this counterparty."), frappe.PermissionError)
