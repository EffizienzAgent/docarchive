import frappe


def account_row(email_account):
	if not email_account:
		return None
	for row in frappe.get_single("Archive Settings").mail_accounts:
		if row.email_account == email_account:
			return frappe._dict(row.as_dict())
	return None


def after_rename(doc, method, old, new, merge=False):
	frappe.db.set_value("Mail Original", {"email_account": old}, "email_account", new, update_modified=False)
	frappe.db.set_value(
		"Business Document",
		{"source_account": old, "source_system": "email", "archive_started_at": ("is", "not set")},
		"source_account",
		new,
		update_modified=False,
	)


def account_settings(email_account):
	row = account_row(email_account)
	return row if row and row.mode != "Off" else None


def internal_domains(row):
	domains = {d.strip().lower() for d in (row.internal_domains or "").split(",") if d.strip()}
	if domains:
		return domains
	email_id = frappe.db.get_value("Email Account", row.email_account, "email_id") or ""
	return {email_id.rpartition("@")[2].lower()} - {""}
