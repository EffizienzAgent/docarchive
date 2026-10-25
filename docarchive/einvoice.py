import json
import re

import frappe
from frappe import _
from frappe.utils import getdate

from docarchive import content, settings

XML_ROLE = "Structured Invoice (XML)"
BUILT_IN = "docarchive.einvoice_parser.extract"
EXTENSIONS = {"pdf", "xml"}
TYPE_SLUGS = {"outgoing": "outgoing-invoice", "incoming": "incoming-invoice"}
COUNTERPARTY_SIDE = {"outgoing": "buyer", "incoming": "seller"}
FIELDS = {
	"document_date": "Document Date",
	"document_number": "Document Number",
	"document_type": "Business Document Type",
	"own_company": "Own Company",
}
NOT_ID = re.compile(r"[^A-Z0-9]")


def normal_id(value) -> str:
	return NOT_ID.sub("", str(value or "").upper())


def extract_file(data, file_name):
	for method in [*reversed(frappe.get_hooks("docarchive_einvoice_extractors")), BUILT_IN]:
		try:
			result = frappe.get_attr(method)(data, file_name)
		except Exception:
			frappe.log_error(title=f"docarchive: e-invoice extractor {method}")
			continue
		if result:
			return result
	return None


def extract(files) -> dict | None:
	for file_name, data, source in files:
		_stem, dot, ext = (file_name or "").rpartition(".")
		if not data or not dot or ext.lower() not in EXTENSIONS:
			continue
		result = extract_file(data, file_name)
		if result:
			result = {**result, "source": source, "file_name": file_name}
			result["direction"] = direction(result)[0]
			return result
	return None


def own_ids() -> dict[str, str]:
	ids = {}
	for row in frappe.get_all("Own Company", filters={"disabled": 0}, fields=["name", "vat_ids"]):
		for value in (row.vat_ids or "").splitlines():
			if normal_id(value):
				ids[normal_id(value)] = row.name
	return ids


def party_ids(party) -> list[str]:
	party = party or {}
	return [normal_id(v) for v in [*party.get("vat_ids", []), *party.get("tax_numbers", [])] if normal_id(v)]


def direction(info) -> tuple[str | None, str | None]:
	ids = own_ids()
	for side, label in (("seller", "outgoing"), ("buyer", "incoming")):
		for value in party_ids(info.get(side)):
			if value in ids:
				return label, ids[value]
	return None, None


def expected(info) -> dict:
	if not info:
		return {}
	label, company = direction(info)
	slug = TYPE_SLUGS.get(label)
	document_type = slug and frappe.db.get_value("Business Document Type", {"slug": slug}, "name")
	values = {
		"document_date": info.get("issue_date"),
		"document_number": info.get("number"),
		"document_type": document_type,
		"own_company": company,
	}
	return {field: value for field, value in values.items() if value}


def counterparty_of(info) -> dict | None:
	side = COUNTERPARTY_SIDE.get(info.get("direction")) if info else None
	party = (info or {}).get(side) if side else None
	if not party or not (party.get("vat_ids") or party.get("tax_numbers") or party.get("name")):
		return None
	return {
		"vat_id": next(iter(party.get("vat_ids") or []), None),
		"tax_number": next(iter(party.get("tax_numbers") or []), None),
		"name": party.get("name"),
		"address": party.get("address"),
	}


def data_of(doc) -> dict | None:
	value = doc.get("einvoice_data")
	return json.loads(value) if value else None


def counterparty_hint(doc) -> dict | None:
	return counterparty_of(data_of(doc))


def title(doc) -> str | None:
	info = data_of(doc)
	if not info:
		return None
	party = counterparty_of(info) or info.get("seller") or {}
	label = _("Credit Note") if info.get("kind") == "credit_note" else _("Invoice")
	return " ".join(filter(None, (label, info.get("number"), party.get("name"))))


def same(field, left, right) -> bool:
	if field == "document_date":
		return getdate(left) == getdate(right)
	return str(left).strip() == str(right).strip()


def signature(doc):
	return (doc.get("content_sha256"), *(row.content_sha256 for row in doc.get("attachments") or []))


def dump(doc, info):
	doc.einvoice_data = json.dumps(info, sort_keys=True, default=str) if info else None


def update(doc, before, files):
	if doc.flags.einvoice_extract or before is None or signature(doc) != signature(before):
		info = extract(files)
		xml = info.pop("xml", None) if info else None
		dump(doc, info)
		if info:
			prefill(doc, expected(info))
			if xml and info.get("embedded_file"):
				store_xml(doc, info, xml)
	info = data_of(doc)
	if info:
		info["direction"] = direction(info)[0]
		dump(doc, info)


def prefill(doc, values):
	for field, value in values.items():
		if not doc.get(field):
			doc.set(field, value)


def record_conflicts(doc):
	info = data_of(doc)
	if not info:
		return
	info["conflicts"] = [
		{"field": field, "label": FIELDS[field], "xml": value}
		for field, value in expected(info).items()
		if doc.get(field) and not same(field, doc.get(field), value)
	]
	dump(doc, info)


def store_xml(doc, info, xml):
	if not settings.get().store_einvoice_xml or "xml" not in settings.allowed_extensions():
		return
	sha256 = content.sha256(xml)
	if sha256 == doc.content_sha256 or any(
		row.content_sha256 == sha256 for row in doc.get("attachments") or []
	):
		return
	file = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": info["embedded_file"],
			"content": xml,
			"is_private": 1,
			"attached_to_doctype": doc.doctype,
			"attached_to_name": doc.name,
			"attached_to_field": "attachments",
		}
	).insert(ignore_permissions=True)
	doc.append(
		"attachments",
		{
			"file": file.file_url,
			"role": XML_ROLE,
			"title": info["embedded_file"].rpartition(".")[0],
			"content_sha256": sha256,
		},
	)


def backfill(dry_run=False) -> dict:
	result = {"checked": 0, "found": [], "errors": []}
	for name in frappe.get_all(
		"Business Document",
		filters={"archive_started_at": ("is", "not set"), "einvoice_data": ("is", "not set")},
		pluck="name",
		order_by="creation",
	):
		result["checked"] += 1
		try:
			found = backfill_document(name, dry_run)
		except Exception as e:
			frappe.db.rollback()
			result["errors"].append(
				f"{name}: {e if isinstance(e, frappe.ValidationError) else type(e).__name__}"
			)
			continue
		if found:
			result["found"].append(found)
	return result


def backfill_document(name, dry_run) -> dict | None:
	doc = frappe.get_doc("Business Document", name)
	if dry_run:
		info = extract(doc.einvoice_files())
		if not info:
			return None
		fills = {field: value for field, value in expected(info).items() if not doc.get(field)}
		return {"name": name, "number": info.get("number"), "fills": fills}
	doc.flags.einvoice_extract = True
	doc.save(ignore_permissions=True)
	frappe.db.commit()
	info = data_of(doc)
	return {"name": name, "number": info.get("number")} if info else None
