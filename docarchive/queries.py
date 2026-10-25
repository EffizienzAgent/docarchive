import frappe

from docarchive.document_archive.doctype.business_document.business_document import missing_fields

FIELDS = [
	"name",
	"title",
	"document_type",
	"document_number",
	"document_date",
	"own_company",
	"einvoice_needs_review",
	"counterparty_type",
	"counterparty",
	"contract_end_date",
	"file",
	"archive_status",
	"archive_error",
]
REQUIREMENTS = (
	("counterparty_required", [["counterparty", "is", "not set"]]),
	("number_required", [["document_number", "is", "not set"]]),
	("contract_end_required", [["contract_end_date", "is", "not set"]]),
)


def incomplete_filters():
	types = frappe.get_list(
		"Business Document Type", fields=["name", *(flag for flag, _conditions in REQUIREMENTS)]
	)
	queries = [
		[["file", "is", "not set"]],
		[["document_date", "is", "not set"]],
		[["own_company", "is", "not set"]],
		[["einvoice_needs_review", "=", 1]],
		[["archive_status", "=", "Failed"]],
	]
	for flag, conditions in REQUIREMENTS:
		names = [t.name for t in types if t.get(flag)]
		if names:
			queries.append([["document_type", "in", names], *conditions])
	return queries


def documents_to_assign() -> list[frappe._dict]:
	if not frappe.has_permission("Business Document", "read"):
		return []
	documents = {}
	for filters in incomplete_filters():
		for doc in frappe.get_list(
			"Business Document",
			filters=[["archive_status", "!=", "Archived"], *filters],
			fields=FIELDS,
			limit_page_length=0,
		):
			documents[doc.name] = doc
	ordered = sorted(documents.values(), key=lambda d: (str(d.document_date or ""), d.name), reverse=True)
	for doc in ordered:
		doc.missing = missing_fields(doc)
	return ordered


@frappe.whitelist()
def count_documents_to_assign() -> int:
	return len(documents_to_assign())
