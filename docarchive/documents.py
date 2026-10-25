import datetime
import re

import frappe

DOCUMENT_LIMIT = 50
FILE_NAME_DATES = (
	(re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)"), "ymd"),
	(re.compile(r"(?<!\d)(\d{4})(\d{2})(\d{2})(?!\d)"), "ymd"),
	(re.compile(r"(?<!\d)(\d{1,2})\.(\d{1,2})\.(\d{4})(?!\d)"), "dmy"),
)
YEARS = range(1990, 2100)
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
	return {"document_type": last[0] if last else fallback}


def date_from_file_name(name) -> str | None:
	for pattern, order in FILE_NAME_DATES:
		for match in pattern.finditer(name or ""):
			parts = dict(zip(order, map(int, match.groups()), strict=True))
			if parts["y"] not in YEARS:
				continue
			try:
				return datetime.date(parts["y"], parts["m"], parts["d"]).isoformat()
			except ValueError:
				continue
	return None


def upload_name(file_url) -> str:
	name = frappe.db.get_value("File", {"file_url": file_url, "owner": frappe.session.user}, "file_name")
	return name or file_url.rsplit("/", 1)[-1]


@frappe.whitelist(methods=["POST"])
def create_documents(
	counterparty_type: str,
	counterparty: str,
	file_urls,
	document_type: str,
	document_date: str | None = None,
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
				"document_date": document_date or date_from_file_name(upload_name(main)),
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
