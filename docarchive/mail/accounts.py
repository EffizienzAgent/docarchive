import frappe


def account_settings(email_account):
	if not email_account:
		return None
	for row in frappe.get_single("Archive Settings").mail_accounts:
		if row.email_account == email_account:
			return frappe._dict(row.as_dict()) if row.mode != "Off" else None
	return None


def internal_domains(row):
	domains = {d.strip().lower() for d in (row.internal_domains or "").split(",") if d.strip()}
	if domains:
		return domains
	email_id = frappe.db.get_value("Email Account", row.email_account, "email_id") or ""
	return {email_id.rpartition("@")[2].lower()} - {""}
