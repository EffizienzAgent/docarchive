from collections import Counter

import frappe
from frappe import _
from frappe.utils import add_months, now_datetime

DOCUMENT = "Business Document"
FIELDS = [
	"name",
	"title",
	"document_type",
	"own_company",
	"counterparty_type",
	"counterparty",
	"retention_mode",
	"retain_until",
	"archive_status",
]


def execute(filters=None):
	filters = frappe._dict(filters or {})
	rows = documents(filters)
	return columns(), rows, None, chart(rows)


def conditions(filters) -> list:
	result = [] if filters.failed_copies else [["archive_status", "=", "Archived"]]
	for field in ("document_type", "own_company", "counterparty_type", "counterparty"):
		if filters.get(field):
			result.append([field, "=", filters[field]])
	if filters.lock_mode:
		result.append(["retention_mode", "like", f"%{filters.lock_mode}%"])
	if filters.ending_within_months:
		limit = add_months(now_datetime(), int(filters.ending_within_months))
		result.append(["retain_until", "<=", limit])
	return result


def documents(filters) -> list[dict]:
	rows = frappe.get_list(
		DOCUMENT, filters=conditions(filters), fields=FIELDS, order_by="retain_until asc", limit_page_length=0
	)
	failed = {}
	for copy in frappe.get_all(
		"Archive Copy",
		filters={
			"parenttype": DOCUMENT,
			"parent": ("in", [r.name for r in rows] or [""]),
			"status": "Failed",
		},
		fields=["parent", "target_name"],
	):
		failed.setdefault(copy.parent, []).append(copy.target_name)
	for row in rows:
		row.failed_copies = ", ".join(failed.get(row.name, []))
		row.retain_year = row.retain_until.year if row.retain_until else None
	return [row for row in rows if row.failed_copies] if filters.failed_copies else rows


def chart(rows):
	counts = Counter(row.retain_year for row in rows if row.retain_year)
	years = sorted(counts)
	if not years:
		return None
	return {
		"data": {
			"labels": [str(year) for year in years],
			"datasets": [{"name": _("Documents"), "values": [counts[year] for year in years]}],
		},
		"type": "bar",
	}


def columns():
	return [
		{
			"fieldname": "name",
			"label": _("Business Document"),
			"fieldtype": "Link",
			"options": DOCUMENT,
			"width": 150,
		},
		{"fieldname": "title", "label": _("Title"), "fieldtype": "Data", "width": 220},
		{
			"fieldname": "document_type",
			"label": _("Business Document Type"),
			"fieldtype": "Link",
			"options": "Business Document Type",
			"width": 150,
		},
		{
			"fieldname": "own_company",
			"label": _("Own Company"),
			"fieldtype": "Link",
			"options": "Own Company",
			"width": 140,
		},
		{
			"fieldname": "counterparty_type",
			"label": _("Counterparty Type"),
			"fieldtype": "Link",
			"options": "DocType",
			"width": 120,
		},
		{
			"fieldname": "counterparty",
			"label": _("Counterparty"),
			"fieldtype": "Dynamic Link",
			"options": "counterparty_type",
			"width": 160,
		},
		{"fieldname": "retention_mode", "label": _("Retention Mode"), "fieldtype": "Data", "width": 120},
		{
			"fieldname": "retain_until",
			"label": _("Retain Until (UTC)"),
			"fieldtype": "Datetime",
			"width": 160,
		},
		{"fieldname": "retain_year", "label": _("Year"), "fieldtype": "Int", "width": 80},
		{"fieldname": "failed_copies", "label": _("Failed Copies"), "fieldtype": "Data", "width": 140},
	]
