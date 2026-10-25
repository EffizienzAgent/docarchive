import frappe
from frappe.utils import today

from docarchive import extension, settings, storage_targets
from docarchive.document_archive.doctype.business_document.business_document import missing_fields

DOCUMENT = "Business Document"
DOCUMENT_LIMIT = 50
PANEL_LIMIT = 500
DOCUMENT_FIELDS = [
	"name",
	"title",
	"document_type",
	"document_number",
	"document_date",
	"archive_status",
	"legal_hold",
]
PANEL_FIELDS = [
	*DOCUMENT_FIELDS,
	"file",
	"archive_started_at",
	"retain_until",
	"counterparty",
	"contract_end_date",
]


@frappe.whitelist()
def counterparty_documents(counterparty_type: str, counterparty: str) -> list:
	frappe.has_permission(counterparty_type, "read", counterparty, throw=True)
	if not frappe.has_permission("Business Document", "read"):
		return []
	return frappe.get_list(
		"Business Document",
		filters={
			"counterparty_type": counterparty_type,
			"counterparty": counterparty,
			"archive_status": ("!=", "Deleted"),
		},
		fields=DOCUMENT_FIELDS,
		order_by="document_date desc, creation desc",
		limit=DOCUMENT_LIMIT,
	)


def archiving_on() -> bool:
	return bool(settings.get().enabled and storage_targets.configured())


@frappe.whitelist()
def counterparty_panel(counterparty_type: str, counterparty: str) -> dict:
	frappe.has_permission(counterparty_type, "read", counterparty, throw=True)
	panel = {
		"documents": [],
		"truncated": False,
		"can_create": bool(frappe.has_permission(DOCUMENT, "create")),
		"can_write": bool(frappe.has_permission(DOCUMENT, "write")),
		"archiving": archiving_on(),
		"can_extend": extension.is_manager() and bool(frappe.has_permission(DOCUMENT, "write")),
	}
	if not frappe.has_permission(DOCUMENT, "read"):
		return panel
	rows = frappe.get_list(
		DOCUMENT,
		filters={
			"counterparty_type": counterparty_type,
			"counterparty": counterparty,
			"archive_status": ("!=", "Deleted"),
		},
		fields=PANEL_FIELDS,
		order_by="document_date desc, creation desc",
		limit=PANEL_LIMIT + 1,
	)
	archived_types = set(frappe.get_all("Business Document Type", filters={"archive": 1}, pluck="name"))
	for row in rows[:PANEL_LIMIT]:
		missing = missing_fields(row)
		panel["documents"].append(
			{
				**{field: row.get(field) for field in (*DOCUMENT_FIELDS, "file", "retain_until")},
				"missing": missing,
				"eligible": panel["archiving"]
				and panel["can_write"]
				and not row.archive_started_at
				and row.document_type in archived_types
				and not missing,
			}
		)
	panel["truncated"] = len(rows) > PANEL_LIMIT
	return panel


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


def upload_rows(file_urls, document_type, document_date, documents):
	if documents:
		return frappe.parse_json(documents)
	return [
		{"file_url": url, "document_type": document_type, "document_date": document_date}
		for url in frappe.parse_json(file_urls) or []
	]


@frappe.whitelist(methods=["POST"])
def create_documents(
	counterparty_type: str,
	counterparty: str,
	file_urls=None,
	document_type: str | None = None,
	document_date: str | None = None,
	documents=None,
	combine: bool = False,
) -> list:
	frappe.has_permission(counterparty_type, "read", counterparty, throw=True)
	rows = upload_rows(file_urls, document_type, document_date, documents)
	groups = [rows] if frappe.utils.cint(combine) and rows else [[row] for row in rows]
	return [
		frappe.get_doc(
			{
				"doctype": DOCUMENT,
				"counterparty_type": counterparty_type,
				"counterparty": counterparty,
				"document_type": main.get("document_type"),
				"document_date": main.get("document_date"),
				"title": main.get("title"),
				"file": main["file_url"],
				"attachments": [{"file": row["file_url"], "title": row.get("title")} for row in rest],
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
