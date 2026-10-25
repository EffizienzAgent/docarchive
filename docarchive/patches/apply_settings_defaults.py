import frappe

SETTINGS = "Archive Settings"
DEFAULTS = {"export_show_credit": 1, "overdue_hours": 48, "export_expiry_days": 7, "mail_write_timeout": 30}


def execute():
	stored = dict(
		frappe.db.sql(
			"select field, value from tabSingles where doctype=%s and field in %s",
			(SETTINGS, tuple(DEFAULTS)),
		)
	)
	for field, default in DEFAULTS.items():
		if stored.get(field) in (None, ""):
			frappe.db.set_single_value(SETTINGS, field, default, update_modified=False)
	frappe.clear_document_cache(SETTINGS)
