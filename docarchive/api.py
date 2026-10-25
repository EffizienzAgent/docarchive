import base64
import binascii
import datetime

import frappe
from frappe import _
from frappe.core.api.file import get_max_file_size
from frappe.rate_limiter import rate_limit
from pypdf.errors import PyPdfError

from docarchive import content, settings
from docarchive.document_archive.doctype.business_document.business_document import (
	extension,
	missing_fields,
)
from docarchive.permissions import INTEGRATION_ROLE

DOCUMENT = "Business Document"
ALLOWED_ROLES = (INTEGRATION_ROLE, "System Manager")
PARTY_HINTS = ("source_party_id", "source_party_name", "source_party_email")
SAVEPOINT = "docarchive_upload"


class UploadError(frappe.ValidationError):
	http_status_code = 400


def fail(message):
	frappe.throw(message, UploadError)


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=120, seconds=60)
def upload_document(
	document_type: str | None = None,
	document_date: str | None = None,
	file_name: str | None = None,
	content_base64: str | None = None,
	document_number: str | None = None,
	title: str | None = None,
	own_company: str | None = None,
	contract_end_date: str | None = None,
	source_system: str | None = None,
	source_account: str | None = None,
	source_party_type: str | None = None,
	source_party_id: str | None = None,
	source_party_name: str | None = None,
	source_party_email: str | None = None,
	source_key: str | None = None,
	legal_hold: str | None = None,
) -> dict:
	frappe.only_for(ALLOWED_ROLES)
	doc_type = document_type_for(document_type)
	fields = {
		"document_type": doc_type.name,
		"document_date": parse_date(document_date, _("Document Date"), required=True),
		"contract_end_date": parse_date(contract_end_date, _("Contract End Date")),
		"document_number": clean(document_number),
		"title": clean(title),
		"own_company": own_company_for(own_company),
		"source_system": clean(source_system),
		"source_account": clean(source_account),
		"source_party_type": clean(source_party_type),
		"source_party_id": clean(source_party_id),
		"source_party_name": clean(source_party_name),
		"source_party_email": clean(source_party_email),
		"hold_on_archive": parse_flag(legal_hold, "legal_hold"),
	}
	check_required(doc_type, fields)
	name, data = read_upload(file_name, content_base64)
	sha = content.sha256(data)
	source_key = clean(source_key)

	scope = {"source_system": fields["source_system"], "source_account": fields["source_account"]}
	for _attempt in range(2):
		frappe.db.savepoint(SAVEPOINT)
		existing = find_existing(source_key, scope, sha, doc_type.name)
		if existing:
			return existing
		current = (source_versions(source_key, scope) or [None])[-1]
		try:
			doc = create(fields, name, data, None if current else source_key, current and current.name)
		except frappe.UniqueValidationError:
			frappe.db.rollback(save_point=SAVEPOINT)
			frappe.message_log.clear()
			continue
		return response(doc.name, "replaced" if current else "created")
	fail(_("This source_key is already used by another source."))


def find_existing(source_key, scope, sha, document_type):
	versions = source_versions(source_key, scope)
	for version in versions:
		if version.content_sha256 == sha:
			return exists(version.name)
	if source_key and not versions and frappe.db.exists(DOCUMENT, {"source_key": source_key}):
		fail(_("This source_key is already used by another source."))
	if not versions:
		same = frappe.db.get_value(
			DOCUMENT, {"content_sha256": sha, "document_type": document_type}, "name", order_by="creation"
		)
		if same:
			return exists(same)
	return None


def clean(value):
	value = (value or "").strip()
	return value or None


def document_type_for(slug):
	slug = clean(slug)
	if not slug:
		fail(_("Document type is missing."))
	name = frappe.db.get_value("Business Document Type", {"slug": slug}, "name")
	if not name:
		fail(_("Unknown document type {0}.").format(slug))
	return frappe.get_cached_doc("Business Document Type", name)


