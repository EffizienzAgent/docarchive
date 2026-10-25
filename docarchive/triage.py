import frappe
from frappe import _

from docarchive import file_names, queries
from docarchive.document_archive.doctype.business_document.business_document import (
	CONTRACT_END,
	file_name,
	file_name_of,
	missing_fields,
)

DOCUMENT = "Business Document"
EDITABLE = (
	"title",
	"document_type",
	"document_date",
	"document_number",
	"counterparty_type",
	"counterparty",
	"contract_end_date",
)
SAVEPOINT = "docarchive_triage"


def item(doc) -> dict:
	main_name = file_name_of(doc) if doc.file else None
	return {
		"name": doc.name,
		**{field: doc.get(field) for field in EDITABLE},
		"file": doc.file,
		"file_name": main_name,
		"attachments": [
			{"file": row.file, "file_name": file_name(doc, row.file), "role": row.role, "title": row.title}
			for row in doc.attachments
		],
		"suggested_date": file_names.date_from_file_name(main_name)[0] if main_name else None,
		"missing": missing_fields(doc),
		"needs_contract_end": needs_contract_end(doc.document_type),
		"archive_status": doc.archive_status,
		"retain_until": doc.retain_until,
		"locked": bool(doc.archive_started_at),
		"can_write": bool(doc.has_permission("write")),
		"can_delete": not doc.archive_started_at and bool(doc.has_permission("delete")),
		"modified": str(doc.modified),
	}


def needs_contract_end(document_type) -> bool:
	return bool(document_type) and (
		frappe.db.get_value("Business Document Type", document_type, "retention_from") == CONTRACT_END
	)


@frappe.whitelist()
def triage_item(name: str) -> dict:
	doc = frappe.get_doc(DOCUMENT, name)
	doc.check_permission("read")
	return item(doc)


@frappe.whitelist()
def document_type_rules(document_type: str) -> dict:
	frappe.has_permission("Business Document Type", "read", document_type, throw=True)
	return {"needs_contract_end": needs_contract_end(document_type)}


@frappe.whitelist()
def open_documents() -> list[str]:
	return [doc.name for doc in queries.documents_to_assign()]


def error_message(error) -> str:
	messages = [
		frappe.utils.strip_html(entry.get("message") if isinstance(entry, dict) else str(entry))
		for entry in frappe.local.message_log
	]
	frappe.local.message_log = []
	return "\n".join(dict.fromkeys(m for m in messages if m)) or str(error)


@frappe.whitelist(methods=["POST"])
def save_item(name: str, values, modified: str | None = None) -> dict:
	doc = frappe.get_doc(DOCUMENT, name)
	doc.check_permission("write")
	if modified and modified != str(doc.modified):
		return {
			"error": _(
				"Someone else changed this document in the meantime. It was reloaded, please check it again."
			),
			"item": item(doc),
		}
	values = frappe.parse_json(values) or {}
	doc.update({field: values[field] or None for field in EDITABLE if field in values})
	frappe.db.savepoint(SAVEPOINT)
	try:
		doc.save()
	except frappe.ValidationError as error:
		frappe.db.rollback(save_point=SAVEPOINT)
		return {"error": error_message(error)}
	return {"item": item(doc)}


@frappe.whitelist(methods=["POST"])
def add_attachments(name: str, file_urls) -> dict:
	doc = frappe.get_doc(DOCUMENT, name)
	doc.check_permission("write")
	for file_url in frappe.parse_json(file_urls) or []:
		doc.append("attachments", {"file": file_url})
	frappe.db.savepoint(SAVEPOINT)
	try:
		doc.save()
	except frappe.ValidationError as error:
		frappe.db.rollback(save_point=SAVEPOINT)
		return {"error": error_message(error), "item": item(frappe.get_doc(DOCUMENT, name))}
	return {"item": item(doc)}
