import frappe
from frappe import _
from frappe.utils import getdate

DEFAULT_EXTENSIONS = "pdf\npng\njpg\njpeg\ntif\ntiff\nxml"
SETTINGS = "Archive Settings"
DEFAULTS = {"export_show_credit": 1, "overdue_hours": 48, "export_expiry_days": 7, "mail_write_timeout": 30}


def allowed_extensions() -> set[str]:
	value = frappe.db.get_single_value("Archive Settings", "allowed_extensions") or DEFAULT_EXTENSIONS
	return {ext.strip().lstrip(".").lower() for ext in value.replace(",", "\n").split("\n") if ext.strip()}


def get():
	return frappe.get_cached_doc("Archive Settings")


def stored(field):
	rows = frappe.db.sql("select value from tabSingles where doctype=%s and field=%s", (SETTINGS, field))
	return rows[0][0] if rows else None


def fill_defaults():
	values = dict(
		frappe.db.sql(
			"select field, value from tabSingles where doctype=%s and field in %s",
			(SETTINGS, tuple(DEFAULTS)),
		)
	)
	for field, default in DEFAULTS.items():
		if values.get(field) in (None, ""):
			frappe.db.set_single_value(SETTINGS, field, default, update_modified=False)
	frappe.clear_document_cache(SETTINGS)


def enabled_company(name):
	if frappe.db.get_value("Own Company", name, "disabled") in (None, 1):
		frappe.throw(_("Own company {0} does not exist or is disabled.").format(name))
	return name


def own_company_for(document_date, own_company=None) -> str | None:
	if own_company:
		return enabled_company(own_company)
	config = get()
	if (
		config.successor_own_company
		and config.successor_from_date
		and document_date
		and getdate(document_date) >= getdate(config.successor_from_date)
	):
		return config.successor_own_company
	return config.default_own_company


def retention_years(own_company, document_type) -> int | None:
	for rule in get().retention_rules:
		if rule.own_company == own_company and rule.document_type == document_type:
			return rule.years
	return frappe.db.get_value("Business Document Type", document_type, "default_retention_years") or None


def date_limits() -> frappe._dict:
	config = get()
	return frappe._dict(
		future_days=config.max_future_days if config.max_future_days is not None else 31,
		contract_years=config.max_contract_years or 50,
	)
