import frappe
from frappe import _


@frappe.whitelist()
def doctypes() -> list[str]:
	return list(dict.fromkeys(frappe.get_hooks("docarchive_counterparty_doctypes")))


def validate(doc):
	if not doc.counterparty:
		return
	if not doc.counterparty_type:
		frappe.throw(_("Choose the counterparty type."))
	allowed = doctypes()
	meta = frappe.get_meta(doc.counterparty_type)
	if (allowed and doc.counterparty_type not in allowed) or meta.istable or meta.issingle:
		frappe.throw(_("{0} cannot be a counterparty.").format(_(doc.counterparty_type)))
