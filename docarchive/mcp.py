import io
import json

import frappe
from frappe.utils import add_to_date, cint, get_datetime, get_url_to_form, strip_html

from docarchive import content, counterparty, queries, settings, triage
from docarchive.document_archive.doctype.business_document.business_document import (
	CORRESPONDENCE_FIELDS,
	SOURCE_FIELDS,
	missing_fields,
)
from docarchive.mail import headers

DOCUMENT = "Business Document"
DEFAULT_LIMIT = 50
MAX_LIMIT = 200
TEXT_LIMIT = 30_000
ASSIGNABLE = (*triage.EDITABLE, "own_company", "correspondence_channel", "correspondence_direction")
PLAIN_TEXT = ("txt", "csv", "xml", "json", "md")


def available() -> bool:
	return bool(frappe.has_permission(DOCUMENT, "read"))


def url(name) -> str:
	return get_url_to_form(DOCUMENT, name)


def list_open_documents(limit=None) -> dict:
	documents = queries.documents_to_assign()
	limit = min(max(cint(limit) or DEFAULT_LIMIT, 1), MAX_LIMIT)
	return {
		"total": len(documents),
		"documents": [
			{
				**{field: doc.get(field) for field in queries.FIELDS if field != "file"},
				"missing": doc.missing,
				"url": url(doc.name),
			}
			for doc in documents[:limit]
		],
	}


def get_document(name, include_text=True) -> dict:
	doc = frappe.get_doc(DOCUMENT, name)
	doc.check_permission("read")
	return record(doc, include_text)


def record(doc, include_text=False) -> dict:
	result = {
		**triage.item(doc),
		"own_company": doc.own_company,
		"counterparty_name": doc.counterparty_name,
		**{field: doc.get(field) for field in (*CORRESPONDENCE_FIELDS, *SOURCE_FIELDS)},
		"filing_reason": doc.filing_reason,
		"archive_error": doc.archive_error,
		"einvoice": json.loads(doc.einvoice_data) if doc.einvoice_data else None,
		"archives_after": archives_after(doc),
		"url": url(doc.name),
	}
	if include_text:
		result["text"], result["text_note"] = file_text(doc)
	return result


def archives_after(doc) -> str | None:
	if doc.archive_started_at or missing_fields(doc):
		return None
	if not frappe.db.get_value("Business Document Type", doc.document_type, "archive"):
		return None
	return str(add_to_date(get_datetime(doc.modified), hours=settings.get().min_age_hours or 0))


def file_text(doc) -> tuple[str | None, str | None]:
	data, ext = content.content_for(doc)
	if data is None:
		return None, "The file is missing."
	try:
		text = extract(data, ext)
	except Exception:
		return None, "The text could not be read from the file."
	if text is None:
		return None, "Text extraction is not supported for .{0} files.".format(ext)
	text = text.strip()
	if not text:
		return None, "The file has no text layer (e.g. a scan)."
	if len(text) > TEXT_LIMIT:
		return text[:TEXT_LIMIT], "Text cut after {0} characters.".format(TEXT_LIMIT)
	return text, None


def extract(data, ext) -> str | None:
	if ext == "pdf":
		from pypdf import PdfReader

		return "\n\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages)
	if ext == "eml":
		return mail_text(data)
	if ext in PLAIN_TEXT:
		return data.decode("utf-8", errors="replace")
	return None


def mail_text(data) -> str:
	message = headers.message_from(data)
	lines = [
		f"{label}: {headers.header(message, label)}" for label in ("From", "To", "Cc", "Date", "Subject")
	]
	body = message.get_body(preferencelist=("plain", "html"))
	text = body.get_content() if body else ""
	if body and body.get_content_type() == "text/html":
		text = strip_html(text)
	attachments = [part.get_filename() or part.get_content_type() for part in message.iter_attachments()]
	if attachments:
		lines.append("Attachments: " + ", ".join(attachments))
	return "\n".join(lines) + "\n\n" + text


def document_options() -> dict:
	frappe.has_permission(DOCUMENT, "read", throw=True)
	return {
		"document_types": frappe.get_list(
			"Business Document Type",
			fields=[
				"name",
				"archive",
				"retention_from",
				"default_retention_years",
				"counterparty_required",
				"number_required",
				"contract_end_required",
				"is_correspondence",
			],
			order_by="name asc",
		),
		"own_companies": frappe.get_list("Own Company", pluck="name", order_by="name asc"),
		"default_own_company": settings.get().default_own_company,
		"counterparty_doctypes": counterparty.doctypes(),
	}


def assign_document(name, modified=None, **values) -> dict:
	doc = frappe.get_doc(DOCUMENT, name)
	doc.check_permission("write")
	if modified and modified != str(doc.modified):
		frappe.throw("Someone else changed this document in the meantime. Read it again first.")
	changes = {field: values[field] or None for field in ASSIGNABLE if values.get(field) is not None}
	if not changes:
		frappe.throw("Pass at least one field to change.")
	if changes.get("counterparty"):
		party_type = changes.get("counterparty_type") or doc.counterparty_type
		changes["counterparty"] = party_name(party_type, changes["counterparty"])
	doc.update(changes)
	doc.save()
	return record(doc)


def party_name(doctype, value) -> str:
	if not doctype:
		frappe.throw("Choose the counterparty type.")
	if frappe.db.exists(doctype, value):
		return value
	field = frappe.get_meta(doctype).get_title_field()
	matches = frappe.get_list(doctype, filters={field: value}, pluck="name", limit=2)
	if len(matches) != 1:
		frappe.throw("No single {0} is named exactly '{1}'. Pass its ID.".format(doctype, value))
	return matches[0]
