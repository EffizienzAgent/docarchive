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
TYPE = "Business Document Type"
SAVEPOINT = "docarchive_mail_types"
LOG_TITLE = "docarchive mail types"
CORRESPONDENCE = "Correspondence"
INTERNAL_CORRESPONDENCE = "Internal Correspondence"
MAIL_TYPES = (
	{
		"type_name": CORRESPONDENCE,
		"slug": "correspondence",
		"default_retention_years": 6,
		"counterparty_required": 1,
		"is_correspondence": 1,
	},
	{
		"type_name": INTERNAL_CORRESPONDENCE,
		"slug": "internal-correspondence",
		"default_retention_years": 6,
		"counterparty_required": 0,
		"is_correspondence": 1,
	},
)


def seed_types():
	if frappe.db.count(TYPE):
		return
	for fields in DEFAULT_TYPES:
		_create(fields)
	ensure_mail_types()
	if not frappe.db.get_single_value("Archive Settings", "default_upload_type"):
		frappe.db.set_single_value("Archive Settings", "default_upload_type", DEFAULT_UPLOAD_TYPE)


def ensure_mail_types():
	for fields in MAIL_TYPES:
		try:
			frappe.db.savepoint(SAVEPOINT)
			_ensure(fields)
		except Exception:
			frappe.db.rollback(save_point=SAVEPOINT)
			frappe.log_error(title=LOG_TITLE)


def _ensure(fields):
	name, slug = fields["type_name"], fields["slug"]
	if frappe.db.exists(TYPE, name):
		frappe.db.set_value(TYPE, name, "is_correspondence", 1)
		return
	other = frappe.db.get_value(TYPE, {"slug": slug})
	if other:
		frappe.log_error(
			title=LOG_TITLE,
			message=f"Business Document Type {other} already uses the slug {slug}, so {name} was not created.",
		)
		return
	_create(fields)


def _create(fields):
	frappe.get_doc({"doctype": TYPE, "archive": 1, **fields}).insert(ignore_permissions=True)
