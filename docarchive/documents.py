import frappe
from frappe.utils import today

DOCUMENT_LIMIT = 50
DOCUMENT_FIELDS = ["name", "title", "document_type", "document_number", "document_date", "archive_status"]


@frappe.whitelist()
def counterparty_documents(counterparty_type: str, counterparty: str) -> list:
	frappe.has_permission(counterparty_type, "read", counterparty, throw=True)
	if not frappe.has_permission("Business Document", "read"):
		return []
	return frappe.get_list(
		"Business Document",
		filters={"counterparty_type": counterparty_type, "counterparty": counterparty},
		fields=DOCUMENT_FIELDS,
		order_by="document_date desc, creation desc",
		limit=DOCUMENT_LIMIT,
	)


@frappe.whitelist()
def upload_defaults() -> dict:
	if not frappe.has_permission("Business Document", "create"):
		return {}
	last = frappe.get_list(
		"Business Document",
		filters={"owner": frappe.session.user},
		pluck="document_type",
		order_by="creation desc",
		limit=1,
	)
	fallback = frappe.db.get_single_value("Archive Settings", "default_upload_type")
	return {"document_type": last[0] if last else fallback, "document_date": today()}


@frappe.whitelist(methods=["POST"])
def create_documents(
	counterparty_type: str,
	counterparty: str,
	file_urls,
	document_type: str,
	document_date: str,
	combine: bool = False,
) -> list:
	frappe.has_permission(counterparty_type, "read", counterparty, throw=True)
	file_urls = frappe.parse_json(file_urls)
	groups = [file_urls] if frappe.utils.cint(combine) and file_urls else [[url] for url in file_urls]
	return [
		frappe.get_doc(
			{
				"doctype": "Business Document",
				"counterparty_type": counterparty_type,
				"counterparty": counterparty,
				"document_type": document_type,
				"document_date": document_date,
				"file": main,
				"attachments": [{"file": url} for url in rest],
			}
		)
		.insert()
		.name
		for main, *rest in groups
	]


@frappe.whitelist(methods=["POST"])
def discard_uploads(file_urls) -> int:
	names = frappe.get_all(
		"File",
		filters={
			"file_url": ("in", frappe.parse_json(file_urls) or [""]),
			"owner": frappe.session.user,
			"attached_to_doctype": ("is", "not set"),
		},
		pluck="name",
	)
	for name in names:
		frappe.delete_doc("File", name)
	return len(names)
