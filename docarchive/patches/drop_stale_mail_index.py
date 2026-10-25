import frappe

STALE = "email_account_status_ignored_creation_index"


def execute():
	if frappe.db.sql("show index from `tabMail Original` where Key_name=%s", STALE):
		frappe.db.sql_ddl(f"alter table `tabMail Original` drop index `{STALE}`")
