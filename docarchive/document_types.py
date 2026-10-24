import frappe

DEFAULT_TYPES = (
	{
		"type_name": "Outgoing Invoice",
		"slug": "outgoing-invoice",
		"default_retention_years": 8,
		"counterparty_required": 1,
		"number_required": 1,
	},
	{
		"type_name": "Incoming Invoice",
		"slug": "incoming-invoice",
		"default_retention_years": 8,
		"counterparty_required": 1,
	},
	{
		"type_name": "Contract",
		"slug": "contract",
		"retention_from": "Contract End Date",
		"default_retention_years": 10,
		"counterparty_required": 1,
	},
	{
		"type_name": "Bank Statement",
		"slug": "bank-statement",
		"default_retention_years": 10,
	},
)
DEFAULT_UPLOAD_TYPE = "Contract"


def seed_types():
	if frappe.db.count("Business Document Type"):
		return
	for fields in DEFAULT_TYPES:
		frappe.get_doc({"doctype": "Business Document Type", "archive": 1, **fields}).insert(
			ignore_permissions=True
		)
	if not frappe.db.get_single_value("Archive Settings", "default_upload_type"):
		frappe.db.set_single_value("Archive Settings", "default_upload_type", DEFAULT_UPLOAD_TYPE)
