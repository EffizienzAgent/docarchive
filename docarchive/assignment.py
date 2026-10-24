import frappe

FIELDS = ("own_company", "counterparty_type", "counterparty")


def assign(doc) -> bool:
	before = {field: doc.get(field) for field in FIELDS}
	for method in frappe.get_hooks("docarchive_assign"):
		frappe.get_attr(method)(doc)
	if before["own_company"]:
		doc.own_company = before["own_company"]
	if before["counterparty"]:
		doc.counterparty_type = before["counterparty_type"]
		doc.counterparty = before["counterparty"]
	return any(doc.get(field) != value for field, value in before.items())
