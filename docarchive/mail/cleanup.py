import frappe
from frappe.utils import add_days, now_datetime

from docarchive.mail import archiving, originals

ORIGINAL = "Mail Original"
DOCUMENT = "Business Document"
TYPE = "Business Document Type"
SAVEPOINT = "docarchive_mail_cleanup"
DEFAULT_KEEP_DAYS = 90


def run() -> dict:
	result = {"originals": 0, "documents": 0}
	keep = {row.email_account: row.keep_days for row in frappe.get_single("Archive Settings").mail_accounts}
	for account in _accounts():
		cutoff = add_days(now_datetime(), -(keep.get(account) or DEFAULT_KEEP_DAYS))
		result["originals"] += _remove(_old_originals(account, cutoff), _remove_original)
		result["documents"] += _remove(_old_documents(account, cutoff), _remove_document)
	try:
		originals.retry_pending()
	except Exception:
		frappe.log_error(title="docarchive mail cleanup")
	return result


def _accounts():
	originals = frappe.get_all(
		ORIGINAL, filters={"email_account": ("is", "set")}, pluck="email_account", distinct=True
	)
	documents = frappe.get_all(
		DOCUMENT,
		filters={
			"source_doctype": "Communication",
			"source_account": ("is", "set"),
			"archive_started_at": ("is", "not set"),
		},
		pluck="source_account",
		distinct=True,
	)
	return sorted(set(originals) | set(documents))


def _old_originals(email_account, cutoff):
	return frappe.get_all(
		ORIGINAL,
		filters={
			"email_account": email_account,
			"status": "Stored",
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
			"document_type": ("in", _types_needing_party() or [""]),
			"hold_on_archive": 0,
			"creation": ("<", cutoff),
		},
		pluck="name",
	)


def _types_needing_party():
	return frappe.get_all(TYPE, filters={"counterparty_required": 1}, pluck="name")


def _remove_original(name):
	frappe.delete_doc(ORIGINAL, name, ignore_permissions=True)


def _remove_document(name) -> bool:
	started, party, hold, doc_type = frappe.db.get_value(
		DOCUMENT, name, ["archive_started_at", "counterparty", "hold_on_archive", "document_type"]
	)
	if started or party or hold or not frappe.db.get_value(TYPE, doc_type, "counterparty_required"):
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
