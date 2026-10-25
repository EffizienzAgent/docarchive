import frappe
from frappe.utils import add_days, now_datetime

from docarchive.mail import archiving

ORIGINAL = "Mail Original"
DOCUMENT = "Business Document"
SAVEPOINT = "docarchive_mail_cleanup"


def run() -> dict:
	result = {"originals": 0, "documents": 0}
	for row in frappe.get_single("Archive Settings").mail_accounts:
		if row.mode == "Off" or not row.email_account or not row.keep_days:
			continue
		cutoff = add_days(now_datetime(), -row.keep_days)
		result["originals"] += _remove(_old_originals(row.email_account, cutoff), _remove_original)
		result["documents"] += _remove(_old_documents(row.email_account, cutoff), _remove_document)
	return result


def _old_originals(email_account, cutoff):
	return frappe.get_all(
		ORIGINAL,
		filters={
			"email_account": email_account,
			"business_document": ("is", "not set"),
			"creation": ("<", cutoff),
		},
		pluck="name",
	)


def _old_documents(email_account, cutoff):
	return frappe.get_all(
		DOCUMENT,
		filters={
			"source_doctype": "Communication",
			"source_account": email_account,
			"archive_started_at": ("is", "not set"),
			"counterparty": ("is", "not set"),
			"creation": ("<", cutoff),
		},
		pluck="name",
	)


def _remove_original(name):
	frappe.delete_doc(ORIGINAL, name, ignore_permissions=True)


def _remove_document(name) -> bool:
	started, party = frappe.db.get_value(DOCUMENT, name, ["archive_started_at", "counterparty"])
	if started or party:
		return False
	archiving.remove_document(name)
	return True


def _remove(names, remover) -> int:
	removed = 0
	for name in names:
		try:
			frappe.db.savepoint(SAVEPOINT)
			removed += remover(name) is not False
		except Exception:
			frappe.db.rollback(save_point=SAVEPOINT)
			frappe.log_error(title=f"docarchive mail cleanup: {name}")
	return removed
