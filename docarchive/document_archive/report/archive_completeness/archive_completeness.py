from frappe import _

from docarchive import monitoring


def execute(filters=None):
	return columns(), monitoring.problems(filters)


def columns() -> list[dict]:
	return [
		{"fieldname": "problem", "label": _("Problem"), "fieldtype": "Data", "width": 130},
		{
			"fieldname": "name",
			"label": _("Business Document"),
			"fieldtype": "Link",
			"options": "Business Document",
			"width": 160,
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
			"width": 130,
		},
		{"fieldname": "document_date", "label": _("Document Date"), "fieldtype": "Date", "width": 110},
		{"fieldname": "document_number", "label": _("Document Number"), "fieldtype": "Data", "width": 160},
		{"fieldname": "since", "label": _("Since"), "fieldtype": "Datetime", "width": 150},
		{"fieldname": "details", "label": _("Details"), "fieldtype": "Data", "width": 260},
		{
			"fieldname": "mail_original",
			"label": _("Mail Original"),
			"fieldtype": "Link",
			"options": "Mail Original",
			"width": 130,
		},
	]