def parse_flag(value, label) -> int:
	value = (clean(str(value)) if value is not None else "") or ""
	if value.lower() in ("", "0", "false", "no"):
		return 0
	if value.lower() in ("1", "true", "yes"):
		return 1
	fail(_("{0} must be true or false.").format(label))


def parse_date(value, label, required=False):
	value = clean(value)
	if not value:
		if required:
			fail(_("{0} is missing.").format(label))
		return None
	try:
		return datetime.date.fromisoformat(value).isoformat()
	except ValueError:
		fail(_("{0} must be a date in the format YYYY-MM-DD.").format(label))


def own_company_for(value):
	value = clean(value)
	if value and frappe.db.get_value("Own Company", value, "disabled") in (None, 1):
		fail(_("Own company {0} does not exist or is disabled.").format(value))
	return value


def check_required(doc_type, fields):
	if doc_type.number_required and not fields["document_number"]:
		fail(_("{0} is missing.").format(_("Document Number")))
	if doc_type.contract_end_required and not fields["contract_end_date"]:
		fail(_("{0} is missing.").format(_("Contract End Date")))
	if doc_type.counterparty_required and not any(fields[hint] for hint in PARTY_HINTS):
		fail(
			_(
				"This document type needs a counterparty: send source_party_id, source_party_name or source_party_email."
			)
		)


def read_upload(file_name, content_base64):
	upload = frappe.request.files.get("file") if frappe.request and frappe.request.files else None
	if upload:
		name = clean(file_name) or clean(upload.filename)
		data = upload.stream.read(get_max_file_size() + 1)
	elif content_base64:
		name = clean(file_name)
		try:
			data = base64.b64decode(content_base64, validate=True)
		except binascii.Error, ValueError:
			fail(_("content_base64 is not valid Base64."))
	else:
		fail(_("No file was sent. Send it as multipart field file or as content_base64."))
	if not name or "/" in name or "\\" in name:
		fail(_("The file needs a file name without slashes."))
	allowed = settings.allowed_extensions()
	if extension(name) not in allowed:
		fail(_("Only these file types can be filed: {0}").format(", ".join(sorted(allowed))))
	if not data:
		fail(_("The file is empty."))
	if len(data) > get_max_file_size():
		fail(_("The file is larger than {0} MB.").format(get_max_file_size() // (1024 * 1024)))
	return name, data


def source_versions(source_key, scope):
	fields = ["name", "content_sha256"]
	filters = {"source_key": source_key, **{k: v or ("is", "not set") for k, v in scope.items()}}
	doc = source_key and frappe.db.get_value(DOCUMENT, filters, fields, as_dict=True)
	versions = []
	while doc:
		versions.append(doc)
		doc = frappe.db.get_value(
			DOCUMENT, {"replaces": doc.name}, fields, as_dict=True, order_by="creation desc"
		)
	return versions


def create(fields, file_name, data, source_key, replaces):
	try:
		file = frappe.get_doc(
			{"doctype": "File", "file_name": file_name, "content": data, "is_private": 1}
		).insert(ignore_permissions=True)
	except PyPdfError:
		fail(_("The file is damaged or does not match its file type."))
	doc = frappe.get_doc(
		{
			"doctype": DOCUMENT,
			**{field: value for field, value in fields.items() if value is not None},
			"file": file.file_url,
			"source_key": source_key,
			"replaces": replaces,
		}
	)
	doc.flags.archive_write = True
	return doc.insert(ignore_permissions=True)


def exists(name):
	return {"name": name, "status": "exists"}


def response(name, status):
	doc = frappe.get_doc(DOCUMENT, name)
	return {
		"name": doc.name,
		"status": status,
		"replaces": doc.replaces,
		"document_type": doc.document_type,
		"own_company": doc.own_company,
		"counterparty_type": doc.counterparty_type,
		"counterparty": doc.counterparty,
		"content_sha256": doc.content_sha256,
		"archive_status": doc.archive_status,
		"legal_hold": doc.hold_on_archive,
		"missing": missing_fields(doc),
	}
